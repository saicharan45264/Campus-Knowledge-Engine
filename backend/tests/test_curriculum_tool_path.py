"""Coverage for tool-selected curriculum query normalization."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from academic_tool_calling import route_tool_call
from main import execute_graph_syllabus_query


def test_course_code_aspect_and_unit_are_kept_as_separate_arguments():
    _intent, query = route_tool_call(
        "What topics are in Unit 2 of 23CSE401?",
        {
            "name": "lookup_curriculum_concept",
            "arguments": {
                "course_code_or_name": "23CSE401",
                "aspect": "unit_syllabus",
                "unit_number": 2,
            },
        },
    )

    assert "course=23CSE401" in query
    assert "aspect=unit_syllabus" in query
    assert "unit=2" in query


def test_bloom_request_reports_missing_source_mapping_instead_of_guessing():
    result = execute_graph_syllabus_query(
        None,
        "What Bloom taxonomy levels apply to 23CSE401? "
        "[Structured academic lookup: course=23CSE401 aspect=blooms_taxonomy]",
    )

    assert result["match_type"] == "unsupported_aspect"
    assert "not present in the indexed curriculum graph" in result["message"]
