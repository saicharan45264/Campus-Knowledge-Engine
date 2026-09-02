#!/usr/bin/env python3
"""
neo4j_schema.py — One-time schema migration: create Neo4j uniqueness
constraints and indexes for the Graph RAG pipeline.

Safe to re-run: all statements use IF NOT EXISTS.

Run before starting the server or before legacy_migration.py:
    python backend/migrations/neo4j_schema.py [--dry-run]
"""

import sys
import os

# Allow running from the backend/ directory or the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database import get_neo4j

DRY_RUN = "--dry-run" in sys.argv

CONSTRAINTS = [
    (
        "doc_id_unique",
        "CREATE CONSTRAINT doc_id_unique IF NOT EXISTS "
        "FOR (d:Document) REQUIRE d.id IS UNIQUE"
    ),
    (
        "question_id_unique",
        "CREATE CONSTRAINT question_id_unique IF NOT EXISTS "
        "FOR (q:Question) REQUIRE q.id IS UNIQUE"
    ),
    (
        "topic_id_unique",
        "CREATE CONSTRAINT topic_id_unique IF NOT EXISTS "
        "FOR (t:Topic) REQUIRE t.id IS UNIQUE"
    ),
    (
        "course_code_unique",
        "CREATE CONSTRAINT course_code_unique IF NOT EXISTS "
        "FOR (c:Course) REQUIRE c.code IS UNIQUE"
    ),
]

INDEXES = [
    (
        "topic_normalized_name_idx",
        "CREATE INDEX topic_normalized_name_idx IF NOT EXISTS "
        "FOR (t:Topic) ON (t.normalized_name)"
    ),
    (
        "question_course_idx",
        "CREATE INDEX question_course_idx IF NOT EXISTS "
        "FOR (q:Question) ON (q.course_code)"
    ),
    (
        "topic_course_idx",
        "CREATE INDEX topic_course_idx IF NOT EXISTS "
        "FOR (t:Topic) ON (t.course_code)"
    ),
    (
        "topic_approved_idx",
        "CREATE INDEX topic_approved_idx IF NOT EXISTS "
        "FOR (t:Topic) ON (t.approved)"
    ),
]

DEDUP_QUERIES = [
    ("Document.id", """
        MATCH (d:Document)
        WITH d.id AS id, collect(d) AS nodes
        WHERE size(nodes) > 1
        UNWIND nodes[1..] AS dup
        DETACH DELETE dup
    """),
    ("Question.id", """
        MATCH (q:Question)
        WITH q.id AS id, collect(q) AS nodes
        WHERE size(nodes) > 1
        UNWIND nodes[1..] AS dup
        DETACH DELETE dup
    """),
    ("Topic.id", """
        MATCH (t:Topic)
        WITH t.id AS id, collect(t) AS nodes
        WHERE size(nodes) > 1
        UNWIND nodes[1..] AS dup
        DETACH DELETE dup
    """),
    ("Course.code", """
        MATCH (c:Course)
        WITH c.code AS code, collect(c) AS nodes
        WHERE size(nodes) > 1
        UNWIND nodes[1..] AS dup
        DETACH DELETE dup
    """),
]


def run_migration(driver):
    with driver.session() as session:

        print("\n=== Step 1: Deduplication ===")
        for label, cypher in DEDUP_QUERIES:
            if DRY_RUN:
                print(f"  [DRY-RUN] Would dedup: {label}")
            else:
                result = session.run(cypher)
                summary = result.consume()
                deleted = summary.counters.nodes_deleted
                if deleted:
                    print(f"  Deduped {label}: deleted {deleted} duplicate nodes")
                else:
                    print(f"  Deduped {label}: no duplicates found")

        print("\n=== Step 2: Uniqueness Constraints ===")
        for name, cypher in CONSTRAINTS:
            if DRY_RUN:
                print(f"  [DRY-RUN] Would create constraint: {name}")
            else:
                try:
                    session.run(cypher)
                    print(f"  OK  constraint: {name}")
                except Exception as e:
                    print(f"  ERR constraint {name}: {e}")

        print("\n=== Step 3: Indexes ===")
        for name, cypher in INDEXES:
            if DRY_RUN:
                print(f"  [DRY-RUN] Would create index: {name}")
            else:
                try:
                    session.run(cypher)
                    print(f"  OK  index: {name}")
                except Exception as e:
                    print(f"  ERR index {name}: {e}")

        if not DRY_RUN:
            print("\n=== Step 4: Verification ===")
            try:
                constraints = session.run("SHOW CONSTRAINTS").data()
                print(f"  Total constraints in database: {len(constraints)}")
                for c in constraints:
                    name = c.get("name", "?")
                    labelsOrTypes = c.get("labelsOrTypes", [])
                    print(f"    * {name} on {labelsOrTypes}")
            except Exception as e:
                print(f"  Could not list constraints: {e}")

    print("\nDone." if not DRY_RUN else "\n[DRY-RUN] No changes made.")


if __name__ == "__main__":
    mode = "DRY-RUN" if DRY_RUN else "APPLY"
    print(f"Neo4j Schema Migration — {mode} mode")
    print("=" * 50)
    driver = get_neo4j()
    run_migration(driver)
