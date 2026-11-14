from backend.academic_tool_calling import exam_schedule_query_intent, fallback_academic_tool, route_tool_call


def test_generic_exam_date_routes_to_exam_schedule_without_assuming_end_sem():
    query = "What exams are scheduled on March 16, 2026?"
    call = fallback_academic_tool(query)
    assert call["name"] == "lookup_exam_schedule"
    assert call["arguments"]["exam_type"] == "all"
    intent, routed_query = route_tool_call(query, call)
    assert intent == "ACADEMIC_DOCUMENT_LOOKUP"
    assert "end_sem" not in routed_query
    assert "mid_term" not in routed_query


def test_course_name_exam_query_routes_to_course_schedule():
    call = fallback_academic_tool("When is the exam for Compiler Design?")
    assert call["name"] == "lookup_exam_schedule"
    assert call["arguments"]["exam_type"] == "all"


def test_midterm_commencement_routes_to_calendar_milestone():
    call = fallback_academic_tool("When do midterm examinations commence for Semester 5?")
    assert call["name"] == "lookup_academic_calendar"
    assert call["arguments"]["event_type"] == "midterm"


def test_exam_schedule_routes_before_calendar_for_window_requests():
    query = "When do the 6th-semester end-semester examinations start and end?"
    assert exam_schedule_query_intent(query)
    call = fallback_academic_tool(query)
    assert call["name"] == "lookup_exam_schedule"
    assert call["arguments"]["exam_type"] == "end_sem"


def test_course_code_lookup_without_exam_word_routes_to_exam_schedule():
    query = "What is the course code for Distributed Systems?"
    assert exam_schedule_query_intent(query)
    assert fallback_academic_tool(query)["name"] == "lookup_exam_schedule"


def test_saturday_exam_inventory_routes_to_exam_schedule():
    query = "Are there any examinations conducted on Saturdays?"
    assert exam_schedule_query_intent(query)
    assert fallback_academic_tool(query)["name"] == "lookup_exam_schedule"
