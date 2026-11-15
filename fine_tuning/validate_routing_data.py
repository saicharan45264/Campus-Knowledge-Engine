"""Audit router fine-tuning data before training; does not modify the source file."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


REQUIRED_TOOLS = {
    "lookup_academic_calendar",
    "lookup_class_timetable",
    "lookup_elective_directory",
    "lookup_lab_schedule",
    "lookup_exam_schedule",
    "lookup_exam_seating",
    "lookup_curriculum_concept",
    "lookup_academic_regulations",
    "lookup_past_exam_questions",
}

TOOL_ARGUMENTS = {
    "lookup_academic_calendar": {
        "event_type": {"commencement", "midterm", "endsem", "holiday", "working_saturday", "general"},
        "term": {"odd", "even", "all"},
        "target_date": None, "target_month": None, "cohort": None,
        "temporal_scope": {"next", "all", "specific", "general"}, "festival": None,
    },
    "lookup_class_timetable": {
        "course_code": None, "weekday": {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"},
        "slot_number": None, "query_type": {"venue", "faculty", "time_slot", "day_schedule", "lunch_break", "general"},
        "department": None, "semester": None, "class_name": None,
    },
    "lookup_elective_directory": {"elective_bucket": {"PE-IV", "PE-V", "PE-VI", "free_elective", "all"}},
    "lookup_lab_schedule": {
        "lab_identifier": None, "weekday": {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"},
        "check_vacant_only": None, "department": None, "semester": None,
    },
    "lookup_exam_schedule": {
        "course_code": None, "department": None, "semester": None,
        "exam_type": {"end_sem", "mid_term"},
    },
    "lookup_exam_seating": {"register_number": None, "department": None, "semester": None},
    "lookup_curriculum_concept": {
        "course_code_or_name": None,
        "aspect": {"prerequisites", "unit_syllabus", "course_outcomes", "blooms_taxonomy", "credits_ltpc", "general"},
        "unit_number": None,
    },
    "lookup_academic_regulations": {"topic": None, "clause_number": None},
    "lookup_past_exam_questions": {
        "course_code_or_name": None, "topic": None,
        "exam_type": {"mid_term", "end_sem", "all"}, "marks": None,
    },
}

REQUIRED_ARGUMENTS = {
    "lookup_academic_calendar": {"event_type", "temporal_scope"},
    "lookup_class_timetable": {"query_type"},
    "lookup_elective_directory": {"elective_bucket"},
    "lookup_lab_schedule": {"lab_identifier"},
    "lookup_exam_schedule": {"exam_type"},
    "lookup_exam_seating": set(),
    "lookup_curriculum_concept": {"course_code_or_name", "aspect"},
    "lookup_academic_regulations": {"topic"},
    "lookup_past_exam_questions": set(),
}


def load_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: {exc.msg}")
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                errors.append(f"line {line_number}: expected a JSON object")
    return rows, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Source JSONL dataset")
    args = parser.parse_args()

    rows, errors = load_jsonl(args.dataset)
    tasks = Counter(str(row.get("task", "<missing>")) for row in rows)
    intents: Counter[str] = Counter()
    canonical_tool_counts: Counter[str] = Counter()
    curated_counts: Counter[tuple[str, str]] = Counter()
    source_counts: Counter[str] = Counter()
    no_tool_fallbacks = 0
    malformed_tool_rows = 0
    schema_errors: list[str] = []

    for row in rows:
        source_counts[str(row.get("source", "<missing>"))] += 1
        if row.get("source") == "curated_seed":
            label = row.get("tool") or "NO_TOOL"
            curated_counts[(str(row.get("split", "<missing>")), label)] += 1
        if row.get("task") == "intent_classification":
            try:
                target = json.loads(row.get("output", ""))
                intents[str(target.get("intent", "<missing>"))] += 1
            except (json.JSONDecodeError, AttributeError, TypeError):
                intents["<invalid-output>"] += 1

        # Canonical examples are needed to teach both tool selection and its
        # arguments. The uploaded v1 corpus uses a different task/output schema.
        if "tool" in row or "arguments" in row:
            tool = row.get("tool")
            arguments = row.get("arguments")
            if tool is None and arguments == {}:
                no_tool_fallbacks += 1
            elif tool in REQUIRED_TOOLS and isinstance(arguments, dict):
                canonical_tool_counts[tool] += 1
                unknown = set(arguments) - set(TOOL_ARGUMENTS[tool])
                missing = REQUIRED_ARGUMENTS[tool] - set(arguments)
                if unknown:
                    schema_errors.append(f"{row.get('query', '')!r}: unknown {tool} arguments {sorted(unknown)}")
                if missing:
                    schema_errors.append(f"{row.get('query', '')!r}: missing {tool} arguments {sorted(missing)}")
                for key, value in arguments.items():
                    allowed = TOOL_ARGUMENTS[tool][key]
                    if allowed is not None and value not in allowed:
                        schema_errors.append(f"{row.get('query', '')!r}: invalid {tool}.{key}={value!r}")
            else:
                malformed_tool_rows += 1

    print(f"File: {args.dataset}")
    print(f"Valid JSON records: {len(rows)}")
    print(f"Parse/shape errors: {len(errors)}")
    print("Task counts:")
    for task, count in sorted(tasks.items()):
        print(f"  {task}: {count}")
    print("Intent labels in intent_classification records:")
    for intent, count in sorted(intents.items()):
        print(f"  {intent}: {count}")
    print("Canonical tool-call examples (query + tool + arguments):")
    for tool in sorted(REQUIRED_TOOLS):
        print(f"  {tool}: {canonical_tool_counts[tool]}")
    print(f"  no-tool fallback: {no_tool_fallbacks}")
    print("Curated seed examples by split/route:")
    for split, label in sorted(curated_counts):
        print(f"  {split} / {label}: {curated_counts[(split, label)]}")
    print("Source counts:")
    for source, count in sorted(source_counts.items()):
        print(f"  {source}: {count}")
    if malformed_tool_rows:
        print(f"Malformed/noncanonical tool records: {malformed_tool_rows}")
    print(f"Tool argument schema errors: {len(schema_errors)}")
    for error in schema_errors[:15]:
        print(f"  {error}")
    if errors:
        print("First data errors:")
        for error in errors[:10]:
            print(f"  {error}")

    missing = sorted(tool for tool in REQUIRED_TOOLS if canonical_tool_counts[tool] == 0)
    missing_train = sorted(
        label for label in [*REQUIRED_TOOLS, "NO_TOOL"] if curated_counts[("train", label)] < 10
    )
    missing_eval = sorted(
        label for label in [*REQUIRED_TOOLS, "NO_TOOL"] if curated_counts[("eval", label)] < 5
    )
    if missing or missing_train or missing_eval or schema_errors or malformed_tool_rows:
        print("\nNOT READY: routing data is missing canonical examples, schema-valid labels, or a held-out split.")
        if missing:
            print("No canonical examples for: " + ", ".join(missing))
        if missing_train:
            print("Curated routes with fewer than 10 training examples: " + ", ".join(missing_train))
        if missing_eval:
            print("Curated routes with fewer than 5 held-out examples: " + ", ".join(missing_eval))
        return 1

    print("\nEvery registered route has a schema-valid curated training and held-out set.")
    print("The legacy inferred examples remain optional and are excluded by the trainer by default.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
