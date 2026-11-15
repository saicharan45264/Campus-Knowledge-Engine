"""training/colab_train_router.py — Standalone lightweight QLoRA fine-tuning script.

Optimized for Google Colab (T4/V100/A100) or local CUDA environments.
Trains in < 15 minutes on the M.A.C.H. AI curated academic router dataset.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("colab_router_trainer")

ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = ROOT / "campus_router_curated_v1.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "mach_router_lora_adapter"
DEFAULT_MODEL = "Qwen/Qwen2.5-3B-Instruct"

SYSTEM_INSTRUCTION = """Route the student's query to exactly one registered academic lookup tool. Invoke one tool from the list below, or make no tool call when the query is general or the current tool set cannot handle it. Extract only values stated or unambiguously implied; omit unknown optional arguments and never invent dates, course codes, student details, or facts.

Tools and arguments:
- lookup_academic_calendar: event_type (commencement, midterm, endsem, holiday, working_saturday, general), temporal_scope (next, all, specific, general), term, target_date, target_month, cohort, festival.
- lookup_class_timetable: query_type (venue, faculty, time_slot, day_schedule, lunch_break, general), course_code, weekday, slot_number, department, semester, class_name.
- lookup_elective_directory: elective_bucket (PE-IV, PE-V, PE-VI, free_elective, all).
- lookup_lab_schedule: lab_identifier, weekday, check_vacant_only, department, semester.
- lookup_exam_schedule: exam_type (end_sem, mid_term), course_code, department, semester. Use this for a course's exam date/session; use the calendar tool for semester-wide exam commencement.
- lookup_exam_seating: register_number, department, semester. For 'my seat' queries, leave register_number out; the signed-in profile supplies it.
- lookup_curriculum_concept: course_code_or_name, aspect (prerequisites, unit_syllabus, course_outcomes, blooms_taxonomy, credits_ltpc, general), unit_number.
- lookup_academic_regulations: topic, clause_number.
- lookup_past_exam_questions: course_code_or_name, topic, exam_type (mid_term, end_sem, all), marks.
For a general conversational query or a request for unsupported PYQ/CO mapping, do not call a tool so the application fallback can handle it."""


def load_dataset_splits(dataset_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train_rows, eval_rows = [], []
    with dataset_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("split") == "train":
                train_rows.append(row)
            else:
                eval_rows.append(row)
    return train_rows, eval_rows


def format_chat_data(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted = []
    for r in rows:
        query = str(r.get("query") or r.get("input") or "").strip()
        tool = r.get("tool")
        args = r.get("arguments") or {}
        assistant_content = json.dumps({"tool": tool, "arguments": args}, ensure_ascii=False)
        formatted.append({
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": query},
                {"role": "assistant", "content": assistant_content},
            ]
        })
    return formatted


def extract_json_route(text: str) -> tuple[str | None, dict[str, Any]]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            res = json.loads(match.group(0))
            if isinstance(res, dict) and ("tool" in res or "arguments" in res):
                return res.get("tool"), res.get("arguments") or {}
        except Exception:
            pass
    return None, {}


def predict_single(model, tokenizer, query_text: str) -> tuple[str | None, dict[str, Any]]:
    import torch

    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": query_text},
    ]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.inference_mode():
        out = model.generate(
            **inputs,
            max_new_tokens=96,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )

    output_text = tokenizer.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    return extract_json_route(output_text)


def evaluate(model, tokenizer, eval_rows: list[dict[str, Any]], max_samples: int = 30) -> tuple[float, float]:
    samples = eval_rows[:max_samples]
    tool_correct = 0
    exact_correct = 0

    for item in samples:
        q = item.get("query") or item.get("input")
        exp_tool = item.get("tool")
        exp_args = item.get("arguments") or {}

        p_tool, p_args = predict_single(model, tokenizer, q)
        if p_tool == exp_tool:
            tool_correct += 1
            if p_args == exp_args:
                exact_correct += 1

    tool_acc = (tool_correct / len(samples)) * 100
    exact_acc = (exact_correct / len(samples)) * 100
    return round(tool_acc, 2), round(exact_acc, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    args = parser.parse_args()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for QLoRA fine-tuning. Make sure you are using a GPU runtime.")

    logger.info("Target GPU: %s (%s)", torch.cuda.get_device_name(0), args.model_id)

    train_rows, eval_rows = load_dataset_splits(args.dataset)
    logger.info("Loaded %d train and %d held-out eval examples.", len(train_rows), len(eval_rows))

    train_data = format_chat_data(train_rows)
    eval_data = format_chat_data(eval_rows)

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.float16,
    )

    tokenizer = AutoTokenizer.from_pretrained(args.model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model = AutoModelForCausalLM.from_pretrained(
        args.model_id,
        quantization_config=bnb_config,
        torch_dtype=torch.float16,
        device_map="auto",
        trust_remote_code=True,
    )

    logger.info("Running baseline evaluation on base model...")
    base_tool_acc, base_exact_acc = evaluate(model, tokenizer, eval_rows, max_samples=25)
    logger.info("Base Model -> Tool Accuracy: %s%% | Exact Match: %s%%", base_tool_acc, base_exact_acc)

    model = prepare_model_for_kbit_training(model)

    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )

    training_args = SFTConfig(
        output_dir=str(args.output_dir / "checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        logging_steps=10,
        save_strategy="no",
        fp16=False,
        bf16=False,
        optim="paged_adamw_8bit",
        max_length=512,
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=Dataset.from_list(train_data),
        peft_config=peft_config,
        processing_class=tokenizer,
    )

    logger.info("Starting QLoRA training for %d epochs...", args.epochs)
    start_t = time.time()
    trainer.train()
    duration_min = round((time.time() - start_t) / 60, 2)
    logger.info("Training completed in %s minutes.", duration_min)

    logger.info("Evaluating fine-tuned model...")
    tuned_tool_acc, tuned_exact_acc = evaluate(trainer.model, tokenizer, eval_rows, max_samples=25)
    logger.info("Fine-Tuned Model -> Tool Accuracy: %s%% | Exact Match: %s%%", tuned_tool_acc, tuned_exact_acc)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))

    manifest = {
        "base_model": args.model_id,
        "epochs": args.epochs,
        "train_samples": len(train_rows),
        "eval_samples": len(eval_rows),
        "duration_minutes": duration_min,
        "base_tool_accuracy": base_tool_acc,
        "fine_tuned_tool_accuracy": tuned_tool_acc,
        "base_exact_accuracy": base_exact_acc,
        "fine_tuned_exact_accuracy": tuned_exact_acc,
    }
    (args.output_dir / "training_manifest.json").write_text(json.dumps(manifest, indent=2))
    shutil.make_archive(str(args.output_dir), "zip", root_dir=str(args.output_dir))
    logger.info("Adapter saved and zipped to %s.zip", args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
