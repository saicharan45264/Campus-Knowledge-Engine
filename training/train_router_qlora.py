"""QLoRA fine-tuning for the academic tool router; runs on free Colab/Kaggle T4 GPU."""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from build_router_dataset import SYSTEM_INSTRUCTION
from validate_routing_data import REQUIRED_TOOLS

ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = ROOT / "academic_tool_schemas.json"
DEFAULT_MODEL = "Qwen/Qwen2.5-3B-Instruct"
SEED = 73021


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Line {line_number} is not a JSON object")
            rows.append(row)
    return rows


def load_schemas() -> list[dict[str, Any]]:
    schemas = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    names = {item["function"]["name"] for item in schemas}
    if names != REQUIRED_TOOLS:
        raise ValueError(
            "Tool schema file is out of sync with the router registry: "
            f"missing={sorted(REQUIRED_TOOLS - names)}, extra={sorted(names - REQUIRED_TOOLS)}"
        )
    return schemas


def prepare_splits(
    rows: list[dict[str, Any]], include_legacy: bool, max_legacy_per_label: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    curated = [row for row in rows if row.get("source") == "curated_seed"]
    train = [row for row in curated if row.get("split") == "train"]
    evaluation = [row for row in curated if row.get("split") == "eval"]

    if include_legacy:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if str(row.get("source", "")).startswith("legacy_inferred:") and row.get("split") == "train":
                label = row.get("tool") or "NO_TOOL"
                grouped[label].append(row)
        rng = random.Random(SEED)
        for label, candidates in grouped.items():
            rng.shuffle(candidates)
            train.extend(candidates[:max_legacy_per_label])

    expected_labels = REQUIRED_TOOLS | {None}
    train_counts = Counter(row.get("tool") for row in train)
    eval_counts = Counter(row.get("tool") for row in evaluation)
    missing_train = [label or "NO_TOOL" for label in expected_labels if train_counts[label] < 10]
    missing_eval = [label or "NO_TOOL" for label in expected_labels if eval_counts[label] < 5]
    if missing_train or missing_eval:
        raise ValueError(
            "Insufficient curated examples. Need at least 10 train and 5 held-out eval "
            f"examples per route. Missing train={sorted(missing_train)}, eval={sorted(missing_eval)}"
        )
    return train, evaluation


def _messages(row: dict[str, Any]) -> list[dict]:
    """Build a plain conversational message list for Qwen training.
    Assistant response is plain JSON text so the tokenizer chat template works without tool schemas.
    """
    query = str(row.get("query") or row.get("input") or "").strip()
    if not query:
        raise ValueError("A training row has an empty query")
    tool = row.get("tool")
    arguments = row.get("arguments")
    if not isinstance(arguments, dict):
        raise ValueError(f"Arguments must be an object for query {query!r}")

    messages = [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "user", "content": query},
    ]
    if tool is None:
        messages.append({"role": "assistant", "content": '{"tool":null,"arguments":{}}'})
    elif tool in REQUIRED_TOOLS:
        messages.append({
            "role": "assistant",
            "content": json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False),
        })
    else:
        raise ValueError(f"Unknown tool {tool!r} for query {query!r}")
    return messages


def _first_json_object(text: str) -> dict[str, Any] | None:
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        quoted = False
        escaped = False
        for end in range(start, len(text)):
            current = text[end]
            if quoted:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    quoted = False
            else:
                if current == '"':
                    quoted = True
                elif current == "{":
                    depth += 1
                elif current == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            value = json.loads(text[start : end + 1])
                            return value if isinstance(value, dict) else None
                        except json.JSONDecodeError:
                            break
    return None


