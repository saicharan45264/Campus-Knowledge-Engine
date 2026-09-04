"""
pyq_processor.py — Canonical PYQ extraction, Neo4j write, embedding, and
                   processing report.

Replaces map_pyq_structured_to_kg() and the process_pyq_background() logic
in utils.py / app.py.

Canonical Neo4j schema used here:
  (:Question)-[:BELONGS_TO]->(:Course)
  (:Question)-[:EXTRACTED_FROM]->(:Document)
  (:Question)-[:MAPPED_TO_CO]->(:CourseOutcome)

The admin-supplied course_code is ALWAYS authoritative.
The question ID is stable: md5(document_id + course_code + q_num + page_num).
"""

import os
import json
import time
import dataclasses
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'), override=True)


# ---------------------------------------------------------------------------
# Processing Report dataclass
# ---------------------------------------------------------------------------

@dataclass
class SkipReason:
    q_num:  str
    reason: str
    raw_text: str = ""


@dataclass
class PYQProcessingReport:
    document_id:          str
    filename:             str
    course_code:          str
    total_pages:          int   = 0
    candidates_detected:  int   = 0
    accepted:             int   = 0
    saved_neo4j:          int   = 0
    embedded_postgres:    int   = 0
    with_image_url:       int   = 0
    mapped_to_co:         int   = 0
    mapped_to_topic:      int   = 0
    pending_topic_review: int   = 0
    skipped:              int   = 0
    skip_reasons:         list  = field(default_factory=list)  # list[SkipReason]
    timings:              dict  = field(default_factory=dict)
    status:               str   = "pending"  # pending|processing|completed|partially_completed|failed
    created_at:           str   = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d)

    @classmethod
    def from_json(cls, s: str) -> "PYQProcessingReport":
        d = json.loads(s)
        return cls(**d)

    def finalize(self):
        """
        Set the final status based on topic mapping success:
        - failed: 0 questions saved or 0 questions mapped to topics.
        - partial: missed more than half of the total available questions in topic mapping.
        - completed: successfully mapped at least half or more of the questions to topics in Neo4j.
        """
        total = self.saved_neo4j
        if total == 0 or self.mapped_to_topic == 0:
            self.status = "failed"
        elif self.mapped_to_topic < (total / 2.0):
            self.status = "partially_completed"
        else:
            self.status = "completed"


# ---------------------------------------------------------------------------
# Neo4j write — canonical path
# ---------------------------------------------------------------------------

def save_questions_to_neo4j(
    neo4j_driver,
    structured_questions: list[dict],
    document_id: str,
    report: PYQProcessingReport
) -> None:
    """
    Writes Question nodes using only the canonical schema.
    Each call handles one document's worth of questions.
    """
    if not structured_questions:
        return

    t0 = time.time()
    now_iso = datetime.utcnow().isoformat()

    with neo4j_driver.session() as session:
        # Ensure Document node exists in Neo4j
        session.run("""
            MERGE (doc:Document {id: $doc_id})
            ON CREATE SET doc.doc_type = 'pyq',
                          doc.course_code = $c_code,
                          doc.created_at = $now
        """, doc_id=document_id, c_code=structured_questions[0]["course_code"],
             now=now_iso)

        for q in structured_questions:
            q_id    = q["id"]
            q_text  = q.get("question_text", "")
            q_num   = q.get("question_number", "")
            btl     = q.get("btl_tag")
            marks   = q.get("marks")
            has_fig = q.get("has_figure", False)
            co_tag  = q.get("co_tag")
            c_code  = q.get("course_code", "")
            img_url = q.get("image_url", "")

            try:
                # Question node + BELONGS_TO Course + EXTRACTED_FROM Document
                session.run("""
                    MERGE (q:Question {id: $q_id})
                    SET q.text             = $q_text,
                        q.question_number  = $q_num,
                        q.btl              = $btl,
                        q.marks            = $marks,
                        q.has_figure       = $has_fig,
                        q.image_url        = $img_url,
                        q.document_id      = $doc_id,
                        q.course_code      = $c_code,
                        q.extraction_method   = 'structured',
                        q.extraction_status   = 'accepted',
                        q.created_at          = $now

                    WITH q
                    MATCH (doc:Document {id: $doc_id})
                    MERGE (q)-[:EXTRACTED_FROM]->(doc)
                    WITH q
                    MATCH (c:Course {code: $c_code})
                    MERGE (q)-[:BELONGS_TO]->(c)
                """, q_id=q_id, q_text=q_text, q_num=q_num, btl=btl,
                    marks=marks, has_fig=has_fig, img_url=img_url,
                    doc_id=document_id, c_code=c_code, now=now_iso)

                report.saved_neo4j += 1

                if img_url:
                    report.with_image_url += 1

                # CO mapping
                if co_tag:
                    session.run("""
                        MATCH (q:Question {id: $q_id})
                        MERGE (co:CourseOutcome {id: $co_id})
                        ON CREATE SET co.course_code = $c_code
                        MERGE (q)-[:MAPPED_TO_CO]->(co)
                        WITH co, q
                        MATCH (c:Course {code: $c_code})
                        MERGE (co)-[:BELONGS_TO]->(c)
                    """, q_id=q_id, co_id=co_tag, c_code=c_code)
                    report.mapped_to_co += 1

            except Exception as e:
                skip = SkipReason(q_num=q_num, reason=f"Neo4j write error: {e}", raw_text=q_text[:120])
                report.skip_reasons.append(dataclasses.asdict(skip))
                report.skipped += 1
                print(f"[PYQProcessor] Failed to save Q{q_num}: {e}")

    report.timings["neo4j_s"] = round(time.time() - t0, 2)


# ---------------------------------------------------------------------------
# Convenience: build the report from an existing structured_questions list
# ---------------------------------------------------------------------------

def build_initial_report(
    document_id: str,
    filename: str,
    course_code: str,
    total_pages: int,
    candidates: list[dict],
    accepted: list[dict],
    skip_reasons: list[dict],
) -> PYQProcessingReport:
    report = PYQProcessingReport(
        document_id=document_id,
        filename=filename,
        course_code=course_code,
        total_pages=total_pages,
        candidates_detected=len(candidates),
        accepted=len(accepted),
        skipped=len(skip_reasons),
        skip_reasons=skip_reasons,
        status="processing",
    )
    return report
