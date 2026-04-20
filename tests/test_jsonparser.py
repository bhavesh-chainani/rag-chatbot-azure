import io

import pytest

from prepdocslib.jsonparser import JsonParser, golden_set_retrieval_prefix


@pytest.mark.asyncio
async def test_jsonparser_single_obj():
    file = io.StringIO('{"test": "test"}')
    file.name = "test.json"
    jsonparser = JsonParser()
    pages = [page async for page in jsonparser.parse(file)]
    assert len(pages) == 1
    assert pages[0].page_num == 0
    assert pages[0].offset == 0
    assert pages[0].text == '{"test": "test"}'


@pytest.mark.asyncio
async def test_jsonparser_array_multiple_obj():
    file = io.StringIO('[{"test1": "test"},{"test2": "test"}]')
    file.name = "test.json"
    jsonparser = JsonParser()
    pages = [page async for page in jsonparser.parse(file)]
    assert len(pages) == 2
    assert pages[0].page_num == 0
    assert pages[0].offset == 1
    assert pages[0].text == '{"test1": "test"}'
    assert pages[1].page_num == 1
    assert pages[1].offset == 19
    assert pages[1].text == '{"test2": "test"}'


def test_golden_set_retrieval_prefix_shapes():
    assert golden_set_retrieval_prefix({"id": "not-an-id"}) == ""
    obj = {
        "id": "EMP-01",
        "topic": "Unpaid wages",
        "category": "Employment Law",
        "variations": ["Boss has not paid me for two months"],
        "user_query": "My employer has not paid my salary.",
    }
    prefix = golden_set_retrieval_prefix(obj)
    assert prefix.startswith("Golden Set entry EMP-01.")
    assert "Boss has not paid me for two months" in prefix


def test_golden_set_retrieval_prefix_gen3_id():
    obj = {
        "id": "GEN3-T01",
        "topic": "First contact",
        "category": "PBSG Hotline — General Enquiries (v3)",
        "variations": ["New caller, what do I do?"],
        "user_query": "How do I triage?",
    }
    prefix = golden_set_retrieval_prefix(obj)
    assert prefix.startswith("Golden Set entry GEN3-T01.")
    assert "New caller, what do I do?" in prefix


@pytest.mark.asyncio
async def test_jsonparser_golden_array_prepends_cues():
    payload = (
        '[{"id": "EMP-01", "topic": "T", "variations": ["unpaid salary phrasing"],' ' "user_query": "uq", "x": 1}]'
    )
    file = io.StringIO(payload)
    file.name = "EMP-01.json"
    jsonparser = JsonParser()
    pages = [page async for page in jsonparser.parse(file)]
    assert len(pages) == 1
    assert "Golden Set entry EMP-01." in pages[0].text
    assert "unpaid salary phrasing" in pages[0].text
    assert '"id": "EMP-01"' in pages[0].text
