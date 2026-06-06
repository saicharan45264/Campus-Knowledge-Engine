"""
CurriculumLens — Knowledge Graph Service

Handles the Curriculum Knowledge Graph (CKG):
1. Auto-construction from syllabus documents (LLM-guided)
2. Entity and relation management in Neo4j
3. Graph queries for retrieval and visualization
4. PYQ question linking to concept nodes

CKG Schema:
  Course → Unit → Topic → Concept → Sub-Concept
                                  → Formula
                                  → Algorithm
  Concept ←→ Concept (relatedTo, prerequisiteOf)
  PYQQuestion → Concept (testedIn)
  Resource → Concept (taughtVia)
"""

import json
import logging
from typing import Optional

logger = logging.getLogger("curriculumlens.knowledge_graph")


# ── Data Models for KG ──

class CurriculumHierarchy:
    """Represents the parsed curriculum structure."""

    def __init__(
        self,
        course_code: str,
        course_name: str,
        units: list[dict],
    ):
        self.course_code = course_code
        self.course_name = course_name
        self.units = units  # [{name, topics: [{name, concepts: [{name, formulas, algorithms}]}]}]


class KnowledgeGraphService:
    """
    Service for managing the Curriculum Knowledge Graph in Neo4j.

    Key operations:
    - Build CKG from parsed syllabus (auto-construction)
    - Query concept neighborhoods for KG-enhanced retrieval
    - Link PYQ questions to concept nodes
    - Export graph for D3.js visualization
    """

    def __init__(self, neo4j_driver):
        self.driver = neo4j_driver

    # ── Syllabus Parsing (LLM-Guided) ──

    async def parse_syllabus_with_llm(
        self,
        syllabus_text: str,
        course_code: str,
        course_name: str,
    ) -> CurriculumHierarchy:
        """
        Use LLM to extract structured curriculum hierarchy from raw syllabus text.

        Prompt engineering strategy:
        - Provide the LLM with a clear JSON schema for output
        - Use few-shot examples of correct extractions
        - Request confidence scores for ambiguous classifications
        """
        # System prompt for structured extraction
        system_prompt = """You are an academic curriculum analyzer. Given a university course syllabus,
extract the hierarchical structure in the following JSON format:

{
  "units": [
    {
      "name": "Unit Name",
      "unit_number": 1,
      "topics": [
        {
          "name": "Topic Name",
          "concepts": [
            {
              "name": "Concept Name",
              "description": "Brief description",
              "formulas": ["formula1", "formula2"],
              "algorithms": ["algorithm1"],
              "keywords": ["keyword1", "keyword2"]
            }
          ]
        }
      ]
    }
  ]
}

Rules:
- Each unit should have clear, distinct topics
- Each topic should have specific, granular concepts
- Include formulas and algorithms explicitly when mentioned
- Keywords should include alternative names and related terms
- Be comprehensive but not redundant"""

        user_prompt = f"""Course Code: {course_code}
Course Name: {course_name}

Syllabus Text:
{syllabus_text}

Extract the curriculum hierarchy as JSON."""

        # TODO: Call LLM for extraction
        # response = await llm_service.generate(
        #     system_prompt=system_prompt,
        #     user_prompt=user_prompt,
        #     response_format="json",
        # )
        # hierarchy = json.loads(response)

        logger.info("Syllabus parsing for %s will be connected to LLM service", course_code)

        return CurriculumHierarchy(
            course_code=course_code,
            course_name=course_name,
            units=[],
        )

    # ── Graph Construction ──

    async def build_course_graph(self, hierarchy: CurriculumHierarchy) -> dict:
        """
        Create the CKG in Neo4j from parsed curriculum hierarchy.

        Creates nodes: Course, Unit, Topic, Concept, Formula, Algorithm
        Creates edges: belongsTo, prerequisiteOf, relatedTo
        """
        stats = {"nodes_created": 0, "edges_created": 0}

        async with self.driver.session() as session:
            # Create Course node
            await session.run(
                """
                MERGE (c:Course {code: $code})
                SET c.name = $name, c.updated_at = datetime()
                """,
                code=hierarchy.course_code,
                name=hierarchy.course_name,
            )
            stats["nodes_created"] += 1

            for unit in hierarchy.units:
                # Create Unit node
                unit_id = f"{hierarchy.course_code}_U{unit.get('unit_number', 0)}"
                await session.run(
                    """
                    MERGE (u:Unit {uid: $uid})
                    SET u.name = $name, u.unit_number = $unit_number
                    WITH u
                    MATCH (c:Course {code: $course_code})
                    MERGE (u)-[:belongsTo]->(c)
                    """,
                    uid=unit_id,
                    name=unit["name"],
                    unit_number=unit.get("unit_number", 0),
                    course_code=hierarchy.course_code,
                )
                stats["nodes_created"] += 1
                stats["edges_created"] += 1

                for topic in unit.get("topics", []):
                    # Create Topic node
                    topic_id = f"{unit_id}_{topic['name'][:30]}"
                    await session.run(
                        """
                        MERGE (t:Topic {tid: $tid})
                        SET t.name = $name
                        WITH t
                        MATCH (u:Unit {uid: $unit_id})
                        MERGE (t)-[:belongsTo]->(u)
                        """,
                        tid=topic_id,
                        name=topic["name"],
                        unit_id=unit_id,
                    )
                    stats["nodes_created"] += 1
                    stats["edges_created"] += 1

                    for concept in topic.get("concepts", []):
                        # Create Concept node
                        concept_id = f"{topic_id}_{concept['name'][:30]}"
                        await session.run(
                            """
                            MERGE (co:Concept {cid: $cid})
                            SET co.name = $name,
                                co.description = $description,
                                co.keywords = $keywords
                            WITH co
                            MATCH (t:Topic {tid: $topic_id})
                            MERGE (co)-[:belongsTo]->(t)
                            """,
                            cid=concept_id,
                            name=concept["name"],
                            description=concept.get("description", ""),
                            keywords=concept.get("keywords", []),
                            topic_id=topic_id,
                        )
                        stats["nodes_created"] += 1
                        stats["edges_created"] += 1

                        # Create Formula nodes
                        for formula in concept.get("formulas", []):
                            formula_id = f"{concept_id}_F_{formula[:20]}"
                            await session.run(
                                """
                                MERGE (f:Formula {fid: $fid})
                                SET f.name = $name
                                WITH f
                                MATCH (co:Concept {cid: $concept_id})
                                MERGE (f)-[:belongsTo]->(co)
                                """,
                                fid=formula_id,
                                name=formula,
                                concept_id=concept_id,
                            )
                            stats["nodes_created"] += 1
                            stats["edges_created"] += 1

        logger.info(
            "Built CKG for %s: %d nodes, %d edges",
            hierarchy.course_code,
            stats["nodes_created"],
            stats["edges_created"],
        )
        return stats

    # ── Graph Queries ──

    async def get_concept_context(
        self,
        concept_name: str,
        depth: int = 2,
    ) -> dict:
        """
        Get a concept's full context from the KG:
        - Parent topic, unit, course
        - Related concepts (within depth hops)
        - Prerequisite chain
        - Linked PYQ questions
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (co:Concept)
                WHERE co.name =~ $pattern
                OPTIONAL MATCH (co)-[:belongsTo]->(t:Topic)-[:belongsTo]->(u:Unit)-[:belongsTo]->(c:Course)
                OPTIONAL MATCH (co)-[:prerequisiteOf]->(prereq:Concept)
                OPTIONAL MATCH (co)-[:relatedTo]-(related:Concept)
                OPTIONAL MATCH (co)<-[:testedIn]-(q:PYQQuestion)
                RETURN co, t, u, c,
                       collect(DISTINCT prereq.name) as prerequisites,
                       collect(DISTINCT related.name) as related_concepts,
                       collect(DISTINCT {
                           text: q.question_text,
                           year: q.exam_year,
                           marks: q.marks,
                           difficulty: q.difficulty_level
                       }) as pyq_questions
                LIMIT 1
                """,
                pattern=f"(?i).*{concept_name}.*",
            )

            record = await result.single()
            if not record:
                return {"found": False, "concept": concept_name}

            return {
                "found": True,
                "concept": record["co"]["name"],
                "topic": record["t"]["name"] if record["t"] else None,
                "unit": record["u"]["name"] if record["u"] else None,
                "course": record["c"]["code"] if record["c"] else None,
                "prerequisites": record["prerequisites"],
                "related_concepts": record["related_concepts"],
                "pyq_questions": record["pyq_questions"],
            }

    async def get_graph_for_visualization(
        self,
        course_code: Optional[str] = None,
        depth: int = 3,
    ) -> dict:
        """
        Export the CKG as nodes + edges for D3.js force-directed visualization.
        """
        async with self.driver.session() as session:
            if course_code:
                result = await session.run(
                    """
                    MATCH path = (c:Course {code: $code})<-[:belongsTo*1..4]-(n)
                    UNWIND nodes(path) as node
                    UNWIND relationships(path) as rel
                    RETURN collect(DISTINCT {
                        id: elementId(node),
                        label: labels(node)[0],
                        name: coalesce(node.name, node.code, 'Unknown')
                    }) as nodes,
                    collect(DISTINCT {
                        source: elementId(startNode(rel)),
                        target: elementId(endNode(rel)),
                        type: type(rel)
                    }) as edges
                    """,
                    code=course_code,
                )
            else:
                result = await session.run(
                    """
                    MATCH (n)
                    OPTIONAL MATCH (n)-[r]-(m)
                    RETURN collect(DISTINCT {
                        id: elementId(n),
                        label: labels(n)[0],
                        name: coalesce(n.name, n.code, 'Unknown')
                    }) as nodes,
                    collect(DISTINCT {
                        source: elementId(startNode(r)),
                        target: elementId(endNode(r)),
                        type: type(r)
                    }) as edges
                    """,
                )

            record = await result.single()
            return {
                "nodes": record["nodes"] if record else [],
                "edges": record["edges"] if record else [],
            }

    # ── PYQ Linking ──

    async def link_pyq_to_concept(
        self,
        question_id: str,
        concept_name: str,
        confidence: float = 1.0,
    ) -> bool:
        """Link a PYQ question to a concept node in the KG."""
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (co:Concept)
                WHERE co.name =~ $pattern
                MERGE (q:PYQQuestion {qid: $qid})
                MERGE (q)-[r:testedIn]->(co)
                SET r.confidence = $confidence
                RETURN co.name as concept
                """,
                pattern=f"(?i).*{concept_name}.*",
                qid=question_id,
                confidence=confidence,
            )
            record = await result.single()
            return record is not None
