"""Answers timetable directory questions using records from the selected upload."""
from __future__ import annotations

import re
from sqlalchemy import select
from backend.database import TimetableVersion, ElectiveOption


def is_timetable_directory_query(question: str) -> bool:
    q = question.casefold()
    if re.search(r'\b(exam|examination|regulation|holiday|calendar|seating)\b', q):
        return False
    return bool(re.search(r'\b(who teaches|who handles|who is handling|who is the faculty|which faculties|assisting faculty|class advisors?|contact my class advisor|email address|professional elective|free electives?|pe\s*[- ]?\s*(?:iv|vi|v)\b|course code|where (?:is|are|does|do)|what room|where do i go|venue and faculty)\b', q))


def _tokens(value: str) -> set[str]:
    value = re.sub(r'\bai\b', 'artificial intelligence', value.casefold())
    stop = {'and','the','of','for','in','with','course','courses','group','groups','elective','engineers','engineering'}
    return {x for x in re.findall(r'[a-z0-9]+', value) if x not in stop and len(x) > 1}


def _bucket(text: str) -> str | None:
    q = text.casefold()
    if re.search(r'\bfree\s+elective', q): return 'free'
    m = re.search(r'(?:\bpe\s*[- ]*|professional\s+elective\s*)(vi|iv|v|6|4|5)\b', q)
    if not m: return None
    n = {'4':'IV','5':'V','6':'VI'}.get(m.group(1), m.group(1).upper())
    return 'PE - ' + n


async def answer_directory(question: str, db, version: TimetableVersion, sessions: list[dict]) -> dict | None:
    q = question.casefold()
    if re.search(r'\b(advisor|advisors|advisory|email address)\b', q):
        advisors = version.class_advisors or ''
        if re.search(r'\b(email|contact|address)\b', q) and '@' not in advisors:
            return {'markdown_answer': 'The indexed timetable does not contain a verified email address for the class advisors.'}
        return {'markdown_answer': f"**Class advisors:** {advisors}" if advisors else 'Class advisors are not recorded in the uploaded timetable.'}
    if re.search(r"\bcir\b", q) and re.search(r"\b(faculty|who teaches|who handles|where|venue|room)\b", q):
        rows = [s for s in sessions if (s.get("activity") or "").strip().casefold() == "cir"]
        field = "venue" if re.search(r"\b(where|venue|room)\b", q) else "faculty"
        values = list(dict.fromkeys(str(s[field]).strip() for s in rows if s.get(field)))
        answer = f"**CIR {field}:** " + ", ".join(values) if values else f"The uploaded timetable has no reliably indexed CIR {field}. Please check the source PDF."
        return {"markdown_answer": answer}
    if "mentoring" in q and re.search(r"\b(where|venue|room)\b", q):
        venues = list(dict.fromkeys(str(s['venue']).strip() for s in sessions if 'mentoring' in (s.get('activity') or '').casefold() and s.get('venue')))
        return {'markdown_answer': '**Mentoring location:** ' + ', '.join(venues) if venues else 'The mentoring location was not extracted from the timetable.'}
    opts = (await db.execute(select(ElectiveOption).where(ElectiveOption.timetable_id == version.id))).scalars().all()
    codes = {m.upper() for m in re.findall(r'\b\d{2}[a-z]{3}\d{3,4}\b', question, re.I)}
    is_lab = bool(re.search(r'\b(lab|laboratory|practical)\b', q))
    mentioned = [o for o in opts if (o.course_code or '').upper() in codes]
    if not mentioned:
        qt = _tokens(question)
        scores=[]
        for o in opts:
            ot=_tokens(o.course_name or '')
            if not ot: continue
            common=qt & ot
            # Require at least two informative matching words; a complete one-word name may match.
            if (len(common) >= 2 or (len(ot)==1 and ot <= qt)) and len(common)/len(ot) >= 0.5:
                scores.append((len(common)/len(ot),len(common),o))
        if scores:
            complete = [o for a,b,o in scores if a == 1.0]
            if complete:
                mentioned = complete
            else:
                top=max((a,b) for a,b,_ in scores)
                mentioned=[o for a,b,o in scores if (a,b)==top]
    if is_lab and mentioned:
        lab = [o for o in mentioned if 'lab' in (o.elective_bucket or '').casefold()]
        if lab: mentioned=lab
    elif mentioned:
        nonlab = [o for o in mentioned if 'lab' not in (o.elective_bucket or '').casefold()]
        if nonlab: mentioned=nonlab
    if mentioned:
        rows=[]
        for o in mentioned:
            category = o.elective_bucket if o.elective_bucket and o.elective_bucket.casefold() != 'general' else 'Not classified in the index; check the source PDF'
            parts=[f"**{o.course_code} — {o.course_name}**",f"Category: {category}"]
            if o.faculty: parts.append(f"Faculty: {o.faculty}")
            if o.venue and not (is_lab and re.search(r"\b(dr|ms|mr|mrs)\.?\s", o.venue, re.I)): parts.append(f"Venue details: {o.venue}")
            elif is_lab and o.venue: parts.append("Lab venue and assisting faculty need verification against the source PDF")
            rows.append('- ' + ' · '.join(parts))
        return {'timetable_mode':'directory','markdown_answer':'### Timetable course directory\n\n'+'\n'.join(rows)}
    bucket=_bucket(question)
    if bucket and re.search(r'\b(list|offered|available|fall under|subjects|courses|electives|which)\b', q) and not re.search(r'\b(when|schedule|scheduled|classes)\b', q):
        if bucket == 'free':
            return {'markdown_answer':'The indexed timetable does not reliably identify which directory entries belong to the free-elective bucket. Please check the source timetable.'}
        options=[o for o in opts if re.sub(r'\s+','',(o.elective_bucket or '').upper()) == re.sub(r'\s+','',bucket)]
        if re.search(r"\b(inter[- ]department(?:al)?|other departments?)\b", q):
            dept = (version.department or "").upper()
            options = [o for o in options if re.match(r"^\d{2}([A-Z]{3})", o.course_code or "", re.I) and re.match(r"^\d{2}([A-Z]{3})", o.course_code, re.I).group(1).upper() != dept]
        if options:
            return {'timetable_mode':'directory','markdown_answer':f'### {bucket} options\n\n'+'\n'.join(f'- **{o.course_code}** — {o.course_name}' for o in options)+'\n\nOnly courses assigned to this bucket in the indexed timetable are shown.'}
        return {'markdown_answer':f'No courses were indexed for {bucket} in this timetable.'}
    if codes or (is_timetable_directory_query(question) and re.search(r'\b(who|faculty|where|course code|room)\b',q)):
        return {'markdown_answer':'I could not find a reliable matching course or faculty entry in this class timetable. Please provide a course name or code.'}
    return None
