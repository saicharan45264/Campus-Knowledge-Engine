"""
regulation_service.py — Service retrieving and synthesizing Academic Regulations (B.Tech 2023).
Retrieves relevant clauses with parent context and cross-references.
"""
import re
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from database import RegulationClause, ClauseReference
from embedder import embed_text
from utils import hybrid_search_rrf


CLAUSE_PATTERN = re.compile(r"\b(R\.\d+(?:\.\d+)*|O\.\d+)\b", re.I)


async def answer_regulations_query(
    query_text: str,
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Retrieves regulation clauses for a question and returns the clauses and source references.
    """
    matched_clauses: List[Dict[str, Any]] = []
    seen_ids = set()

    # 1. Check if user explicitly mentioned a clause ID (e.g. "R.12.1" or "R.15")
    explicit_cids = CLAUSE_PATTERN.findall(query_text)
    if explicit_cids:
        for cid in explicit_cids:
            cid_upper = cid.upper()
            stmt = select(RegulationClause).where(
                or_(
                    RegulationClause.clause_id == cid_upper,
                    RegulationClause.parent_clause_id == cid_upper,
                )
            ).order_by(RegulationClause.clause_id)
            res = await db.execute(stmt)
            for row in res.scalars().all():
                if row.clause_id not in seen_ids:
                    seen_ids.add(row.clause_id)
                    matched_clauses.append({
                        "clause_id": row.clause_id,
                        "parent_clause_id": row.parent_clause_id,
                        "title": row.title,
                        "clause_text": row.clause_text,
                        "page_number": row.page_number,
                    })

    # 2. Hybrid vector + keyword search over the indexed regulation chunks.
    if not matched_clauses:
        q_vec = await embed_text(query_text)
        chunks = await hybrid_search_rrf(db, query_text=query_text, query_embedding=q_vec, k=12)
        for chunk in chunks:
            content = chunk.get("content", "")
            # The regulations ingestion format stores the clause ID in every chunk.
            # Keep this case-insensitive because older indexed chunks used varied casing.
            m = re.search(r"\bClause\s+(O\.\d+|R\.\d+(?:\.\d+)*)", content, re.I)
            if not m:
                continue
            cid = m.group(1).upper()
            if cid in seen_ids:
                continue
            c_stmt = select(RegulationClause).where(RegulationClause.clause_id == cid)
            c_res = await db.execute(c_stmt)
            clause_row = c_res.scalars().first()
            if clause_row:
                seen_ids.add(clause_row.clause_id)
                matched_clauses.append({
                    "clause_id": clause_row.clause_id,
                    "parent_clause_id": clause_row.parent_clause_id,
                    "title": clause_row.title,
                    "clause_text": clause_row.clause_text,
                    "page_number": clause_row.page_number,
                })

    # 2b. Reliable fallback over the structured clause table. This still works
    # when embeddings are unavailable or the vector index has not been populated.
    if not matched_clauses:
        stopwords = {"what", "does", "the", "say", "about", "for", "from", "with", "and", "are", "is", "how", "can", "may", "will", "shall", "student", "students", "regulation", "regulations", "document", "rule", "rules", "requirement", "requirements", "specific", "academic", "process", "please", "tell", "me", "explain"}
        terms = list(dict.fromkeys(t for t in re.findall(r"[a-zA-Z]{3,}", query_text.lower()) if t not in stopwords))
        if terms:
            predicates = []
            for term in terms:
                predicates.extend((RegulationClause.title.ilike(f"%{term}%"), RegulationClause.clause_text.ilike(f"%{term}%")))
            direct_stmt = select(RegulationClause).where(or_(*predicates)).limit(100)
            direct_res = await db.execute(direct_stmt)
            rows = direct_res.scalars().all()
            def relevance(row):
                title = (row.title or "").lower()
                body = (row.clause_text or "").lower()
                return (sum(3 for t in terms if t in title) + sum(1 for t in terms if t in body), len(terms), row.clause_id)
            rows.sort(key=relevance, reverse=True)
            for row in rows[:8]:
                seen_ids.add(row.clause_id)
                matched_clauses.append({
                    "clause_id": row.clause_id,
                    "parent_clause_id": row.parent_clause_id,
                    "title": row.title,
                    "clause_text": row.clause_text,
                    "page_number": row.page_number,
                })

    # 3. Pull in referenced clauses (e.g. if R.16.2 is matched, pull R.15.2)
    ref_clauses = []
    for c in matched_clauses:
        ref_stmt = select(ClauseReference).where(ClauseReference.from_clause_id == c["clause_id"])
        ref_res = await db.execute(ref_stmt)
        for ref in ref_res.scalars().all():
            target_id = ref.to_clause_id
            if target_id not in seen_ids:
                seen_ids.add(target_id)
                t_stmt = select(RegulationClause).where(RegulationClause.clause_id == target_id)
                t_res = await db.execute(t_stmt)
                target_row = t_res.scalars().first()
                if target_row:
                    ref_clauses.append({
                        "clause_id": target_row.clause_id,
                        "parent_clause_id": target_row.parent_clause_id,
                        "title": target_row.title,
                        "clause_text": target_row.clause_text,
                        "page_number": target_row.page_number,
                    })

    all_clauses = matched_clauses + ref_clauses

    if all_clauses:
        clause_blocks = []
        for c in all_clauses[:5]:
            p_text = f" *(Section {c['parent_clause_id']})*" if c.get('parent_clause_id') and c['parent_clause_id'] != c['clause_id'] else ""
            clause_blocks.append(
                f"#### Clause {c['clause_id']}: {c['title']}{p_text} — Page {c['page_number']}\n\n"
                f"{c['clause_text']}"
            )
        ans = (
            f"### 📜 Amrita B.Tech Academic Regulations (2023 Admissions)\n\n"
            f"Found **{len(all_clauses)} authoritative regulation clauses** relevant to your inquiry:\n\n"
            + "\n\n---\n\n".join(clause_blocks)
        )
    else:
        headings_stmt = select(RegulationClause).where(
            RegulationClause.clause_id.like("R.%"),
            RegulationClause.parent_clause_id == "R",
        ).order_by(RegulationClause.clause_id).limit(40)
        headings_res = await db.execute(headings_stmt)
        headings = headings_res.scalars().all()
        if headings:
            topic_lines = [f"- **{c.clause_id} — {c.title or 'Regulation'}**" for c in headings]
            ans = (
                "### Amrita B.Tech Academic Regulations (2023)\n\n"
                "I couldn’t identify the specific rule from that question. The indexed regulations cover these sections; ask about one by name (for example, attendance, examination, grading, or promotion):\n\n"
                + "\n".join(topic_lines)
            )
        else:
            ans = "The regulations document has no indexed clauses yet. Ask an administrator to reprocess the uploaded regulations PDF."

    return {
        "query": query_text,
        "markdown_answer": ans,
        "total_clauses": len(all_clauses),
        "clauses": all_clauses,
    }
