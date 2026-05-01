"""
Query relevance router for RAG chatbot.

Checks if the user query is relevant to the bot's scope (e.g. legal enquiries)
before invoking retrieval and the main chat approach. Irrelevant queries
receive a short out-of-scope message to reduce latency and avoid unnecessary
search/LLM calls.
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_SCOPE_DESCRIPTION = "legal enquiries based on our knowledge base"
DEFAULT_OUT_OF_SCOPE_MESSAGE = (
    "I handle legal enquiries based on our knowledge base. "
    "Your question seems outside that scope. Please ask about legal matters I can look up for you."
)

# Short greetings / non-queries we always treat as out-of-scope (no LLM call).
OBVIOUS_NON_QUERIES = frozenset(
    s.strip().lower()
    for s in (
        "hello",
        "hi",
        "hey",
        "yo",
        "sup",
        "hola",
        "good morning",
        "good afternoon",
        "good evening",
        "howdy",
        "greetings",
        "good day",
        "hi there",
        "hello there",
    )
)

# Fast-path terms that strongly indicate an in-scope legal/hotline intake query.
# This reduces false negatives from the lightweight LLM classifier.
OBVIOUS_IN_SCOPE_TERMS = frozenset(
    (
        "legal",
        "lawyer",
        "applicant",
        "triage",
        "representation",
        "criminal",
        "civil",
        "matrimonial",
        "divorce",
        "custody",
        "maintenance",
        "salary",
        "employment",
        "dismiss",
        "wrongful",
        "charged",
        "charge",
        "police",
        "court",
        "probate",
        "estate",
        "lpa",
        "donee",
        "pchi",
        "fjss",
        "clas",
        "pdo",
        "lab",
        "lasco",
        "means test",
        "pro bono",
        "legal aid",
        "pbsg",
    )
)

OBVIOUS_IN_SCOPE_PATTERN = re.compile(
    "|".join(re.escape(term) for term in sorted(OBVIOUS_IN_SCOPE_TERMS, key=len, reverse=True))
)

OUT_OF_SCOPE_THOUGHTS = [
    {
        "title": "Query routed",
        "description": "Question was classified as out of scope; no search was run.",
        "props": None,
    }
]

OUT_OF_SCOPE_CONTEXT = {
    "thoughts": OUT_OF_SCOPE_THOUGHTS,
    "data_points": {},
    "followup_questions": None,
}


def is_obvious_non_query(text: str) -> bool:
    """
    True if the message is clearly not a substantive question (e.g. greeting).
    Used as a fast path to skip the LLM and avoid misclassification.
    """
    if not text or not text.strip():
        return True
    t = text.strip().lower().rstrip("!?.")
    return t in OBVIOUS_NON_QUERIES


def is_obvious_in_scope(text: str) -> bool:
    """
    True if the message clearly looks like an in-scope legal enquiry/intake note.
    """
    if not text or not text.strip():
        return False
    lowered = text.strip().lower()
    return bool(OBVIOUS_IN_SCOPE_PATTERN.search(lowered))


def is_query_relevant(
    *,
    user_message: str,
    client: Optional[Any] = None,
    model: Optional[str] = None,
    deployment: Optional[str] = None,
    scope_description: Optional[str] = DEFAULT_SCOPE_DESCRIPTION,
) -> bool:
    """
    Classify whether the user message is relevant to the bot's scope.

    Uses keyword heuristics only (no LLM call) for maximum speed.
    Messages longer than a few words are assumed in-scope since the triage bot
    is purpose-built and virtually all real usage is in-scope queries.
    """
    if not user_message or not user_message.strip():
        return False
    if is_obvious_in_scope(user_message):
        return True
    # Short messages that aren't greetings and contain 4+ words are likely
    # substantive questions — pass through to RAG without an LLM call.
    word_count = len(user_message.strip().split())
    if word_count >= 4:
        return True
    # Very short (1-3 word) messages that aren't obvious greetings or in-scope
    # keywords: these are rare edge cases. Pass through to avoid blocking legit queries.
    return True


def out_of_scope_response(
    *,
    message: str = DEFAULT_OUT_OF_SCOPE_MESSAGE,
    session_state: Any = None,
) -> dict[str, Any]:
    """
    Build a chat response dict for an out-of-scope query.

    Matches the shape returned by ChatReadRetrieveReadApproach.run_without_streaming
    so the frontend behaves the same.
    """
    return {
        "message": {"content": message, "role": "assistant"},
        "context": OUT_OF_SCOPE_CONTEXT,
        "session_state": session_state,
    }


async def out_of_scope_stream(
    *,
    message: str = DEFAULT_OUT_OF_SCOPE_MESSAGE,
    session_state: Any = None,
):
    """
    Async generator yielding stream events for an out-of-scope response.

    Matches the shape used by ChatReadRetrieveReadApproach.run_with_streaming.
    """
    yield {"delta": {"role": "assistant"}, "context": OUT_OF_SCOPE_CONTEXT, "session_state": session_state}
    yield {"delta": {"content": message, "role": "assistant"}}
    yield {"delta": {"role": "assistant"}, "context": OUT_OF_SCOPE_CONTEXT, "session_state": session_state}
