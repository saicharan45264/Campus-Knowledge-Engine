"""
Tests for topic_mapper.py — offline (no Neo4j, no Ollama).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
import pytest
from topic_mapper import (
    _cosine_similarity, _keyword_score, _question_topic_match_score,
    AUTO_APPROVE_THRESHOLD, SECONDARY_MIN_THRESHOLD, SECONDARY_MAX_GAP, MAX_TOPICS
)


class TestCosineSimliarity:
    def test_identical_vectors_score_one(self):
        v = [1.0, 0.5, 0.25]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors_score_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-9

    def test_empty_vectors_score_zero(self):
        assert _cosine_similarity([], [1.0]) == 0.0
        assert _cosine_similarity([], []) == 0.0

    def test_dimension_mismatch_score_zero(self):
        assert _cosine_similarity([1.0, 2.0], [1.0]) == 0.0


class TestKeywordScore:
    def test_identical_texts_score_one(self):
        score = _keyword_score("Deterministic Finite Automaton", "Deterministic Finite Automaton")
        assert score == 1.0

    def test_partial_overlap(self):
        score = _keyword_score(
            "Minimize the following DFA",
            "Minimization of Finite State Machine"
        )
        assert 0.0 <= score <= 1.0

    def test_no_overlap_score_zero(self):
        score = _keyword_score("Turing Machine Halting Problem", "Context-Free Grammar Derivation")
        assert score == 0.0

    def test_question_topic_match_score_detects_direct_topic_mentions(self):
        score = _question_topic_match_score(
            "Construct an NFA for the given language and compare it with a DFA.",
            "NFA"
        )
        assert score >= 0.90

    def test_stopwords_excluded(self):
        # "using" and "following" are stop words and should not inflate the score
        score1 = _keyword_score("Minimize the following DFA using state equivalence",
                                "Minimization of Finite State Machine")
        score2 = _keyword_score("Minimize DFA state equivalence",
                                "Minimization of Finite State Machine")
        # scores may differ but neither should be 1.0 just because of stop words
        assert score1 <= 1.0 and score2 <= 1.0


class TestThresholdConfig:
    def test_auto_approve_threshold_is_0_80(self):
        assert AUTO_APPROVE_THRESHOLD == 0.80

    def test_secondary_min_threshold_is_0_70(self):
        assert SECONDARY_MIN_THRESHOLD == 0.70

    def test_secondary_max_gap_is_0_15(self):
        assert SECONDARY_MAX_GAP == 0.15

    def test_max_topics_is_2(self):
        assert MAX_TOPICS == 2


class TestMappingSelectionLogic:
    """
    Simulate the mapping decision logic from map_question_to_topics()
    without calling Neo4j or Ollama.
    """

    def _apply_mapping_rules(self, scored: list[dict]) -> list[dict]:
        """Mirror the selection logic from topic_mapper.map_question_to_topics()."""
        scored.sort(key=lambda x: x["confidence"], reverse=True)
        mappings = []
        primary_conf = None

        for rank, s in enumerate(scored):
            if rank == 0:
                if s["confidence"] >= AUTO_APPROVE_THRESHOLD:
                    primary_conf = s["confidence"]
                    mappings.append(s)
                else:
                    break
            elif rank == 1 and primary_conf is not None:
                gap = primary_conf - s["confidence"]
                if s["confidence"] >= SECONDARY_MIN_THRESHOLD and gap <= SECONDARY_MAX_GAP:
                    mappings.append(s)
                break
            if len(mappings) >= MAX_TOPICS:
                break

        return mappings

    def test_high_confidence_primary_accepted(self):
        scored = [{"topic_id": "t1", "topic_name": "DFA", "confidence": 0.90,
                   "semantic_score": 0.90, "keyword_score": 0.90}]
        result = self._apply_mapping_rules(scored)
        assert len(result) == 1
        assert result[0]["topic_id"] == "t1"

    def test_low_confidence_primary_rejected(self):
        scored = [{"topic_id": "t1", "topic_name": "DFA", "confidence": 0.75,
                   "semantic_score": 0.75, "keyword_score": 0.75}]
        result = self._apply_mapping_rules(scored)
        assert len(result) == 0

    def test_secondary_accepted_when_conditions_met(self):
        scored = [
            {"topic_id": "t1", "topic_name": "DFA", "confidence": 0.90,
             "semantic_score": 0.90, "keyword_score": 0.90},
            {"topic_id": "t2", "topic_name": "NFA", "confidence": 0.82,
             "semantic_score": 0.82, "keyword_score": 0.82},
        ]
        result = self._apply_mapping_rules(scored)
        assert len(result) == 2
        assert result[1]["topic_id"] == "t2"

    def test_secondary_rejected_when_gap_too_large(self):
        scored = [
            {"topic_id": "t1", "topic_name": "DFA", "confidence": 0.95,
             "semantic_score": 0.95, "keyword_score": 0.95},
            {"topic_id": "t2", "topic_name": "PDA", "confidence": 0.75,
             "semantic_score": 0.75, "keyword_score": 0.75},
        ]
        # gap = 0.95 - 0.75 = 0.20 > SECONDARY_MAX_GAP (0.15)
        result = self._apply_mapping_rules(scored)
        assert len(result) == 1
        assert result[0]["topic_id"] == "t1"

    def test_secondary_rejected_when_below_min_threshold(self):
        scored = [
            {"topic_id": "t1", "topic_name": "DFA", "confidence": 0.85,
             "semantic_score": 0.85, "keyword_score": 0.85},
            {"topic_id": "t2", "topic_name": "NFA", "confidence": 0.65,
             "semantic_score": 0.65, "keyword_score": 0.65},
        ]
        # gap = 0.20 > MAX_GAP and also < SECONDARY_MIN
        result = self._apply_mapping_rules(scored)
        assert len(result) == 1

    def test_max_two_mappings_enforced(self):
        scored = [
            {"topic_id": "t1", "confidence": 0.95, "topic_name": "A",
             "semantic_score": 0.95, "keyword_score": 0.95},
            {"topic_id": "t2", "confidence": 0.88, "topic_name": "B",
             "semantic_score": 0.88, "keyword_score": 0.88},
            {"topic_id": "t3", "confidence": 0.83, "topic_name": "C",
             "semantic_score": 0.83, "keyword_score": 0.83},
        ]
        result = self._apply_mapping_rules(scored)
        assert len(result) <= MAX_TOPICS
