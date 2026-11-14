"""backend/api/chat.py — Conversational RAG & Student Interaction Endpoints.

Handles SSE real-time token streaming, multi-tier intent execution,
question paper reverse image lookups, and student feedback recording.
"""
from __future__ import annotations

import base64
import logging
import re
import uuid

logger = logging.getLogger("mach_ai.chat")

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.academic_tool_calling import (
    fallback_academic_tool,
    route_tool_call,
    select_academic_tool,
)
from backend.api.auth import get_current_user
from backend.core.guardrails import sanitize_retrieved_context, sanitize_student_query
from backend.core.intent_router import classify_query_intent
from backend.core.semantic_cache import semantic_cache
from backend.core.telemetry import QueryLatencyTracker
from backend.database import StudentAccount, get_db, get_neo4j
from backend.parsers.visual_cropper import (
    _downscale_image_b64,
    describe_uploaded_image,
    load_image_as_b64,
)
from backend.services.embedding_service import (
    embed_multimodal,
    format_query_embedding_text,
)
from backend.services.embedding_service import embed_text as get_embedding
from backend.services.academic_document_qa_service import answer_academic_document_query
from backend.services.academic_qa_service import answer_calendar_query, answer_timetable_query
from backend.services.graph_service import (
    execute_graph_co_query,
    execute_graph_prereq_query,
    execute_graph_syllabus_query,
    execute_neo4j_pyq_search,
    expand_subgraph_context,
    fetch_all_problems_by_topic,
    fetch_problems_by_topic_graph,
    filter_contrasting_subtopics,
)
from backend.services.rag_orchestrator import (
    append_session_message,
    extract_course_code_from_query,
    extract_topic_from_query,
    generate_answer,
    generate_answer_stream,
    get_session_history,
    hybrid_search_rrf,
    resolve_followup_search_query,
)
from backend.services.regulation_service import answer_regulations_query

router = APIRouter(tags=["Chat & Tutoring"])


class ChatRequest(BaseModel):
    """Payload sent by student UI for text or multimodal inquiries."""
    message: str = Field(default="", max_length=15000)
    session_id: str | None = Field(default=None, max_length=128)
    image_b64: str | None = None


class FeedbackRequest(BaseModel):
    """User feedback score (1: helpful, -1: unhelpful)."""
    score: int = Field(..., ge=-1, le=1)


def _record_session_course_context(session_id: str, resp_content: dict) -> None:
    """Saves resolved course code(s) from Zero-LLM JSON responses so bare follow-ups work."""
    codes: list[str] = []
    data = resp_content.get("data") or {}
    if data.get("course_code"):
        codes.append(str(data["course_code"]))
    if resp_content.get("course_code"):
        codes.append(str(resp_content["course_code"]))
    for item in data.get("courses") or data.get("records") or resp_content.get("results") or resp_content.get("problems") or []:
        code = item.get("course_code") or item.get("code")
        if code and str(code) not in codes:
            codes.append(str(code))
    if codes:
        append_session_message(session_id, "assistant", f"Resolved courses: {', '.join(codes[:3])}")


