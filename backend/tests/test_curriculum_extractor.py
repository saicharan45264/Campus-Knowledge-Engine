"""
Tests for curriculum_extractor.py

These tests use synthetic syllabus text that mirrors the format of a real
23CSE303 (Theory of Computation) curriculum PDF.

They run entirely offline (no DB, no Neo4j, no LLM call) by mocking the
LLM fallback and the PDF reader.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, AsyncMock

from curriculum_extractor import (
    _extract_topics_deterministic,
    _is_valid_topic,
    _is_ambiguous,
    _unit_text_blocks,
    STOP_BOUNDARY_RE,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

UNIT1_SAFE_TEXT = """\
Introduction to Formal Languages and Automata
Finite State Machine - Definition and Representation
Deterministic Finite Automaton (DFA)
Non-Deterministic Finite State Machine (NDFA / NFA)
Equivalence of DFA and NFA
Minimization of Finite State Machine
Regular Expressions
Equivalence of Regular Expressions and Finite Automata
"""

UNIT1_WITH_TEXTBOOK = UNIT1_SAFE_TEXT + """\
Text Books:
1. Introduction to Automata Theory, Languages and Computation — Hopcroft, Motwani, Ullman, 3rd Edition, Pearson, 2007.
2. Introduction to the Theory of Computation — Michael Sipser, 3rd Edition, Cengage, 2013.
"""

UNIT2_SAFE_TEXT = """\
Context-Free Grammars (CFG)
Derivation Trees and Ambiguity in CFG
Simplification of CFGs
Chomsky Normal Form (CNF) and Greibach Normal Form (GNF)
Pushdown Automata (PDA) — Definition and Types
Equivalence of PDA and CFG
"""

FULL_COURSE_TEXT = f"""
23CSE303 Theory of Computation L-T-P-C: 3-0-0-3

Unit 1
{UNIT1_WITH_TEXTBOOK}

Unit 2
{UNIT2_SAFE_TEXT}
Text Books:
Hopcroft, Motwani, Ullman.

Unit 3
Turing Machine Model and Variations
Church-Turing Thesis
Decidability and Undecidability
Halting Problem
Complexity Classes P and NP
"""


# ---------------------------------------------------------------------------
# Test _is_valid_topic
# ---------------------------------------------------------------------------

class TestIsValidTopic:
    def test_rejects_bare_number(self):
        assert not _is_valid_topic("3")

    def test_rejects_publication_year(self):
        assert not _is_valid_topic("2007")

    def test_rejects_short_string(self):
        assert not _is_valid_topic("DFA")

    def test_rejects_author_pattern(self):
        assert not _is_valid_topic("Hopcroft,")

    def test_rejects_edition_marker(self):
        assert not _is_valid_topic("3rd Edition, Pearson")

    def test_accepts_hyphenated_concept(self):
        assert _is_valid_topic("Non-Deterministic Finite State Machine (NDFA / NFA)")

    def test_accepts_multi_word_topic(self):
        assert _is_valid_topic("Context-Free Grammars (CFG)")

    def test_accepts_standard_topic(self):
        assert _is_valid_topic("Minimization of Finite State Machine")

    def test_rejects_single_lowercase_word(self):
        assert not _is_valid_topic("automata")

    def test_rejects_isbn_containing_string(self):
        assert not _is_valid_topic("ISBN 978-0-13-228236-7")


# ---------------------------------------------------------------------------
# Test stop boundary
# ---------------------------------------------------------------------------

class TestStopBoundary:
    def test_textbook_header_is_boundary(self):
        assert STOP_BOUNDARY_RE.match("Text Books:")

    def test_textbook_alt_header_is_boundary(self):
        assert STOP_BOUNDARY_RE.match("Text Book(s):")

    def test_reference_header_is_boundary(self):
        assert STOP_BOUNDARY_RE.match("References")

    def test_evaluation_header_is_boundary(self):
        assert STOP_BOUNDARY_RE.match("Evaluation Pattern")

    def test_co_table_is_boundary(self):
        assert STOP_BOUNDARY_RE.match("CO Table")

    def test_topic_line_is_not_boundary(self):
        assert not STOP_BOUNDARY_RE.match("Minimization of Finite State Machine")


# ---------------------------------------------------------------------------
# Test deterministic topic extraction
# ---------------------------------------------------------------------------

class TestDeterministicExtraction:
    def test_hyphenated_concept_preserved(self):
        """'Non-Deterministic Finite State Machine' must be a single topic."""
        topics = _extract_topics_deterministic(UNIT1_SAFE_TEXT)
        names = [t for t in topics if "non-deterministic" in t.lower() or "NDFA" in t or "NFA" in t]
        assert len(names) >= 1, f"Expected at least one NFA/NDFA topic, got: {topics}"

    def test_no_textbook_content_after_stop(self):
        """Topics extracted from unit text with a Text Book section must not include authors or ISBNs."""
        # _unit_text_blocks applies the stop boundary
        blocks = _unit_text_blocks(FULL_COURSE_TEXT)
        unit1 = next((b for b in blocks if b["number"] == 1), None)
        assert unit1 is not None
        topics = _extract_topics_deterministic(unit1["raw_text"])
        names_lower = [t.lower() for t in topics]
        assert not any("hopcroft" in n for n in names_lower), f"Author leaked into topics: {topics}"
        assert not any("sipser" in n for n in names_lower), f"Author leaked into topics: {topics}"
        assert not any("pearson" in n for n in names_lower), f"Publisher leaked into topics: {topics}"
        assert not any("2007" in n for n in names_lower), f"Year leaked into topics: {topics}"

    def test_three_units_detected(self):
        blocks = _unit_text_blocks(FULL_COURSE_TEXT)
        assert len(blocks) == 3, f"Expected 3 units, got {len(blocks)}"

    def test_unit2_topics_extracted(self):
        blocks = _unit_text_blocks(FULL_COURSE_TEXT)
        unit2 = next((b for b in blocks if b["number"] == 2), None)
        assert unit2 is not None
        topics = _extract_topics_deterministic(unit2["raw_text"])
        assert len(topics) >= 3, f"Expected >= 3 topics in Unit 2, got: {topics}"

    def test_minimization_is_one_topic(self):
        topics = _extract_topics_deterministic(UNIT1_SAFE_TEXT)
        min_topics = [t for t in topics if "minimization" in t.lower()]
        assert len(min_topics) == 1, f"Minimization should be exactly one topic: {min_topics}"


# ---------------------------------------------------------------------------
# Test ambiguity detection
# ---------------------------------------------------------------------------

class TestAmbiguityDetection:
    def test_not_ambiguous_for_good_extraction(self):
        topics = _extract_topics_deterministic(UNIT1_SAFE_TEXT)
        assert not _is_ambiguous(topics, UNIT1_SAFE_TEXT)

    def test_ambiguous_when_no_topics_found(self):
        # Non-trivial text but no topics (all lines rejected)
        text = "1\n2\n3\n2007\n1984\nHopcroft,\n"
        topics = []
        assert _is_ambiguous(topics, text)

    def test_not_ambiguous_for_empty_text(self):
        # Trivially empty text — empty is not "ambiguous", just empty
        assert not _is_ambiguous([], "")
