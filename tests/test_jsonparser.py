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
        "id": "FAM-03",
        "topic": "Child access",
        "category": "Family Law",
        "variations": ["My ex won't let me see my kids"],
        "user_query": "I want custody.",
    }
    prefix = golden_set_retrieval_prefix(obj)
    assert prefix.startswith("Golden Set entry FAM-03.")
    assert "My ex won't let me see my kids" in prefix


@pytest.mark.asyncio
async def test_jsonparser_golden_array_prepends_cues():
    payload = (
        '[{"id": "FAM-03", "topic": "T", "variations": ["seen kids"],'
        ' "user_query": "uq", "x": 1}]'
    )
    file = io.StringIO(payload)
    file.name = "pbsg_golden_set_complete_v2.json"
    jsonparser = JsonParser()
    pages = [page async for page in jsonparser.parse(file)]
    assert len(pages) == 1
    assert "Golden Set entry FAM-03." in pages[0].text
    assert "seen kids" in pages[0].text
    assert '"id": "FAM-03"' in pages[0].text