def parse_router_output(text: str, known_tools: set[str]) -> tuple[str | None, dict[str, Any], bool]:
    """Parse either JSON route output or a serialized native function call."""
    obj = _first_json_object(text)
    if obj is not None:
        if "tool" in obj:
            tool = obj.get("tool")
            args = obj.get("arguments") or {}
            if (tool is None or tool in known_tools) and isinstance(args, dict):
                return tool, args, True

        name = obj.get("name")
        args = obj.get("arguments") or {}
        if name in known_tools and isinstance(args, dict):
            return name, args, True

        calls = obj.get("tool_calls")
        if isinstance(calls, list) and len(calls) == 1:
            call = calls[0].get("function", {}) if isinstance(calls[0], dict) else {}
            name = call.get("name")
            args = call.get("arguments") or {}
            if name in known_tools and isinstance(args, dict):
                return name, args, True

    for name in sorted(known_tools, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(name)}\b", text)
        if not match:
            continue
        args = _first_json_object(text[match.end() :])
        if args is not None:
            if set(args) == {"arguments"} and isinstance(args["arguments"], dict):
                args = args["arguments"]
            return name, args, True
    return None, {}, bool(text.strip())


def score_predictions(expected_rows: list[dict[str, Any]], predictions: list[str]) -> dict[str, Any]:
    labels = sorted(REQUIRED_TOOLS) + ["NO_TOOL"]
    counts = Counter()
    exact_args = Counter()
    valid = Counter()
    by_label: dict[str, Counter] = defaultdict(Counter)
    for row, generated in zip(expected_rows, predictions):
        expected_tool = row.get("tool")
        expected_args = row.get("arguments") or {}
        predicted_tool, predicted_args, parsed = parse_router_output(generated, REQUIRED_TOOLS)
        label = expected_tool or "NO_TOOL"
        matched = predicted_tool == expected_tool
        counts["total"] += 1
        counts["tool_correct"] += int(matched)
        counts["arguments_exact"] += int(matched and predicted_args == expected_args)
        counts["valid_output"] += int(parsed)
        by_label[label]["total"] += 1
        by_label[label]["tool_correct"] += int(matched)
        by_label[label]["arguments_exact"] += int(matched and predicted_args == expected_args)
        by_label[label]["valid_output"] += int(parsed)

    def rates(counter: Counter) -> dict[str, float | int]:
        total = counter["total"]
        return {
            **counter,
            "tool_accuracy": round(counter["tool_correct"] / total, 4) if total else 0.0,
            "exact_tool_and_arguments": round(counter["arguments_exact"] / total, 4) if total else 0.0,
            "valid_output_rate": round(counter["valid_output"] / total, 4) if total else 0.0,
        }

    return {"overall": rates(counts), "by_expected_route": {key: rates(by_label[key]) for key in labels if by_label[key]["total"]}}