@router.post("/chat")
async def chat_endpoint(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """
    Core conversational endpoint.
    Routes queries through Tier-0 guardrails, Tier-0.5 semantic cache,
    Zero-LLM fast-path or hybrid RRF RAG with real-time SSE token streaming.
    """
    question = payload.message or ""
    session_id = payload.session_id or "default_session"
    message_id = str(uuid.uuid4())

    tracker = QueryLatencyTracker(session_id, question or "[Image Query]")

    # 1. Tier-0 Guardrails (allow empty text only when a valid image attachment is provided)
    if question.strip() or not payload.image_b64:
        is_safe, advisory_msg = sanitize_student_query(question)
        if not is_safe:
            tracker.end_router("SECURITY_GUARDRAIL")
            tracker.end_retrieval("guardrail_refusal", hit_count=0)
            tracker.log_background()
            return JSONResponse(
                content={"type": "guardrail_refusal", "message": advisory_msg},
                headers={"X-Response-Type": "guardrail_refusal", "X-Message-ID": message_id},
            )

    images_for_llm: list[str] = []
    q_emb: list[float] | None = None
    if payload.image_b64:
        b64_data = payload.image_b64
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        try:
            decoded_bytes = base64.b64decode(b64_data, validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 image payload.")
        if len(decoded_bytes) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Attached image exceeds 20 MB limit.")
        is_valid_img = (
            decoded_bytes.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
            or (decoded_bytes.startswith(b"RIFF") and b"WEBP" in decoded_bytes[:16])
        )
        if not is_valid_img:
            raise HTTPException(status_code=400, detail="Attached file is not a valid image (PNG, JPEG, WebP, or GIF).")

        images_for_llm.append(_downscale_image_b64(b64_data, max_size=1024))
        try:
            image_description = await describe_uploaded_image(b64_data)
            if image_description and not image_description.startswith("Error analyzing image:"):
                if not question.strip():
                    question = f"Explain this academic image and relate it to my syllabus & PYQs:\n{image_description}"
                else:
                    question = f"{question}\n\n[Uploaded Image Context: {image_description}]"
        except Exception as e:
            logger.warning("Error analyzing image: %s", e)
        if not question.strip():
            question = "Explain the uploaded academic diagram or question."
        q_emb = await embed_multimodal(format_query_embedding_text(question), [b64_data])

    append_session_message(session_id, "user", question)

    context_parts = []
    search_query = resolve_followup_search_query(session_id, question)
    course_filter = extract_course_code_from_query(search_query)

    async def _get_q_emb() -> list[float]:
        nonlocal q_emb
        if q_emb is None:
            q_emb = await get_embedding(format_query_embedding_text(search_query))
        return q_emb

    # 2. Structured academic-document tools. The model chooses a registered
    # operation and extracts query parameters; application services retrieve
    # only database records. A bounded lexical dispatcher keeps these paths
    # usable when the remote Ollama tunnel is unavailable.
    tracker.start_router()
    tool_call = None
    if not payload.image_b64:
        from backend.academic_tool_calling import calendar_query_intent, exam_schedule_query_intent, seating_query_intent
        from backend.services.regulation_service import is_regulation_query
        from backend.services.timetable_directory import is_timetable_directory_query
        if re.search(r"\b(lab(?:oratory)?|it\s*lab)\b", search_query, re.I) and not re.search(r"\b(exam|examination|seating|exam\s+hall)\b", search_query, re.I):
            tool_call = {"name": "lookup_lab_schedule", "arguments": {"lab_identifier": "IT Lab-I"}}
        elif seating_query_intent(search_query):
            tool_call = {"name": "lookup_exam_seating", "arguments": {}}
        elif is_regulation_query(search_query):
            tool_call = {"name": "lookup_academic_regulations", "arguments": {}}
        elif exam_schedule_query_intent(search_query):
            tool_call = {"name": "lookup_exam_schedule", "arguments": {"exam_type": "mid_term" if re.search(r"\bmid\s*(?:term|sem)|midterm\b", search_query, re.I) else "end_sem" if re.search(r"\b(end\s*sem(?:ester)?|endsem|final exam)\b", search_query, re.I) else "all"}}
        elif calendar_query_intent(search_query):
            tool_call = {"name": "lookup_academic_calendar", "arguments": {}}
        elif is_timetable_directory_query(search_query) or (re.search(r"\bschedule\b", search_query, re.I) and re.search(r"\b(section|monday|tuesday|wednesday|thursday|friday)\b", search_query, re.I)) or (re.search(r"\b(class|classes|timetable|slots?|free periods?|vacant periods?|mentoring|CIR|project phase|lectures?)\b", search_query, re.I) and not re.search(r"\b(exam|exams|examination|holiday|calendar|regulation)\b", search_query, re.I)):
            tool_call = {"name": "lookup_class_timetable", "arguments": {}}
        else:
            tool_call = await select_academic_tool(search_query)
        if tool_call is None:
            tool_call = fallback_academic_tool(search_query)
    routed = route_tool_call(search_query, tool_call) if tool_call else None
    if routed:
        intent, structured_query = routed
        tracker.end_router(intent)
        tracker.start_retrieval()
        profile_result = await db.execute(
            select(StudentAccount).where(StudentAccount.college_email == (user.get("username") or "").strip().lower())
        )
        profile = profile_result.scalar_one_or_none()
        try:
            tool_name = tool_call.get("name")
            if intent == "CALENDAR_LOOKUP":
                from backend.services.calendar_qa_service import answer_calendar_query as restored_calendar_query
                result_data = await restored_calendar_query(
                    structured_query,
                    db,
                    semester=profile.semester if profile else None,
                )
            elif intent == "TIMETABLE_LOOKUP":
                result_data = await answer_timetable_query(
                    structured_query,
                    db,
                    department=profile.department if profile else "",
                    semester=profile.semester if profile else "",
                    section=profile.section if profile else "",
                )
            elif intent == "ACADEMIC_DOCUMENT_LOOKUP":
                result_data = await answer_academic_document_query(
                    structured_query,
                    db,
                    department=profile.department if profile else "",
                    semester=profile.semester if profile else "",
                    student_roll_number=profile.roll_number if profile else None,
                )
            else:
                result_data = await answer_regulations_query(structured_query, db)
            result_data = result_data or {"markdown_answer": "No matching academic records were found."}
        except Exception:
            logger.exception("Structured academic lookup failed | intent=%s tool=%s", intent, tool_name)
            result_data = {"markdown_answer": "I couldn't retrieve the requested academic record right now. Please retry; the server log contains the retrieval error."}

        tracker.end_retrieval("structured_academic_lookup", hit_count=len(result_data.get("records") or result_data.get("events") or result_data.get("clauses") or []))
        tracker.log_background()
        response_data = {
            "type": "academic_result",
            "intent": intent,
            "query": question,
            **result_data,
        }
        append_session_message(session_id, "assistant", result_data.get("markdown_answer") or "")
        return JSONResponse(
            content=jsonable_encoder(response_data),
            headers={"X-Response-Type": "academic_result", "X-Message-ID": message_id},
        )

    # 3. Existing graph and general RAG paths.
    intent = await classify_query_intent(search_query, query_embedding=q_emb)
    tracker.end_router(intent)

    # 3. Tier-0.5 Semantic Cache (only for LLM-backed intents so deterministic Neo4j queries never collide)
    if not payload.image_b64 and intent in ("GENERAL_QUERY", "SIMPLE_PYQ"):
        cached_payload = await semantic_cache.get(question, query_embedding=q_emb)
        if cached_payload:
            tracker.end_router("CACHED")
            tracker.end_retrieval("semantic_cache", hit_count=1)
            tracker.log_background()
            if isinstance(cached_payload, dict):
                res_type = cached_payload.get("type", "json")
                return JSONResponse(
                    content=cached_payload,
                    headers={"X-Response-Type": res_type, "X-Message-ID": message_id},
                )
            async def yield_cached():
                yield str(cached_payload)
            return StreamingResponse(
                yield_cached(),
                media_type="text/plain",
                headers={"X-Message-ID": message_id},
            )

    neo4j_driver = get_neo4j()
    tracker.start_retrieval()

    if intent == "PROBLEM_LIST":
        topic_name = extract_topic_from_query(question)
        mark_filter = None
        mark_match = re.search(r'\b(\d+)\s*(?:-| )?marks?\b', question.lower())
        if mark_match:
            try:
                mark_filter = int(mark_match.group(1))
            except Exception:
                pass

        graph_problems = (
            await fetch_problems_by_topic_graph(neo4j_driver, topic_name, course_code=course_filter)
            if topic_name
            else []
        )
        keyword_problems = (
            fetch_all_problems_by_topic(neo4j_driver, topic_name, course_code=course_filter)
            if topic_name
            else []
        )
        seen_texts, seen_ids, problems = set(), set(), []
        for p in graph_problems + keyword_problems:
            p_id = p.get("id")
            norm = re.sub(r'\s+', ' ', (p.get("text") or "").strip().lower())
            if norm and norm not in seen_texts and p_id not in seen_ids:
                seen_texts.add(norm)
                if p_id:
                    seen_ids.add(p_id)
                if mark_filter is not None:
                    p_marks = p.get("marks")
                    try:
                        if p_marks is None or int(p_marks) != mark_filter:
                            continue
                    except (ValueError, TypeError) as exc:
                        logger.debug("Mark filter conversion notice: %s", exc)
                        continue
                problems.append(p)

        problems = filter_contrasting_subtopics(problems, topic_name or question)

        if payload.image_b64 and not problems:
            mm_emb = await _get_q_emb()
            vis_chunks = await hybrid_search_rrf(
                db, topic_name or search_query, mm_emb, k=5,
                course_code=course_filter, content_type_filter="visual",
            )
            for vc in vis_chunks:
                if vc.get("vec_sim", 0.0) >= 0.55 and vc.get("content"):
                    img_m = re.search(r"!\[.*?\]\((.*?)\)", vc["content"])
                    problems.append({
                        "id": str(vc["id"]),
                        "question_number": "",
                        "text": vc["content"].split("\n\n![")[0],
                        "marks": None,
                        "btl": None,
                        "has_figure": bool(img_m),
                        "image_url": img_m.group(1) if img_m else "",
                        "course_code": vc.get("course_code") or "",
                    })

        if payload.image_b64 and problems:
            problems.sort(key=lambda p: (1 if p.get("has_figure") else 0), reverse=True)

        subgraph_expansion = None
        syllabus_guidance = None
        if not problems and topic_name:
            subgraph_expansion = expand_subgraph_context(neo4j_driver, topic_name)
            try:
                with neo4j_driver.session() as s_sess:
                    c_match = s_sess.run("""
                        MATCH (c:Course)
                        WHERE toLower(c.code) = toLower($t)
                           OR toLower(c.name) CONTAINS toLower($t)
                           OR (size($t) > 3 AND toLower($t) CONTAINS toLower(c.code))
                        OPTIONAL MATCH (c)-[:HAS_UNIT]->(u:Unit)
                        OPTIONAL MATCH (u)-[:HAS_TOPIC]->(t:Topic)
                        RETURN c.code AS code, c.name AS name,
                               u.number AS unit_num, u.title AS unit_title,
                               collect(DISTINCT t.name) AS topics
                        ORDER BY u.number
                    """, t=topic_name).data()
                    if c_match:
                        c_code = c_match[0]["code"]
                        c_name = c_match[0]["name"]
                        units_data = []
                        for r in c_match:
                            if r.get("unit_title"):
                                units_data.append({
                                    "unit": r.get("unit_title") or f"Unit {r.get('unit_num')}",
                                    "topics": [t for t in r.get("topics", []) if t][:8]
                                })
                        syllabus_guidance = {
                            "course_code": c_code,
                            "course_name": c_name,
                            "units": units_data
                        }
            except Exception as _e:
                logger.warning("Syllabus guidance notice: %s", _e)

        ret_layer = "graph_rag" if problems else ("subgraph_expansion" if subgraph_expansion else "syllabus_fallback")
        tracker.end_retrieval(ret_layer, hit_count=len(problems))
        tracker.log_background()

        resp_content = {
            "type": "problem_list",
            "topic": topic_name or "Practice Problems",
            "problems": problems,
            "syllabus_guidance": syllabus_guidance,
            "subgraph_expansion": subgraph_expansion,
        }
        _record_session_course_context(session_id, resp_content)
        return JSONResponse(
            content=resp_content,
            headers={"X-Response-Type": "problem_list", "X-Message-ID": message_id},
        )

    elif intent == "MULTI_HOP_PREREQ":
        records = execute_graph_prereq_query(neo4j_driver, search_query)
        tracker.end_retrieval("fast_path_prereq", hit_count=len(records))
        tracker.log_background()
        resp_content = {"type": "prereq_result", "results": records, "query": question}
        _record_session_course_context(session_id, resp_content)
        return JSONResponse(
            content=resp_content,
            headers={"X-Response-Type": "prereq_result", "X-Message-ID": message_id},
        )

    elif intent == "GRAPH_PYQ_MAPPING":
        records = execute_graph_co_query(neo4j_driver, search_query)
        tracker.end_retrieval("fast_path_co_btl", hit_count=len(records))
        tracker.log_background()
        resp_content = {"type": "co_mapping", "results": records, "query": question}
        _record_session_course_context(session_id, resp_content)
        return JSONResponse(
            content=resp_content,
            headers={"X-Response-Type": "co_mapping", "X-Message-ID": message_id},
        )

    elif intent == "SIMPLE_CURRICULUM":
        syllabus_res = execute_graph_syllabus_query(neo4j_driver, search_query)
        match_type = syllabus_res.get("match_type")
        if match_type in ("exact", "similar_list", "semester_courses", "elective_courses", "elective_baskets", "course_detail", "curriculum_info"):
            tracker.end_retrieval("fast_path_curriculum", hit_count=1)
            tracker.log_background()
            resp_content = {"type": "curriculum_result", "data": syllabus_res, "query": question}
            _record_session_course_context(session_id, resp_content)
            return JSONResponse(
                content=resp_content,
                headers={"X-Response-Type": "curriculum_result", "X-Message-ID": message_id},
            )
        else:
            emb_vec = await _get_q_emb()
            if emb_vec:
                chunks = await hybrid_search_rrf(db, search_query, emb_vec, k=5, course_code=course_filter)
                if chunks:
                    context_parts.append("--- ADDITIONAL TEXT CHUNKS ---")
                    for chunk in chunks:
                        if chunk.get("content"):
                            context_parts.append(sanitize_retrieved_context(chunk["content"]))
            tracker.end_retrieval("hybrid_rrf", hit_count=len(context_parts))

    elif intent == "GENERAL_QUERY":
        emb_vec = await _get_q_emb()
        if emb_vec:
            chunks = await hybrid_search_rrf(db, search_query, emb_vec, k=5, course_code=course_filter)
            if chunks:
                context_parts.append("--- REFERENCE MATERIAL ---")
                for chunk in chunks:
                    if chunk.get("content"):
                        context_parts.append(sanitize_retrieved_context(chunk["content"]))
        tracker.end_retrieval("hybrid_rrf", hit_count=len(context_parts))

    else:
        # SIMPLE_PYQ
        neo4j_results = execute_neo4j_pyq_search(neo4j_driver, search_query, course_code=course_filter)
        if neo4j_results:
            context_parts.append("--- NEO4J PYQ SEARCH RESULTS ---")
            for record in neo4j_results:
                img_url = record["image_url"].replace(" ", "%20") if record.get("image_url") else None
                if img_url and img_url != "None" and len(images_for_llm) < 2:
                    img_b64 = await load_image_as_b64(img_url)
                    if img_b64:
                        images_for_llm.append(_downscale_image_b64(img_b64, max_size=1024))
                q_num_val = record.get("q_num") or record.get("question_number") or ""
                q_txt_val = record.get("q_text") or record.get("text") or ""
                img_markdown = f"\n![Diagram for Q{q_num_val}]({img_url})" if img_url and img_url != "None" else ""
                context_parts.append(
                    f"[Course: {record.get('course_code', '')} - Q: {q_num_val}]\n{sanitize_retrieved_context(q_txt_val)}{img_markdown}\nMarks: {record.get('marks')}\nBTL: {record.get('btl')}"
                )
            tracker.end_retrieval("graph_pyq_search", hit_count=len(neo4j_results))
        else:
            emb_vec = await _get_q_emb()
            if emb_vec:
                chunks = await hybrid_search_rrf(db, search_query, emb_vec, k=5, course_code=course_filter)
                if chunks:
                    context_parts.append("--- HYBRID SEARCH RESULTS (TEXT + SEMANTIC) ---")
                    for chunk in chunks:
                        if chunk.get("content"):
                            context_parts.append(sanitize_retrieved_context(chunk["content"]))
            tracker.end_retrieval("hybrid_rrf", hit_count=len(context_parts))

    final_context = "\n".join(context_parts)
    if len(final_context) > 4800:
        final_context = final_context[:4800] + "\n...[Context Truncated]..."

    prior_history = get_session_history(session_id)[:-1]

    async def stream_and_cache():
        full_response = []
        is_first_chunk = True
        disconnected = False
        async for chunk in generate_answer_stream(
            question, final_context, images=images_for_llm, think=True, history=prior_history
        ):
            if await request.is_disconnected():
                disconnected = True
                break
            if is_first_chunk:
                tracker.record_ttft()
                is_first_chunk = False
            full_response.append(chunk)
            yield chunk

        if disconnected:
            return

        images = re.findall(r"!\[.*?\]\(.*?\)", final_context)
        if images:
            combined_response_str = "".join(full_response)
            unique_images = []
            for img in images:
                url_match = re.search(r"\((https?://.*?)\)", img)
                if url_match:
                    if url_match.group(1) not in combined_response_str:
                        unique_images.append(img)
                elif img not in combined_response_str:
                    unique_images.append(img)

            if unique_images:
                image_block = "\n\n### Diagrams from Questions:\n" + "\n\n".join(unique_images)
                full_response.append(image_block)
                yield image_block

        response_str = "".join(full_response)
        tracker.record_tokens(len(final_context) // 4, len(response_str) // 4)
        tracker.log_background()
        await semantic_cache.put(question, intent, response_str, query_embedding=q_emb)
        append_session_message(session_id, "assistant", response_str)

    return StreamingResponse(
        stream_and_cache(),
        media_type="text/plain",
        headers={"X-Message-ID": message_id},
    )


@router.post("/feedback/{message_id}")
async def submit_feedback(
    message_id: str,
    body: FeedbackRequest,
    user: dict = Depends(get_current_user),
):
    """Receives thumbs-up/down feedback from the student chat interface."""
    logger.info(
        "Feedback recorded | user=%s message_id=%s score=%s",
        user.get("username", "unknown"),
        message_id,
        body.score,
    )
    return {"message": "Feedback recorded", "message_id": message_id, "score": body.score}


@router.post("/image-query")
async def image_query_endpoint(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Student reverse lookup: describes an uploaded equation/diagram and searches curriculum."""
    # Bounded read to avoid memory exhaustion (max 20 MB for images)
    MAX_IMAGE_SIZE = 20 * 1024 * 1024
    image_buf = bytearray()
    while chunk := await file.read(512 * 1024):
        image_buf.extend(chunk)
        if len(image_buf) > MAX_IMAGE_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Image file exceeds maximum allowable size of 20 MB.",
            )
    image_bytes = bytes(image_buf)

    # Basic magic byte check for common image formats (PNG, JPEG, WebP, GIF)
    is_valid_image = (
        image_bytes.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a"))
        or (image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16])
    )
    if not is_valid_image:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid image (PNG, JPEG, WebP, or GIF).",
        )

    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    image_description = await describe_uploaded_image(b64_image)

    context_parts = []
    query_embedding = await embed_multimodal(
        format_query_embedding_text(image_description), [b64_image]
    )
    if query_embedding:
        try:
            chunks = await hybrid_search_rrf(db, image_description, query_embedding, k=5)
            if chunks:
                context_parts.append("--- RELEVANT TEXT FROM DOCUMENTS ---")
                for c in chunks:
                    context_parts.append(sanitize_retrieved_context(c["content"]))
        except Exception as e:
            logger.warning("Vector search notice: %s", e)

    try:
        neo4j_driver = get_neo4j()
        words = image_description.lower().split()
        with neo4j_driver.session() as session:
            records = session.run(
                """
                MATCH (n)
                WHERE (n:Topic OR n:Question)
                AND any(word IN $words WHERE toLower(coalesce(n.name, '')) CONTAINS word OR toLower(coalesce(n.text, '')) CONTAINS word)
                OPTIONAL MATCH (n)-[:BELONGS_TO]->(c:Course)
                OPTIONAL MATCH (c2:Course)-[:HAS_UNIT]->(:Unit)-[:HAS_TOPIC]->(n)
                RETURN labels(n) as labels, coalesce(n.name, '') as name, coalesce(n.text, '') as text,
                       coalesce(c.code, c2.code, n.course_code, '') as course_code
                LIMIT 5
                """,
                words=words,
            ).data()
            if records:
                context_parts.append("--- KNOWLEDGE GRAPH FACTS ---")
                for r in records:
                    lbl = r["labels"][0] if r["labels"] else "Concept"
                    txt = r["text"] or r["name"]
                    context_parts.append(f"[{lbl} - {r['course_code']}] {txt}")
    except Exception as e:
        logger.warning("Graph search notice: %s", e)

    final_context = "\n".join(context_parts)
    prompt_question = (
        f"A student uploaded an image. Content:\n\n{image_description}\n\n"
        f"Based on curriculum context, explain where this concept appears in the courses, "
        f"and what types of exam questions test this formula, circuit, or diagram."
    )
    ai_response = await generate_answer(prompt_question, final_context)
    return {"description": image_description, "response": ai_response}
