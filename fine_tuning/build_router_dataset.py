"""Build a router-only dataset from v1 intent labels plus reviewed route examples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

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

COURSE_CODE_RE = re.compile(r"\b(?:19|20|22|23|24|25)[A-Z]{2,4}\d{3,4}\b", re.I)
SEMESTER_RE = re.compile(r"\b(?:semester|sem)\s*(I{1,3}|IV|V|VI|VII|VIII|[1-8])\b", re.I)
MARKS_RE = re.compile(r"\b(\d+)\s*marks?\b", re.I)


def _extract_query(instruction: str) -> str:
    marker = "Student Question:"
    if marker not in instruction:
        return ""
    return instruction.rsplit(marker, 1)[-1].strip()


def _exam_type(query: str) -> str:
    q = query.lower()
    if re.search(r"mid\s*[- ]?term|midterm|mid\s*[- ]?sem|midsem", q):
        return "mid_term"
    if re.search(r"end\s*[- ]?sem|endsem|end semester", q):
        return "end_sem"
    return "all"


def _curriculum_aspect(query: str, intent: str) -> tuple[str, int | None]:
    q = query.lower()
    unit = re.search(r"\bunit\s*(\d+)\b", q)
    if intent == "MULTI_HOP_PREREQ" or re.search(r"prereq|before taking|what should i know before|what is required", q):
        return "prerequisites", None
    if re.search(r"\bbtl\b|bloom", q):
        return "blooms_taxonomy", None
    if re.search(r"course outcomes?|\bco\s*\d", q):
        return "course_outcomes", None
    if re.search(r"credit|ltpc|lecture.*tutorial.*practical", q):
        return "credits_ltpc", None
    if unit:
        return "unit_syllabus", int(unit.group(1))
    if re.search(r"syllabus|curriculum|\bunits?\b|topics? in|topics of", q):
        return "unit_syllabus", None
    return "general", None


def _convert_legacy(query: str, target: dict) -> tuple[str | None, dict]:
    intent = str(target.get("intent", "")).upper()
    topic = str(target.get("topic", "")).strip()
    explicit_code = COURSE_CODE_RE.search(query)

    if intent == "GENERAL":
        return None, {}

    if intent in {"PROBLEM_LIST", "SIMPLE_PYQ"}:
        args: dict = {}
        if intent == "SIMPLE_PYQ":
            course = explicit_code.group(0).upper() if explicit_code else topic
            if course:
                args["course_code_or_name"] = course
        else:
            if explicit_code:
                args["course_code_or_name"] = explicit_code.group(0).upper()
            if topic and not COURSE_CODE_RE.fullmatch(topic):
                args["topic"] = topic
        args["exam_type"] = _exam_type(query)
        mark = MARKS_RE.search(query)
        if mark:
            args["marks"] = int(mark.group(1))
        return "lookup_past_exam_questions", args

    if intent in {"MULTI_HOP_PREREQ", "SIMPLE_CURRICULUM"}:
        course = explicit_code.group(0).upper() if explicit_code else topic
        if not course:
            return None, {}
        aspect, unit_number = _curriculum_aspect(query, intent)
        args = {"course_code_or_name": course, "aspect": aspect}
        if unit_number is not None:
            args["unit_number"] = unit_number
        return "lookup_curriculum_concept", args

    # The current tool registry has no PYQ-to-CO/BTL mapping tool. Leave these
    # examples for the existing fallback router instead of teaching a false tool.
    if intent == "GRAPH_PYQ_MAPPING":
        return None, {}

    return None, {}


def _normalise_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().casefold())


def _legacy_split(query: str) -> str:
    # Stable, exact-query grouped split. Authored examples carry an explicit split.
    bucket = int(hashlib.sha256(_normalise_query(query).encode()).hexdigest()[:8], 16) % 10
    return "eval" if bucket == 0 else "train"


def _make_record(query: str, tool: str | None, arguments: dict, split: str, source: str) -> dict:
    output = {"tool": tool, "arguments": arguments}
    return {
        "task": "tool_routing",
        "instruction": SYSTEM_INSTRUCTION,
        "input": query,
        "output": json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        "query": query,
        "tool": tool,
        "arguments": arguments,
        "split": split,
        "source": source,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_v1", type=Path)
    parser.add_argument("--gold-examples", type=Path, default=Path(__file__).with_name("router_gold_examples.jsonl"))
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("campus_router_dataset_v2.jsonl"))
    args = parser.parse_args()

    records: dict[str, dict] = {}
    skipped = {"missing_query": 0, "bad_label": 0, "unmapped_intent": 0, "conflict": 0}
    with args.source_v1.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if row.get("task") != "intent_classification":
                    continue
                target = json.loads(row.get("output", ""))
                query = _extract_query(str(row.get("instruction", "")))
            except (json.JSONDecodeError, TypeError):
                skipped["bad_label"] += 1
                continue
            if not query:
                skipped["missing_query"] += 1
                continue
            intent = str(target.get("intent", "")).upper()
            if intent not in {"GENERAL", "PROBLEM_LIST", "SIMPLE_PYQ", "MULTI_HOP_PREREQ", "SIMPLE_CURRICULUM", "GRAPH_PYQ_MAPPING"}:
                skipped["unmapped_intent"] += 1
                continue
            tool, arguments = _convert_legacy(query, target)
            record = _make_record(query, tool, arguments, _legacy_split(query), f"legacy_inferred:{intent}")
            key = _normalise_query(query)
            previous = records.get(key)
            if previous and (previous["tool"], previous["arguments"]) != (tool, arguments):
                skipped["conflict"] += 1
                continue
            records[key] = record

    with args.gold_examples.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                sample = json.loads(line)
                query = str(sample["query"]).strip()
                tool = sample.get("tool")
                arguments = sample.get("arguments", {})
                split = sample.get("split", "train")
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"Invalid gold example at line {line_number}: {exc}") from exc
            if not query or not isinstance(arguments, dict) or split not in {"train", "eval"}:
                raise ValueError(f"Invalid query/arguments/split at gold example line {line_number}")
            record = _make_record(query, tool, arguments, split, "curated_seed")
            key = _normalise_query(query)
            previous = records.get(key)
            if previous and (previous["tool"], previous["arguments"]) != (tool, arguments):
                raise ValueError(f"Gold label conflicts with v1 label: {query}")
            records[key] = record

    ordered = sorted(records.values(), key=lambda row: (row["split"], row["source"], row["query"].casefold()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as target:
        for row in ordered:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts: dict[str, dict[str, int]] = {}
    for row in ordered:
        label = row["tool"] or "NO_TOOL_FALLBACK"
        counts.setdefault(row["split"], {})[label] = counts.setdefault(row["split"], {}).get(label, 0) + 1
    print(f"Wrote {len(ordered)} deduplicated routing examples to {args.output}")
    print("Counts by split/tool:")
    print(json.dumps(counts, indent=2, ensure_ascii=False))
    print("Skipped/conflicts:")
    print(json.dumps(skipped, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
