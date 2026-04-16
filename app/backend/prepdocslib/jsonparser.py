import json
import re
from collections.abc import AsyncGenerator
from typing import IO

from .page import Page
from .parser import Parser

# PBSG Golden Set entries use ids like FAM-03, EMP-01, or GEN3-T01 (General Enquiries).
# Prepend retrieval cues so keyword (BM25) and embeddings align with informal phrasings.
_GOLDEN_ENTRY_ID = re.compile(r"^(?:[A-Z]{3}-\d{2}|GEN3-[A-Z0-9-]+)$")


def golden_set_retrieval_prefix(obj: dict) -> str:
    entry_id = obj.get("id")
    if not isinstance(entry_id, str) or not _GOLDEN_ENTRY_ID.match(entry_id):
        return ""
    parts: list[str] = [f"Golden Set entry {entry_id}."]
    topic = obj.get("topic")
    if isinstance(topic, str) and topic.strip():
        parts.append(f"Topic: {topic.strip()}.")
    category = obj.get("category")
    if isinstance(category, str) and category.strip():
        parts.append(f"Category: {category.strip()}.")
    variations = obj.get("variations")
    if isinstance(variations, list) and variations:
        phrases = [v.strip() for v in variations if isinstance(v, str) and v.strip()]
        if phrases:
            parts.append("User phrasings and query variations: " + " | ".join(phrases) + ".")
    user_query = obj.get("user_query")
    if isinstance(user_query, str) and user_query.strip():
        parts.append(f"Representative user query: {user_query.strip()}.")
    return " ".join(parts) + "\n\n"


class JsonParser(Parser):
    """
    Concrete parser that can parse JSON into Page objects. A top-level object becomes a single Page, while a top-level array becomes multiple Page objects.
    """

    async def parse(self, content: IO) -> AsyncGenerator[Page, None]:
        offset = 0
        data = json.loads(content.read())
        if isinstance(data, list):
            for i, obj in enumerate(data):
                offset += 1  # For opening bracket or comma before object
                page_text = json.dumps(obj)
                if isinstance(obj, dict):
                    page_text = golden_set_retrieval_prefix(obj) + page_text
                yield Page(i, offset, page_text)
                offset += len(page_text)
        elif isinstance(data, dict):
            page_text = json.dumps(data)
            page_text = golden_set_retrieval_prefix(data) + page_text
            yield Page(0, 0, page_text)
