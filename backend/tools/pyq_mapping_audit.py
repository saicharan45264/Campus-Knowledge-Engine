"""Export PYQ topic mappings for human review and score reviewed labels.

Export:
    python backend/tools/pyq_mapping_audit.py export --course-code 23CSE213 --output pyq-audit.csv

After a reviewer fills ``gold_topic_ids`` with a JSON array per question:
    python backend/tools/pyq_mapping_audit.py evaluate --input pyq-audit.csv

Gold labels should use Neo4j topic IDs, not topic names, so duplicate or
similarly named topics cannot be mistaken for one another.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import get_neo4j  # noqa: E402


def export_mappings(course_code: str, output_path: Path) -> int:
    driver = get_neo4j()
    try:
        with driver.session() as session:
            rows = session.run(
                """
                MATCH (q:Question {course_code: $course_code})
                OPTIONAL MATCH (q)-[r:TESTS_TOPIC]->(t:Topic)
                WITH q, collect(DISTINCT CASE WHEN t IS NULL THEN NULL ELSE {
                    topic_id: t.topic_id,
                    topic_name: t.name,
                    confidence: r.confidence,
                    semantic_score: r.semantic_score,
                    keyword_score: r.keyword_score,
                    mapping_method: r.mapping_method
                } END) AS mappings
                OPTIONAL MATCH (q)-[s:SUGGESTED_TOPIC]->(st:Topic)
                RETURN q.id AS question_id,
                       q.document_id AS document_id,
                       q.question_number AS question_number,
                       q.subpart_label AS subpart,
                       q.page_number AS page,
                       q.text AS question_text,
                       q.mapping_status AS mapping_status,
                       q.mapping_reason AS mapping_reason,
                       q.mapper_version AS mapper_version,
                       q.mapping_candidates AS candidates,
                       mappings,
                       collect(DISTINCT CASE WHEN st IS NULL THEN NULL ELSE {
                           topic_id: st.topic_id,
                           topic_name: st.name,
                           confidence: s.confidence,
                           semantic_score: s.semantic_score,
                           keyword_score: s.keyword_score,
                           retrieval_score: s.retrieval_score,
                           mapping_method: s.mapping_method
                       } END) AS suggestions
                ORDER BY document_id, page, question_number, subpart
                """,
                course_code=course_code.strip().upper(),
            ).data()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "course_code", "question_id", "document_id", "question_number", "subpart", "page",
            "question_text", "mapping_status", "mapping_reason", "mapper_version",
            "predicted_topic_ids", "predicted_topics", "candidate_rankings",
            "suggested_topic_ids", "suggested_topics",
            "gold_topic_ids", "audit_status", "reviewer_notes",
        ]
        with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                mappings = [mapping for mapping in (row.get("mappings") or []) if mapping]
                suggestions = [item for item in (row.get("suggestions") or []) if item]
                candidates = row.get("candidates") or "[]"
                try:
                    candidates = json.loads(candidates) if isinstance(candidates, str) else candidates
                except json.JSONDecodeError:
                    candidates = []
                writer.writerow({
                    "course_code": course_code.strip().upper(),
                    "question_id": row.get("question_id") or "",
                    "document_id": row.get("document_id") or "",
                    "question_number": row.get("question_number") or "",
                    "subpart": row.get("subpart") or "",
                    "page": row.get("page") or "",
                    "question_text": row.get("question_text") or "",
                    "mapping_status": row.get("mapping_status") or "unmapped",
                    "mapping_reason": row.get("mapping_reason") or "",
                    "mapper_version": row.get("mapper_version") or "",
                    "predicted_topic_ids": json.dumps(
                        [mapping.get("topic_id") for mapping in mappings if mapping.get("topic_id")],
                        ensure_ascii=False,
                    ),
                    "predicted_topics": json.dumps(mappings, ensure_ascii=False),
                    "candidate_rankings": json.dumps(candidates, ensure_ascii=False),
                    "suggested_topic_ids": json.dumps(
                        [item.get("topic_id") for item in suggestions if item.get("topic_id")],
                        ensure_ascii=False,
                    ),
                    "suggested_topics": json.dumps(suggestions, ensure_ascii=False),
                    "gold_topic_ids": "",
                    "audit_status": "",
                    "reviewer_notes": "",
                })
        print(f"Exported {len(rows)} question records to {output_path}")
        return len(rows)
    finally:
        driver.close()


def evaluate_labels(input_path: Path) -> None:
    total = mapped = annotated = exact = true_positive = predicted_links = gold_links = 0
    with input_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            total += 1
            try:
                predicted = set(json.loads(row.get("predicted_topic_ids") or "[]"))
                raw_gold = row.get("gold_topic_ids") or ""
                if not raw_gold.strip():
                    continue
                gold = set(json.loads(raw_gold))
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError(
                    f"Invalid JSON topic ID list for question {row.get('question_id')}: {exc}"
                ) from exc
            annotated += 1
            mapped += bool(predicted)
            predicted_links += len(predicted)
            gold_links += len(gold)
            overlap = predicted & gold
            true_positive += len(overlap)
            exact += predicted == gold

    precision = true_positive / predicted_links if predicted_links else 0.0
    recall = true_positive / gold_links if gold_links else 0.0
    exact_match = exact / annotated if annotated else 0.0
    coverage = mapped / annotated if annotated else 0.0
    print(f"Annotated questions: {annotated}/{total}")
    print(f"Mapped-question coverage on annotated set: {coverage:.1%}")
    print(f"Topic-link precision: {precision:.1%} ({true_positive}/{predicted_links})")
    print(f"Topic-link recall: {recall:.1%} ({true_positive}/{gold_links})")
    print(f"Exact topic-set match: {exact_match:.1%} ({exact}/{annotated})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export graph mappings for review")
    export_parser.add_argument("--course-code", required=True)
    export_parser.add_argument("--output", type=Path, required=True)

    evaluate_parser = subparsers.add_parser("evaluate", help="Score reviewer-provided gold topic IDs")
    evaluate_parser.add_argument("--input", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "export":
        export_mappings(args.course_code, args.output)
    else:
        evaluate_labels(args.input)


if __name__ == "__main__":
    main()