def generate_predictions(model: Any, tokenizer: Any, rows: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> list[str]:
    import torch

    model.eval()
    predictions = []
    for index, row in enumerate(rows, 1):
        query = str(row.get("query") or row.get("input") or "").strip()
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": query},
        ]
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        prompt_length = inputs["input_ids"].shape[-1]
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=192, do_sample=False)
        predictions.append(tokenizer.decode(generated[0][prompt_length:], skip_special_tokens=True))
        if index % 10 == 0:
            print(f"  evaluated {index}/{len(rows)} held-out prompts")
    model.train()
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "campus_router_dataset_v2.jsonl")
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "router_output")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--max-legacy-per-label", type=int, default=40)
    parser.add_argument("--include-legacy", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = read_rows(args.dataset)
    schemas = load_schemas()
    train_rows, eval_rows = prepare_splits(rows, args.include_legacy, args.max_legacy_per_label)
    print(f"Dataset: {args.dataset}")
    print(f"Base model: {args.model_id}")
    print(f"Training rows: {len(train_rows)}; held-out curated rows: {len(eval_rows)}")
    print("Training routes:", dict(Counter(row.get("tool") or "NO_TOOL" for row in train_rows)))
    print("Held-out routes:", dict(Counter(row.get("tool") or "NO_TOOL" for row in eval_rows)))
    if args.dry_run:
        return 0

    import torch
    from datasets import Dataset
    from peft import LoraConfig, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    has_cuda = torch.cuda.is_available()
    if has_cuda:
        print("🚀 GPU detected: Training with 4-bit QLoRA on CUDA.")
        dtype = torch.float16  # FP16 for T4 GPU
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_quant_storage=dtype,
        )
        model = AutoModelForCausalLM.from_pretrained(
            args.model_id,
            quantization_config=quant_config,
            torch_dtype=dtype,
            device_map={"": torch.cuda.current_device()},
            low_cpu_mem_usage=True,
            trust_remote_code=True,
        )
    else:
        print("💻 No GPU detected: Running on CPU with float32 LoRA.")
        dtype = torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            args.model_id,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
            trust_remote_code=True,
        )

    tokenizer = AutoTokenizer.from_pretrained(args.model_id, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model.config.use_cache = False

    print("Running base-model generation evaluation on the curated held-out split...")
    base_predictions = generate_predictions(model, tokenizer, eval_rows, schemas)
    base_metrics = score_predictions(eval_rows, base_predictions)
    print("Base model exact tool accuracy:", base_metrics["overall"]["tool_accuracy"])

    model.train()
    if has_cuda:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
        # Cast any stray BF16 trainable params → FP16 for T4 AMP scaler compatibility
        for _n, _p in model.named_parameters():
            if _p.requires_grad and _p.dtype == torch.bfloat16:
                _p.data = _p.data.to(torch.float16)

    train_data = [{"messages": _messages(row)} for row in train_rows]
    eval_data  = [{"messages": _messages(row)} for row in eval_rows]
    train_ds = Dataset.from_list(train_data)
    eval_ds  = Dataset.from_list(eval_data)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    lora_config = LoraConfig(
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )
    warmup_kw = {"warmup_ratio": 0.05} if "warmup_ratio" in getattr(SFTConfig, "__dataclass_fields__", {}) else {"warmup_steps": 0.05}
    train_config = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=1 if has_cuda else 2,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=8 if has_cuda else 4,
        learning_rate=1e-4 if has_cuda else 2e-4,
        **warmup_kw,
        lr_scheduler_type="cosine",
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        max_length=min(args.max_length, 512) if not has_cuda else args.max_length,
        assistant_only_loss=False,
        gradient_checkpointing=has_cuda,
        gradient_checkpointing_kwargs={"use_reentrant": False} if has_cuda else None,
        fp16=has_cuda,
        bf16=False,
        optim="paged_adamw_8bit" if has_cuda else "adamw_torch",
        report_to="none",
        seed=SEED,
        data_seed=SEED,
    )
    trainer = SFTTrainer(
        model=model,
        args=train_config,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=tokenizer,
        peft_config=lora_config,
    )
    trainer.train()

    print("Running fine-tuned generation evaluation on the same held-out split...")
    tuned_predictions = generate_predictions(trainer.model, tokenizer, eval_rows, schemas)
    tuned_metrics = score_predictions(eval_rows, tuned_predictions)
    trainer.save_model(str(args.output_dir / "adapter"))
    tokenizer.save_pretrained(args.output_dir / "adapter")

    report = {
        "base_model": args.model_id,
        "adapter_dir": str(args.output_dir / "adapter"),
        "epochs": args.epochs,
        "training_examples": len(train_rows),
        "held_out_examples": len(eval_rows),
        "base_metrics": base_metrics,
        "fine_tuned_metrics": tuned_metrics,
    }
    (args.output_dir / "training_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    with (args.output_dir / "held_out_predictions.jsonl").open("w", encoding="utf-8") as output:
        for row, base_text, tuned_text in zip(eval_rows, base_predictions, tuned_predictions):
            base_tool, base_arguments, _ = parse_router_output(base_text, REQUIRED_TOOLS)
            tuned_tool, tuned_arguments, _ = parse_router_output(tuned_text, REQUIRED_TOOLS)
            output.write(json.dumps({
                "query": row.get("query") or row.get("input"),
                "expected": {"tool": row.get("tool"), "arguments": row.get("arguments") or {}},
                "base_prediction": {"tool": base_tool, "arguments": base_arguments},
                "fine_tuned_prediction": {"tool": tuned_tool, "arguments": tuned_arguments},
            }, ensure_ascii=False) + "\n")

    print("Base exact tool accuracy:", base_metrics["overall"]["tool_accuracy"])
    print("Fine-tuned exact tool accuracy:", tuned_metrics["overall"]["tool_accuracy"])
    print(f"Saved adapter and manifest in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
