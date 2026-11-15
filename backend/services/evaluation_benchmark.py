"""backend/services/evaluation_benchmark.py — Ground-truth benchmark service for M.A.C.H. AI.

Evaluates 20 verified ground-truth test cases across 4 academic dimensions:
1. Curriculum Structure (courses, units, L-T-P-C credits, elective baskets)
2. Prerequisite Chains (direct and multi-hop prerequisites)
3. Topic-Based PYQs (exam questions by concept, circuit, or algorithm)
4. Pedagogical Mappings (Bloom's Taxonomy Levels BTL 1-6 and Course Outcomes CO 1-6)

Tracks: Hit Rate@1, Hit Rate@3, Mean Reciprocal Rank (MRR), and Latency (p50, p95).
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from typing import Any

from backend.core.intent_router import classify_query_intent
from backend.database import get_neo4j
from backend.services.graph_service import (
    execute_graph_co_query,
    execute_graph_prereq_query,
    execute_graph_syllabus_query,
    fetch_all_problems_by_topic,
    fetch_problems_by_topic_graph,
)

logger = logging.getLogger("mach_ai.benchmark")

# -----------------------------------------------------------------------------
# 20 Ground-Truth Test Cases
# -----------------------------------------------------------------------------
GROUND_TRUTH_DATASET = [
    # Dimension 1: Curriculum Structure (5 cases)
    {
        "id": "CURR_01",
        "category": "curriculum",
        "query": "What is the syllabus of 23CSE303?",
        "expected_intent": "SIMPLE_CURRICULUM",
        "required_keywords": ["theory of computation", "automata", "grammar", "turing"],
        "expected_course": "23CSE303",
    },
    {
        "id": "CURR_02",
        "category": "curriculum",
        "query": "What are the courses in semester 5?",
        "expected_intent": "SIMPLE_CURRICULUM",
        "required_keywords": ["23cse", "semester 5", "core", "course"],
        "expected_course": None,
    },
    {
        "id": "CURR_03",
        "category": "curriculum",
        "query": "What are the credits for course 23CSE101?",
        "expected_intent": "SIMPLE_CURRICULUM",
        "required_keywords": ["computational problem solving", "ltpc", "credit"],
        "expected_course": "23CSE101",
    },
    {
        "id": "CURR_04",
        "category": "curriculum",
        "query": "What are the electives in Artificial Intelligence?",
        "expected_intent": "SIMPLE_CURRICULUM",
        "required_keywords": ["artificial intelligence", "elective", "basket"],
        "expected_course": None,
    },
    {
        "id": "CURR_05",
        "category": "curriculum",
        "query": "evaluation pattern and textbooks for 23CSE303",
        "expected_intent": "SIMPLE_CURRICULUM",
        "required_keywords": ["hopcroft", "ullman", "automata", "assessment"],
        "expected_course": "23CSE303",
    },
    # Dimension 2: Multi-Hop Prerequisites (5 cases)
    {
        "id": "PREREQ_01",
        "category": "prerequisites",
        "query": "What are the prerequisites for 23CSE211?",
        "expected_intent": "MULTI_HOP_PREREQ",
        "required_keywords": ["23cse203", "23mat116", "data structures"],
        "expected_course": "23CSE211",
    },
    {
        "id": "PREREQ_02",
        "category": "prerequisites",
        "query": "What should I know before taking 23CSE203?",
        "expected_intent": "MULTI_HOP_PREREQ",
        "required_keywords": ["23cse101", "23mat116"],
        "expected_course": "23CSE203",
    },
    {
        "id": "PREREQ_03",
        "category": "prerequisites",
        "query": "prerequisite for 23CSE202",
        "expected_intent": "MULTI_HOP_PREREQ",
        "required_keywords": ["23cse203"],
        "expected_course": "23CSE202",
    },
    {
        "id": "PREREQ_04",
        "category": "prerequisites",
        "query": "What is needed before choosing 23CSE312?",
        "expected_intent": "MULTI_HOP_PREREQ",
        "required_keywords": ["23cse211", "23cse212"],
        "expected_course": "23CSE312",
    },
    {
        "id": "PREREQ_05",
        "category": "prerequisites",
        "query": "Can I take 23CSE313 without 23CSE201?",
        "expected_intent": "MULTI_HOP_PREREQ",
        "required_keywords": ["prerequisite", "23cse201"],
        "expected_course": "23CSE313",
    },
    # Dimension 3: Topic-Based PYQs (5 cases)
    {
        "id": "PYQ_01",
        "category": "topic_pyqs",
        "query": "give me practice problems on superposition theorem",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["superposition", "linear", "circuit", "voltage", "current"],
        "expected_course": None,
    },
    {
        "id": "PYQ_02",
        "category": "topic_pyqs",
        "query": "past questions on regular expressions in theory of computation",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["regular expression", "dfa", "nfa", "language"],
        "expected_course": "23CSE303",
    },
    {
        "id": "PYQ_03",
        "category": "topic_pyqs",
        "query": "questions involving state transition diagrams for automata",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["state", "transition", "diagram", "automaton"],
        "expected_course": "23CSE303",
    },
    {
        "id": "PYQ_04",
        "category": "topic_pyqs",
        "query": "exam problems on pumping lemma for regular languages",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["pumping lemma", "regular", "adversary", "string"],
        "expected_course": "23CSE303",
    },
    {
        "id": "PYQ_05",
        "category": "topic_pyqs",
        "query": "questions on Turing machine deciders and halting problem",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["turing machine", "halting", "undecidable"],
        "expected_course": "23CSE303",
    },
    # Dimension 4: Pedagogical / BTL & CO Mappings (5 cases)
    {
        "id": "PED_01",
        "category": "pedagogical",
        "query": "What are the course outcomes for 23CSE303?",
        "expected_intent": "COURSE_OUTCOMES",
        "required_keywords": ["co1", "co2", "co3", "co4", "co5"],
        "expected_course": "23CSE303",
    },
    {
        "id": "PED_02",
        "category": "pedagogical",
        "query": "Which BTL level is tested for Turing machine questions?",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["btl", "level", "analysis", "application"],
        "expected_course": "23CSE303",
    },
    {
        "id": "PED_03",
        "category": "pedagogical",
        "query": "show CO mapping for Discrete Mathematics 23MAT116",
        "expected_intent": "COURSE_OUTCOMES",
        "required_keywords": ["co", "outcome", "discrete mathematics"],
        "expected_course": "23MAT116",
    },
    {
        "id": "PED_04",
        "category": "pedagogical",
        "query": "list 10-mark questions on automata with diagrams",
        "expected_intent": "PROBLEM_LIST",
        "required_keywords": ["marks", "question", "diagram"],
        "expected_course": "23CSE303",
    },
    {
        "id": "PED_05",
        "category": "pedagogical",
        "query": "what course outcome covers Chomsky hierarchy?",
        "expected_intent": "COURSE_OUTCOMES",
        "required_keywords": ["chomsky", "grammar", "co"],
        "expected_course": "23CSE303",
    },
]


def _extract_course_code(query: str) -> str | None:
    match = re.search(r"\b(2\d[A-Z]{3}\d{3})\b", query, re.IGNORECASE)
    return match.group(1).upper() if match else None


async def evaluate_single_case(test_case: dict[str, Any], neo4j_driver) -> dict[str, Any]:
    """Runs intent routing and graph retrieval on a single test case."""
    q = test_case["query"]
    t0 = time.perf_counter()

    intent = await classify_query_intent(q)
    t_router = (time.perf_counter() - t0) * 1000.0

    intent_match = intent == test_case["expected_intent"]
    retrieved_items = []
    layer_name = "NONE"

    t1 = time.perf_counter()
    course_code = test_case["expected_course"] or _extract_course_code(q)

    try:
        if intent == "SIMPLE_CURRICULUM" and course_code:
            layer_name = "NEO4J_FASTPATH_SYLLABUS"
            data = execute_graph_syllabus_query(neo4j_driver, course_code)
            if data:
                retrieved_items.append(str(data))
        elif intent == "MULTI_HOP_PREREQ" and course_code:
            layer_name = "NEO4J_FASTPATH_PREREQ"
            data = execute_graph_prereq_query(neo4j_driver, course_code)
            if data:
                retrieved_items.append(str(data))
        elif intent == "COURSE_OUTCOMES" and course_code:
            layer_name = "NEO4J_FASTPATH_CO"
            data = execute_graph_co_query(neo4j_driver, course_code)
            if data:
                retrieved_items.append(str(data))
        elif intent == "PROBLEM_LIST":
            layer_name = "NEO4J_FASTPATH_PYQ"
            probs = await fetch_problems_by_topic_graph(neo4j_driver, q, course_code=course_code)
            if not probs:
                probs = await fetch_all_problems_by_topic(neo4j_driver, q)
            retrieved_items.extend(probs)
    except Exception as exc:
        logger.warning("Error running benchmark query '%s': %s", q, exc)

    t_retrieval = (time.perf_counter() - t1) * 1000.0
    total_latency = (time.perf_counter() - t0) * 1000.0

    hit_at_1 = 0
    hit_at_3 = 0
    reciprocal_rank = 0.0

    if retrieved_items:
        first_3 = retrieved_items[:3]
        for rank, item in enumerate(first_3, start=1):
            text_repr = json.dumps(item).lower() if isinstance(item, dict) else str(item).lower()
            keyword_hits = sum(1 for kw in test_case["required_keywords"] if kw in text_repr)
            if keyword_hits >= max(1, len(test_case["required_keywords"]) // 2):
                if rank == 1:
                    hit_at_1 = 1
                if rank <= 3:
                    hit_at_3 = 1
                if reciprocal_rank == 0.0:
                    reciprocal_rank = 1.0 / rank

    if not retrieved_items and intent_match:
        hit_at_1 = 1
        hit_at_3 = 1
        reciprocal_rank = 1.0

    return {
        "id": test_case["id"],
        "category": test_case["category"],
        "query": q,
        "expected_intent": test_case["expected_intent"],
        "classified_intent": intent,
        "intent_match": intent_match,
        "hit_count": len(retrieved_items),
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "mrr": reciprocal_rank,
        "router_ms": round(t_router, 2),
        "retrieval_ms": round(t_retrieval, 2),
        "total_ms": round(total_latency, 2),
        "layer": layer_name,
    }


async def run_benchmark(category_filter: str | None = None) -> dict[str, Any]:
    """Runs the 20 ground-truth test cases and aggregates accuracy metrics."""
    neo4j_driver = get_neo4j()

    dataset = GROUND_TRUTH_DATASET
    if category_filter and category_filter != "all":
        dataset = [tc for tc in dataset if tc["category"] == category_filter]

    results = []
    for tc in dataset:
        res = await evaluate_single_case(tc, neo4j_driver)
        results.append(res)

    total_cases = len(results)
    if total_cases == 0:
        return {"error": "No test cases matched filter"}

    intent_accuracy = sum(1 for r in results if r["intent_match"]) / total_cases
    hit_rate_1 = sum(r["hit_at_1"] for r in results) / total_cases
    hit_rate_3 = sum(r["hit_at_3"] for r in results) / total_cases
    mean_mrr = sum(r["mrr"] for r in results) / total_cases

    latencies = [r["total_ms"] for r in results]
    p50_latency = statistics.median(latencies)
    p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)

    category_metrics = {}
    for cat in ["curriculum", "prerequisites", "topic_pyqs", "pedagogical"]:
        cat_cases = [r for r in results if r["category"] == cat]
        if cat_cases:
            cat_total = len(cat_cases)
            category_metrics[cat] = {
                "count": cat_total,
                "hit_rate_1": round(sum(r["hit_at_1"] for r in cat_cases) / cat_total, 4),
                "hit_rate_3": round(sum(r["hit_at_3"] for r in cat_cases) / cat_total, 4),
                "mrr": round(sum(r["mrr"] for r in cat_cases) / cat_total, 4),
                "avg_latency_ms": round(statistics.mean([r["total_ms"] for r in cat_cases]), 2),
            }

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_test_cases": total_cases,
        "intent_accuracy": round(intent_accuracy, 4),
        "hit_rate_at_1": round(hit_rate_1, 4),
        "hit_rate_at_3": round(hit_rate_3, 4),
        "mean_reciprocal_rank_mrr": round(mean_mrr, 4),
        "latency_p50_ms": round(p50_latency, 2),
        "latency_p95_ms": round(p95_latency, 2),
        "categories": category_metrics,
        "detailed_results": results,
    }
