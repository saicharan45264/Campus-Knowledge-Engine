import pytest

from backend.services import topic_mapper


@pytest.mark.asyncio
async def test_mapper_returns_confirmed_suggestion_and_unmapped_without_llm(monkeypatch):
    async def fake_embed_texts(texts, batch_size=24):
        vectors = []
        for text in texts:
            value = text.casefold()
            vectors.append([1.0, 0.0] if "relational algebra" in value or "what is the concept?" in value else [0.0, 1.0])
        return vectors

    monkeypatch.setattr(topic_mapper, "embed_texts", fake_embed_texts)
    syllabus = "\n".join([
        "[T1] Unit: Unit 1 | Topic: Relational Algebra",
        "[T2] Unit: Unit 2 | Topic: Normalization",
    ])
    questions = [
        {"question_text": "Explain relational algebra."},
        {"question_text": "What is the concept?"},
        {"question_text": ""},
    ]

    result = await topic_mapper.semantic_map_questions_to_topics("23CSE202", syllabus, questions)

    assert result[0]["status"] == "mapped"
    assert result[0]["mappings"][0]["topic_id"] == "T1"
    assert result[1]["status"] == "provisional"
    assert result[1]["suggestions"][0]["topic_id"] == "T1"
    assert result[2]["status"] == "unmapped"
    assert all("mapping_method" in candidate for item in result.values() for candidate in item["candidates"])
