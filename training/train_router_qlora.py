"""QLoRA fine-tuning for the academic tool router; intended for a Kaggle GPU."""

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
DEFAULT_MODEL = "google/gemma-4-12B-it"
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


def _messages(row: dict[str, Any], schemas: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
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
            "tool_calls": [{
                "type": "function",
                "function": {"name": tool, "arguments": arguments},
            }],
        })
    else:
        raise ValueError(f"Unknown tool {tool!r} for query {query!r}")
    return messages, schemas


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
            # Some serializers wrap function arguments as {"arguments": {...}}.
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


def generate_predictions(model: Any, processor: Any, rows: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> list[str]:
    import torch

    model.eval()
    predictions = []
    for index, row in enumerate(rows, 1):
        query = str(row.get("query") or row.get("input") or "").strip()
        inputs = processor.apply_chat_template(
            [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": query},
            ],
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
            add_generation_prompt=True,
            tools=schemas,
            enable_thinking=False,
        ).to(model.device)
        prompt_length = inputs["input_ids"].shape[-1]
        with torch.inference_mode():
            generated = model.generate(**inputs, max_new_tokens=192, do_sample=False)
        predictions.append(processor.tokenizer.decode(generated[0][prompt_length:], skip_special_tokens=False))
        if index % 10 == 0:
            print(f"  evaluated {index}/{len(rows)} held-out prompts")
    model.train()
    return predictions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "campus_router_dataset_v2.jsonl")
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "kaggle_output")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--max-legacy-per-label", type=int, default=40)
    parser.add_argument("--include-legacy", action="store_true", help="Add capped v1-derived training examples; not recommended until manually audited.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report the curated split without importing GPU libraries.")
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
    from transformers import AutoModelForMultimodalLM, AutoProcessor, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise RuntimeError("No CUDA GPU found. Run this script in a Kaggle GPU notebook.")
    if not hasattr(__import__("transformers"), "AutoModelForMultimodalLM"):
        raise RuntimeError("Gemma 4 training requires a recent Transformers release (5.10.1+).")

    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=dtype,
        bnb_4bit_quant_storage=dtype,
    )
    processor = AutoProcessor.from_pretrained(args.model_id)
    model = AutoModelForMultimodalLM.from_pretrained(
        args.model_id,
        quantization_config=quant_config,
        dtype=dtype,
        device_map={"": torch.cuda.current_device()},
        low_cpu_mem_usage=True,
    )
    model.config.use_cache = False
    print("Running base-model generation evaluation on the curated held-out split…")
    base_predictions = generate_predictions(model, processor, eval_rows, schemas)
    base_metrics = score_predictions(eval_rows, base_predictions)
    print("Base model exact tool accuracy:", base_metrics["overall"]["tool_accuracy"])
    model.train()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    train_data = []
    for row in train_rows:
        messages, row_schemas = _messages(row, schemas)
        train_data.append({"messages": messages, "tools": row_schemas})
    eval_data = []
    for row in eval_rows:
        messages, row_schemas = _messages(row, schemas)
        eval_data.append({"messages": messages, "tools": row_schemas})
    train_ds = Dataset.from_list(train_data, on_mixed_types="use_json")
    eval_ds = Dataset.from_list(eval_data, on_mixed_types="use_json")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    lora_config = LoraConfig(
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    train_config = SFTConfig(
        output_dir=str(args.output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=1e-4,
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
        logging_steps=5,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        max_length=args.max_length,
        assistant_only_loss=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        fp16=dtype == torch.float16,
        bf16=dtype == torch.bfloat16,
        optim="paged_adamw_8bit",
        report_to="none",
        seed=SEED,
        data_seed=SEED,
    )
    trainer = SFTTrainer(
        model=model,
        args=train_config,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        processing_class=processor.tokenizer,
        peft_config=lora_config,
    )
    trainer.train()
    print("Running fine-tuned generation evaluation on the same held-out split…")
    tuned_predictions = generate_predictions(trainer.model, processor, eval_rows, schemas)
    tuned_metrics = score_predictions(eval_rows, tuned_predictions)
    trainer.save_model(str(args.output_dir / "adapter"))
    processor.save_pretrained(args.output_dir / "adapter")

    report = {
        "base_model": args.model_id,
        "adapter_dir": str(args.output_dir / "adapter"),
        "epochs": args.epochs,
        "training_examples": len(train_rows),
        "held_out_examples": len(eval_rows),
        "training_sources": dict(Counter(row.get("source", "") for row in train_rows)),
        "held_out_source": "curated_seed only",
        "base_metrics": base_metrics,
        "fine_tuned_metrics": tuned_metrics,
        "note": "This script saves an adapter. The adapter is not wired into the app's Ollama endpoint.",
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
                "base_raw_output": base_text,
                "fine_tuned_raw_output": tuned_text,
            }, ensure_ascii=False) + "\n")
    print("Base exact tool/argument accuracy:", base_metrics["overall"]["exact_tool_and_arguments"])
    print("Fine-tuned exact tool/argument accuracy:", tuned_metrics["overall"]["exact_tool_and_arguments"])
    print(f"Saved adapter and manifest in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
