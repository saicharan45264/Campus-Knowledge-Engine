// Keep the canonical TESTS_TOPIC relationship and remove only the legacy
// TESTS edge when the same question-topic pair already has TESTS_TOPIC.
// Safe to rerun; it does not remove a topic mapping that exists only as TESTS.
MATCH (q:Question)-[legacy:TESTS]->(t:Topic)
MATCH (q)-[:TESTS_TOPIC]->(t)
DELETE legacy;
