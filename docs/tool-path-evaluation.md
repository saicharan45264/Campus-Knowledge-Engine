# Academic tool path evaluation

Evaluation date: 2026-11-11  
Scope: model-selected academic tools, deterministic backend retrieval, and response rendering for the document types currently represented in the local database.

## Summary

All nine registered tool routes were exercised with representative natural-language questions. The model selected the intended route after one prompt adjustment for personal exam seating, and the backend reached the relevant data source for each route. The current tool layer is ready to serve as the baseline for refining the fine-tuning dataset, with two explicit corpus gaps: there are no indexed previous-year question records, and Bloom-level mappings are absent.

## Route results

| Document / data type | Tool path | Evaluation result |
| --- | --- | --- |
| Academic calendar | `lookup_academic_calendar` | Pass. Holiday-list query returned 27 holidays. Semester 5 midterm commencement resolved to the calendar milestone (17 August 2026). Month query for October returned six academic events and excluded timetable overrides. |
| Class timetable | `lookup_class_timetable` | Pass. CSE Semester VII-F Friday query returned the matching weekly schedule. |
| Electives | `lookup_elective_directory` | Pass. PE-VI query returned seven offerings. |
| Lab timetable | `lookup_lab_schedule` | Pass. CSE Semester IV Wednesday query reached the lab schedule records. |
| Exam schedule | `lookup_exam_schedule` | Pass. CSE Semester VI end-sem query for 23CSE312 returned its scheduled examination record. Semester-wide commencement requests route to the academic calendar. |
| Seating arrangements | `lookup_exam_seating` | Pass. Route uses the authenticated student's profile when the query does not provide a roll number. A synthetic profile was used for handler verification; student PII was not included in this report. The seating dataset contains 82 records. |
| Curriculum | `lookup_curriculum_concept` | Pass for outcomes, unit topics, prerequisites, and credits. Structured course/aspect/unit arguments now remain separate. Live graph lookups returned 4 outcomes for 23CSE401, unit topics, 2 prerequisite links for 23CSE203, and 3 credits / 2-0-2 for 23CSE401. |
| Regulations | `lookup_academic_regulations` | Pass. The regulations store contains 90 clauses; an R.12 query returned matching clauses, and an attendance-condonation query returned relevant clauses. |
| Previous-year questions | `lookup_past_exam_questions` | Route and query normalization pass, retrieval unavailable. The current database has no indexed PYQ document and the graph has zero `Question` records. |

## Verification performed

- 27 focused tests passed across tool routing, schedule regressions, curriculum parsing, and intent routing.
- Python modules compiled successfully with `py_compile`.
- Student chat JavaScript passed `node --check`.
- `git diff --check` passed.
- Live route checks covered each registered tool and its corresponding service/data path where source records exist.

## Findings to carry into dataset refinement

1. Add representative PYQ source documents and verified extracted question records before expecting PYQ answers. Fine-tuning cannot make absent source facts retrievable.
2. Add authoritative Bloom-level annotations to curriculum ingestion if Bloom taxonomy questions must be supported. The graph currently has no Bloom-level nodes, so the response explicitly reports that the requested mapping is unavailable.
3. Build training examples from the tool contract: user phrasing, selected tool, normalized arguments, and expected grounded response. Include paraphrases and ambiguous requests, while preserving the distinction between calendar milestones and course-specific exam schedules.
4. Keep retrieval and database results as the factual source of truth. Fine-tuning should improve intent/tool selection and argument extraction, not encode dates, student records, or other changing academic facts into model weights.

## Scope caveat

This evaluation confirms the current local corpus and exercised paths; it does not prove coverage for every possible question or unseen document layout. When additional PYQ and Bloom-level data are indexed, rerun the same route checks and add examples from those documents to the evaluation set.
