import base64
import json
from datetime import date

import pytest
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai.types.chat import ChatCompletion, ChatCompletionChunk

import pbsg_triage_state
from approaches.approach import (
    ActivityDetail,
    DataPoints,
    Document,
    ExtraInfo,
    SharePointResult,
    ThoughtStep,
    WebResult,
)
from approaches.chatreadretrieveread import ChatReadRetrieveReadApproach
from approaches.promptmanager import PromptManager
from pbsg_triage_state import (
    PBSGTransition,
    branch_key_for_answer,
    build_triage_state,
    format_state_prompt,
    normalize_simple_answer,
    resolve_expected_transition,
    validate_response_questions,
)
from prepdocslib.embeddings import ImageEmbeddings

from .mocks import (
    MOCK_EMBEDDING_DIMENSIONS,
    MOCK_EMBEDDING_MODEL_NAME,
    MockAsyncSearchResultsIterator,
    mock_retrieval_response,
)


async def mock_search(*args, **kwargs):
    return MockAsyncSearchResultsIterator(kwargs.get("search_text"), kwargs.get("vector_queries"))


async def mock_retrieval(*args, **kwargs):
    return mock_retrieval_response()


def test_get_search_query(chat_approach):
    payload = """
    {
	"id": "chatcmpl-81JkxYqYppUkPtOAia40gki2vJ9QM",
	"object": "chat.completion",
	"created": 1695324963,
	"model": "gpt-4.1-mini",
	"prompt_filter_results": [
		{
			"prompt_index": 0,
			"content_filter_results": {
				"hate": {
					"filtered": false,
					"severity": "safe"
				},
				"self_harm": {
					"filtered": false,
					"severity": "safe"
				},
				"sexual": {
					"filtered": false,
					"severity": "safe"
				},
				"violence": {
					"filtered": false,
					"severity": "safe"
				}
			}
		}
	],
	"choices": [
		{
			"index": 0,
			"finish_reason": "function_call",
			"message": {
				"content": "this is the query",
				"role": "assistant",
				"tool_calls": [
					{
                        "id": "search_sources1235",
						"type": "function",
						"function": {
							"name": "search_sources",
							"arguments": "{\\n\\"search_query\\":\\"accesstelemedicineservices\\"\\n}"
						}
					}
				]
			},
			"content_filter_results": {

			}
		}
	],
	"usage": {
		"completion_tokens": 19,
		"prompt_tokens": 425,
		"total_tokens": 444
	}
}
"""
    default_query = "hello"
    chatcompletions = ChatCompletion.model_validate(json.loads(payload), strict=False)
    query = chat_approach.get_search_query(chatcompletions, default_query)

    assert query == "accesstelemedicineservices"


def test_get_search_query_returns_default(chat_approach):
    payload = '{"id":"chatcmpl-81JkxYqYppUkPtOAia40gki2vJ9QM","object":"chat.completion","created":1695324963,"model":"gpt-4.1-mini","prompt_filter_results":[{"prompt_index":0,"content_filter_results":{"hate":{"filtered":false,"severity":"safe"},"self_harm":{"filtered":false,"severity":"safe"},"sexual":{"filtered":false,"severity":"safe"},"violence":{"filtered":false,"severity":"safe"}}}],"choices":[{"index":0,"finish_reason":"function_call","message":{"content":"","role":"assistant"},"content_filter_results":{}}],"usage":{"completion_tokens":19,"prompt_tokens":425,"total_tokens":444}}'
    default_query = "hello"
    chatcompletions = ChatCompletion.model_validate(json.loads(payload), strict=False)
    query = chat_approach.get_search_query(chatcompletions, default_query)

    assert query == default_query


def test_get_search_query_returns_default_on_error(chat_approach, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_approach, "extract_rewritten_query", explode)

    payload = '{"id":"chatcmpl-1","object":"chat.completion","created":0,"model":"gpt-4.1-mini","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"anything"}}]}'
    chatcompletions = ChatCompletion.model_validate(json.loads(payload), strict=False)

    assert chat_approach.get_search_query(chatcompletions, "default") == "default"


def test_extract_rewritten_query_invalid_json(chat_approach):
    payload = {
        "id": "chatcmpl-2",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [
            {
                "index": 0,
                "finish_reason": "function_call",
                "message": {
                    "role": "assistant",
                    "content": "fallback query",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "search_sources", "arguments": "{not-json"},
                        }
                    ],
                },
            }
        ],
    }
    completion = ChatCompletion.model_validate(payload, strict=False)

    result = chat_approach.extract_rewritten_query(completion, "original", no_response_token=chat_approach.NO_RESPONSE)

    assert result == "fallback query"


def test_extract_followup_questions(chat_approach):
    content = "Here is answer to your question.<<What is the dress code?>>"
    pre_content, followup_questions = chat_approach.extract_followup_questions(content)
    assert pre_content == "Here is answer to your question."
    assert followup_questions == ["What is the dress code?"]


def test_extract_followup_questions_three(chat_approach):
    content = """Here is answer to your question.

<<What are some examples of successful product launches they should have experience with?>>
<<Are there any specific technical skills or certifications required for the role?>>
<<Is there a preference for candidates with experience in a specific industry or sector?>>"""
    pre_content, followup_questions = chat_approach.extract_followup_questions(content)
    assert pre_content == "Here is answer to your question.\n\n"
    assert followup_questions == [
        "What are some examples of successful product launches they should have experience with?",
        "Are there any specific technical skills or certifications required for the role?",
        "Is there a preference for candidates with experience in a specific industry or sector?",
    ]


def test_extract_followup_questions_no_followup(chat_approach):
    content = "Here is answer to your question."
    pre_content, followup_questions = chat_approach.extract_followup_questions(content)
    assert pre_content == "Here is answer to your question."
    assert followup_questions == []


def test_extract_followup_questions_no_pre_content(chat_approach):
    content = "<<What is the dress code?>>"
    pre_content, followup_questions = chat_approach.extract_followup_questions(content)
    assert pre_content == ""
    assert followup_questions == ["What is the dress code?"]


def test_build_quick_reply_from_selected_entry_and_question(chat_approach):
    entry = {
        "id": "GEN3-T01",
        "branching_logic": {
            "Q1": {
                "question": "Is the applicant currently represented by a lawyer on this same matter?",
                "if_yes": "Route A (Already Represented)",
                "if_no": "Proceed to Q2",
                "if_not_sure": "Clarify: ask whether a lawyer has filed documents.",
            }
        },
    }
    extra_info = ExtraInfo(data_points=DataPoints(text=[f"GEN3-T01.json: {json.dumps(entry)}"]))
    content = """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> Q1: "Are you currently represented by a lawyer on this same matter?"
"""

    quick_reply = chat_approach.build_quick_reply(content, extra_info)

    assert quick_reply is not None
    assert quick_reply.entryId == "GEN3-T01"
    assert quick_reply.questionId == "Q1"
    assert quick_reply.mode == "single"
    assert [(option.id, option.label, option.value) for option in quick_reply.options] == [
        ("if_yes", "Yes", "Yes"),
        ("if_no", "No", "No"),
        ("if_not_sure", "Not sure", "Not sure"),
    ]


def test_build_quick_reply_uses_question_specific_labels(chat_approach):
    entry = {
        "id": "GEN3-T01",
        "branching_logic": {
            "Q2": {
                "question": "Is the applicant the person who needs legal help, or are they calling on behalf of someone else?",
                "if_calling_on_behalf_and_able_to_self_help": "Route B",
                "if_self_or_calling_on_behalf_and_unable_to_self_help": "Proceed to Q3",
                "if_not_sure": "Clarify caller capacity.",
            }
        },
    }
    extra_info = ExtraInfo(data_points=DataPoints(text=[f"GEN3-T01.json: {json.dumps(entry)}"]))
    content = """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> Q2: "Are you the person who needs legal help, or are they calling on behalf of someone else?"
"""

    quick_reply = chat_approach.build_quick_reply(content, extra_info)

    assert quick_reply is not None
    assert [(option.id, option.label, option.value) for option in quick_reply.options] == [
        (
            "if_calling_on_behalf_and_able_to_self_help",
            "Calling for someone else; they can contact PBSG directly",
            "Calling for someone else; they can contact PBSG directly",
        ),
        (
            "if_self_or_calling_on_behalf_and_unable_to_self_help",
            "Applicant is calling, or cannot contact PBSG themselves",
            "Applicant is calling, or cannot contact PBSG themselves",
        ),
        ("if_not_sure", "Not sure", "Not sure"),
    ]
    assert (
        branch_key_for_answer(entry["branching_logic"]["Q2"], "Applicant is calling, or cannot contact PBSG themselves")
        == "if_self_or_calling_on_behalf_and_unable_to_self_help"
    )


def test_build_quick_reply_uses_explicit_pending_entry(chat_approach):
    parent_entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q1": {
                "question": "Has the applicant been charged with a capital offence?",
                "if_yes": "Route A (LASCO)",
                "if_no": "Proceed to Q2",
            }
        },
    }
    urgent_entry = {
        "id": "GEN3-T06",
        "branching_logic": {
            "Q1": {
                "question": "Is there an immediate threat to life or safety right now?",
                "if_yes": "Route A (Emergency Services)",
                "if_no": "Proceed to Q2",
                "if_not_sure": "Route A as a precaution",
            }
        },
    }
    extra_info = ExtraInfo(
        data_points=DataPoints(
            text=[
                f"GEN3-T02.json: {json.dumps(parent_entry)}",
                f"GEN3-T06.json: {json.dumps(urgent_entry)}",
            ]
        )
    )
    content = """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> GEN3-T06 Q1: "Is there an immediate threat to your life or physical safety right now?"
"""

    quick_reply = chat_approach.build_quick_reply(content, extra_info)

    assert quick_reply is not None
    assert quick_reply.entryId == "GEN3-T06"
    assert quick_reply.questionId == "Q1"
    assert [option.label for option in quick_reply.options] == ["Yes", "No", "Not sure"]


def test_build_quick_reply_works_for_structured_nested_urgent_card(chat_approach):
    parent_entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q2": {
                "question": "Is there a court date/deadline within 14 days?",
                "if_yes": "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
            }
        },
    }
    urgent_entry = {
        "id": "GEN3-T06",
        "branching_logic": {
            "Q1": {
                "question": "Is there an immediate threat to life or safety right now?",
                "if_yes": "Route A (Emergency Services)",
                "if_no": "Proceed to Q2",
                "if_not_sure": "Route A as a precaution",
            }
        },
    }
    extra_info = ExtraInfo(
        data_points=DataPoints(
            text=[
                f"GEN3-T02.json: {json.dumps(parent_entry)}",
                f"GEN3-T06.json: {json.dumps(urgent_entry)}",
            ]
        )
    )
    content = """**Selected Entry:** GEN3-T02

**Active stream:** GEN3-T06 urgent concurrent path

**Tell the applicant:**

"Your criminal matter may also have an urgent deadline or safety concern."

> **GEN3-T06 Q1: "Is there an immediate threat to your life or physical safety right now?"**
"""

    quick_reply = chat_approach.build_quick_reply(content, extra_info)

    assert quick_reply is not None
    assert quick_reply.entryId == "GEN3-T06"
    assert quick_reply.questionId == "Q1"
    assert [option.label for option in quick_reply.options] == ["Yes", "No", "Not sure"]


def test_normalize_asked_question_text_uses_selected_entry_question(chat_approach):
    criminal_entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q2": {
                "question": "Is there a court date/deadline within 14 days?",
                "if_yes": "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
                "if_no": "Proceed to Q3",
                "if_not_sure": "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
            }
        },
    }
    urgent_entry = {
        "id": "GEN3-T06",
        "branching_logic": {
            "Q3": {
                "question": "Is there a specific legal deadline within 14 days?",
                "if_yes": "Route C",
                "if_no": "Route D",
            }
        },
    }
    extra_info = ExtraInfo(
        data_points=DataPoints(
            text=[
                f"GEN3-T02.json: {json.dumps(criminal_entry)}",
                f"GEN3-T06.json: {json.dumps(urgent_entry)}",
            ]
        )
    )
    content = """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **Q2: "Is there a specific legal deadline within 14 days (court date, immigration deadline, filing deadline, injunction hearing)?"**
"""

    normalized = chat_approach.normalize_asked_question_text(content, extra_info)

    assert normalized is not None
    assert '> **Q2: "Is there a court date/deadline within 14 days?"**' in normalized
    assert "specific legal deadline within 14 days" not in normalized


def test_normalize_asked_question_text_keeps_explicit_nested_entry_question(chat_approach):
    parent_entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q2": {
                "question": "Is there a court date/deadline within 14 days?",
                "if_yes": "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
                "if_no": "Proceed to Q3",
            }
        },
    }
    urgent_entry = {
        "id": "GEN3-T06",
        "branching_logic": {
            "Q1": {
                "question": "Is there an immediate threat to life or physical safety right now?",
                "if_yes": "Route A (Emergency Services)",
                "if_no": "Proceed to Q2",
                "if_not_sure": "Route A as a precaution",
            }
        },
    }
    extra_info = ExtraInfo(
        data_points=DataPoints(
            text=[
                f"GEN3-T02.json: {json.dumps(parent_entry)}",
                f"GEN3-T06.json: {json.dumps(urgent_entry)}",
            ]
        )
    )
    content = """**Selected Entry:** GEN3-T02

Triage progress:
- Last answered: Q2 = Yes → Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)
- Next question: GEN3-T06 Q1 (Urgent concurrent path)

**Ask the applicant (read verbatim):**

> **GEN3-T06 Q1: "Is there an immediate threat to your life or physical safety right now?"**
"""

    normalized = chat_approach.normalize_asked_question_text(content, extra_info)

    assert normalized == content


def test_build_quick_reply_skips_unsupported_walkthrough(chat_approach):
    entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q6": {
                "question": "Does the applicant meet the means criteria?",
                "if_yes": "Route E (CLAS)",
                "if_no": "Proceed to GEN3-T04",
                "if_not_sure": "Walk through PCHI calculation live; if still unclear, take down figures.",
            }
        },
    }
    extra_info = ExtraInfo(data_points=DataPoints(text=[f"GEN3-T02.json: {json.dumps(entry)}"]))
    content = """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> Q6: "Do you meet the means criteria?"
"""

    assert chat_approach.build_quick_reply(content, extra_info) is None


def test_build_quick_reply_returns_none_without_pending_question(chat_approach):
    entry = {
        "id": "GEN3-T01",
        "branching_logic": {
            "Q1": {
                "question": "Is the applicant currently represented?",
                "if_yes": "Route A",
                "if_no": "Proceed to Q2",
            }
        },
    }
    extra_info = ExtraInfo(data_points=DataPoints(text=[f"GEN3-T01.json: {json.dumps(entry)}"]))
    content = """**Selected Entry:** GEN3-T01

**Routing Recommendation:** Route A
"""

    assert chat_approach.build_quick_reply(content, extra_info) is None


def test_pbsg_triage_state_locks_followup_to_pending_question():
    entry = {
        "id": "GEN3-T03",
        "branching_logic": {
            "Q2": {
                "question": "Is the applicant a Singapore Citizen or PR?",
                "if_yes": "Proceed to Q3",
                "if_no_foreigner": "Proceed to Q4",
                "if_not_sure": "Clarify nationality/residency status; if still unclear, Route F.",
            }
        },
    }
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T03

Triage progress:
- Next question: Q2 (SGC/PR path) from GEN3-T03

**Ask the applicant (read verbatim):**

> **Q2: "Are you a Singapore Citizen or PR?"**""",
        }
    ]

    state = build_triage_state(messages, {"GEN3-T03": entry}, "No, foreigner")
    prompt = format_state_prompt(state)

    assert state.workflow_id == "GEN3-T03"
    assert state.workflow_locked is True
    assert state.pending_entry_id == "GEN3-T03"
    assert state.current_question_id == "Q2"
    assert state.allowed_branch_keys == ["if_yes", "if_no_foreigner", "if_not_sure"]
    assert "Workflow locked: GEN3-T03" in prompt
    assert "Pending question: GEN3-T03 Q2" in prompt
    assert "if_no_foreigner" in prompt


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("yeah", "YES"),
        ("correct", "YES"),
        ("i do", "YES"),
        ("nope", "NO"),
        ("don't have", "NO"),
        ("never", "NO"),
        ("not sure", "UNCLEAR"),
        ("I don't know yet", "UNCLEAR"),
        ("my hearing is tomorrow", None),
    ],
)
def test_normalize_simple_answer(answer, expected):
    assert normalize_simple_answer(answer) == expected


def test_pbsg_triage_state_enters_orchestration_without_locked_workflow():
    state = build_triage_state(
        messages=[],
        entries={"GEN3-T01": {"id": "GEN3-T01", "branching_logic": {"Q1": {"question": "Represented?"}}}},
        latest_user_query="Applicant needs help with a divorce and a criminal charge.",
    )
    prompt = format_state_prompt(state)

    assert state.mode == "ORCHESTRATION"
    assert state.workflow_locked is False
    assert "Mode: ORCHESTRATION" in prompt
    assert "Workflow locked: false" in prompt


def test_pbsg_triage_state_enters_fast_routing_with_simple_answer():
    entry = {
        "id": "GEN3-T01",
        "branching_logic": {
            "Q1": {
                "question": "Is the applicant currently represented?",
                "if_yes": "Route A",
                "if_no": "Proceed to Q2",
            }
        },
    }
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> **Q1: "Are you currently represented?"**""",
        }
    ]

    state = build_triage_state(messages, {"GEN3-T01": entry}, "nope")
    prompt = format_state_prompt(state)

    assert state.mode == "FAST_ROUTING"
    assert state.latest_answer_classification == "NO"
    assert state.allowed_transitions == ["if_yes", "if_no"]
    assert "Mode: FAST_ROUTING" in prompt
    assert "Latest answer classification: NO" in prompt
    assert "execute the matching branch without filler" in prompt


def test_pbsg_triage_state_enters_repair_for_nationality_contradiction():
    entry = {
        "id": "GEN3-T04",
        "branching_logic": {
            "Q4": {
                "question": "Does the applicant meet the means criteria?",
                "if_yes": "Route A",
                "if_no": "Route D",
            }
        },
    }
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T04

What I gathered from your description:
- Q1: Singapore Citizen or PR -> Yes [GEN3-T04.json]

**Ask the applicant (read verbatim):**

> **Q4: "Do you meet the means criteria?"**""",
        }
    ]

    state = build_triage_state(messages, {"GEN3-T04": entry}, "Actually I am a foreigner, not a PR.")
    prompt = format_state_prompt(state)

    assert state.mode == "REPAIR"
    assert state.repair_required is True
    assert "nationality/residency changed from SGC/PR to foreigner" in state.contradiction_signals
    assert "Mode: REPAIR" in prompt
    assert "Repair required: true" in prompt
    assert "Invalidate downstream decisions" in prompt


def test_pbsg_triage_state_enters_repair_for_representation_contradiction():
    entry = {
        "id": "GEN3-T01",
        "branching_logic": {
            "Q2": {
                "question": "Is the applicant calling for self?",
                "if_self": "Proceed to Q3",
            }
        },
    }
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

What I gathered from your description:
- Q1: Currently represented -> No [GEN3-T01.json]

**Ask the applicant (read verbatim):**

> **Q2: "Are you calling for yourself?"**""",
        }
    ]

    state = build_triage_state(messages, {"GEN3-T01": entry}, "My lawyer is already handling this case.")

    assert state.mode == "REPAIR"
    assert state.repair_required is True
    assert "representation status changed to existing lawyer" in state.contradiction_signals


def test_pbsg_triage_state_tracks_queued_workflows_and_monitors():
    entries = {
        "GEN3-T01": {"id": "GEN3-T01", "branching_logic": {"Q1": {"question": "Represented?"}}},
        "GEN3-T02": {"id": "GEN3-T02", "branching_logic": {"Q1": {"question": "Capital?"}}},
        "GEN3-T03": {"id": "GEN3-T03", "branching_logic": {"Q1": {"question": "Violence or deadline?"}}},
    }
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

**Topics identified:**
1. GEN3-T01 (First Contact) — active workflow
2. GEN3-T02 (Criminal Stream) — queued workflow
3. GEN3-T03 (Matrimonial Stream) — queued workflow

**Concurrent monitors:** urgency, vulnerability

**Ask the applicant (read verbatim):**

> **Q1: "Are you currently represented?"**""",
        }
    ]

    state = build_triage_state(
        messages,
        entries,
        "The applicant has a court date next week, cannot afford food, and is also getting divorced.",
    )
    prompt = format_state_prompt(state)

    assert state.active_workflow == "GEN3-T01"
    assert state.queued_workflows == ["GEN3-T02", "GEN3-T03"]
    assert "urgency" in state.concurrent_monitors
    assert "safety" in state.concurrent_monitors
    assert "Active workflow: GEN3-T01" in prompt
    assert "Queued workflows: GEN3-T02, GEN3-T03" in prompt
    assert "Only this workflow may ask the next primary question" in prompt
    assert "They may interrupt only for urgency threshold, safety issue, or required escalation" in prompt


def test_resolve_expected_transition_keeps_gen3_t02_q3_yes_local():
    entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q3": {
                "question": "Has the applicant been charged in court?",
                "if_yes": "Proceed to Q4",
                "if_no": "Proceed to GEN3-T04 — Civil and Guidance Stream Triage",
                "if_not_sure": "Route F (Escalate to PBSG Staff)",
            }
        },
        "routing": ["Route F (Escalate to PBSG Staff): Take down details."],
    }
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **Q3: "Have you been charged in court?"**""",
        }
    ]
    state = build_triage_state(messages, {"GEN3-T02": entry}, "Yes")

    transition = resolve_expected_transition({"GEN3-T02": entry}, state, "Yes")

    assert transition == PBSGTransition(
        entry_id="GEN3-T02",
        question_id="Q3",
        branch_key="if_yes",
        outcome="Proceed to Q4",
        transition_type="proceed_question",
        target_entry_id="GEN3-T02",
        target_question_id="Q4",
    )


def test_resolve_expected_transition_detects_gen3_t02_nested_urgent_route():
    entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q2": {
                "question": "Is there a court date/deadline within 14 days?",
                "if_yes": "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
                "if_no": "Proceed to Q3",
            }
        },
        "routing": [
            "Route D (CLAS + Urgent Concurrent): Proceed to GEN3-T06, following which, regardless of triage outcome of GEN3-T06, proceed to Q3 of GEN3-T02. Present both triaging outcomes to applicant."
        ],
    }
    urgent_entry = {"id": "GEN3-T06", "branching_logic": {"Q1": {"question": "Immediate safety?"}}}
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **Q2: "Is there a court date/deadline within 14 days?"**""",
        }
    ]
    entries = {"GEN3-T02": entry, "GEN3-T06": urgent_entry}
    state = build_triage_state(messages, entries, "Yes")

    transition = resolve_expected_transition(entries, state, "Yes")

    assert transition is not None
    assert transition.transition_type == "nested_stream"
    assert transition.route_label == "Route D"
    assert transition.target_entry_id == "GEN3-T06"
    assert transition.target_question_id == "Q1"
    assert transition.resume_entry_id == "GEN3-T02"
    assert transition.resume_question_id == "Q3"


def test_apply_triage_response_guard_repairs_gen3_t02_q3_yes_wrong_urgent_route(chat_approach):
    criminal_entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q3": {
                "question": "Has the applicant been charged in court?",
                "if_yes": "Proceed to Q4",
                "if_no": "Proceed to GEN3-T04 — Civil and Guidance Stream Triage",
            },
            "Q4": {
                "question": "Is the applicant a Singapore Citizen or PR?",
                "if_yes": "Proceed to Q5",
                "if_no_foreigner": "Proceed to Q6",
            },
        },
        "routing": [
            "Route D (CLAS + Urgent Concurrent): Proceed to GEN3-T06, following which, regardless of triage outcome of GEN3-T06, proceed to Q3 of GEN3-T02."
        ],
    }
    urgent_entry = {
        "id": "GEN3-T06",
        "branching_logic": {"Q1": {"question": "Is there an immediate threat to the applicant's safety?"}},
    }
    extra_info = ExtraInfo(
        data_points=DataPoints(
            text=[
                f"GEN3-T02.json: {json.dumps(criminal_entry)}",
                f"GEN3-T06.json: {json.dumps(urgent_entry)}",
            ]
        ),
        deterministic_transition=PBSGTransition(
            entry_id="GEN3-T02",
            question_id="Q3",
            branch_key="if_yes",
            outcome="Proceed to Q4",
            transition_type="proceed_question",
            target_entry_id="GEN3-T02",
            target_question_id="Q4",
        ),
    )
    content = """**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)

Next: GEN3-T06 Q1 — "Is there an immediate threat to your safety right now?"
"""

    guarded = chat_approach.apply_triage_response_guard(content, extra_info)

    assert guarded is not None
    assert "**Selected Entry:** GEN3-T02" in guarded
    assert "Routing Recommendation" not in guarded
    assert "Next question: Q4 from GEN3-T02" in guarded
    assert '> **Q4: "Are you a Singapore Citizen or PR?"**' in guarded
    assert "GEN3-T06" not in guarded


def test_apply_triage_response_guard_accepts_expected_nested_gen3_t06_question(chat_approach):
    criminal_entry = {
        "id": "GEN3-T02",
        "branching_logic": {
            "Q2": {
                "question": "Is there a court date/deadline within 14 days?",
                "if_yes": "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
            }
        },
    }
    urgent_entry = {
        "id": "GEN3-T06",
        "branching_logic": {"Q1": {"question": "Is there an immediate threat to the applicant's safety?"}},
    }
    extra_info = ExtraInfo(
        data_points=DataPoints(
            text=[
                f"GEN3-T02.json: {json.dumps(criminal_entry)}",
                f"GEN3-T06.json: {json.dumps(urgent_entry)}",
            ]
        ),
        deterministic_transition=PBSGTransition(
            entry_id="GEN3-T02",
            question_id="Q2",
            branch_key="if_yes",
            outcome="Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)",
            transition_type="nested_stream",
            target_entry_id="GEN3-T06",
            target_question_id="Q1",
            route_label="Route D",
            nested_entry_id="GEN3-T06",
            resume_entry_id="GEN3-T02",
            resume_question_id="Q3",
        ),
    )
    content = """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **GEN3-T06 Q1: "Is there an immediate threat to your safety right now?"**"""

    guarded = chat_approach.apply_triage_response_guard(content, extra_info)

    assert guarded == content


def test_validate_response_questions_allows_explicit_nested_entry_question():
    entries = {
        "GEN3-T02": {"id": "GEN3-T02", "branching_logic": {"Q2": {"question": "Deadline?"}}},
        "GEN3-T06": {"id": "GEN3-T06", "branching_logic": {"Q1": {"question": "Immediate safety?"}}},
    }
    content = """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **GEN3-T06 Q1: "Is there an immediate threat to your safety right now?"**"""

    is_valid, reason = validate_response_questions(content, entries)

    assert is_valid is True
    assert reason is None


def test_apply_triage_response_guard_escalates_invalid_question(chat_approach):
    entry = {
        "id": "GEN3-T04",
        "branching_logic": {
            "Q1": {
                "question": "Is the applicant a Singapore Citizen or PR?",
                "if_yes": "Proceed to Q2",
            }
        },
        "routing": [
            "Route C (Escalate to PBSG Staff): Take down details and email PBSG Staff on the same day."
        ],
    }
    extra_info = ExtraInfo(data_points=DataPoints(text=[f"GEN3-T04.json: {json.dumps(entry)}"]))
    content = """**Selected Entry:** GEN3-T04

**Ask the applicant (read verbatim):**

> **Q5: "What type of matter is this?"**"""

    guarded = chat_approach.apply_triage_response_guard(content, extra_info)

    assert guarded is not None
    assert "**Selected Entry:** GEN3-T04" in guarded
    assert "Route C (Escalate to PBSG Staff)" in guarded
    assert "generated transition could not be verified" in guarded
    assert "Q5" not in guarded


def test_apply_triage_response_guard_replaces_duplicate_terminal_route_with_canonical_card(chat_approach):
    entry = chat_approach.pbsg_golden_set_entries["GEN3-T02"]
    transition = chat_approach.pbsg_routing_engine.graph.transition_for("GEN3-T02", "Q1", "if_yes")
    extra_info = ExtraInfo(
        data_points=chat_approach.golden_set_data_points({"GEN3-T02": entry}),
        deterministic_transition=transition,
    )
    content = """**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "First model route card."

**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "Repeated model route card."
"""

    guarded = chat_approach.apply_triage_response_guard(content, extra_info)

    assert guarded is not None
    assert guarded.count("**Selected Entry:**") == 1
    assert guarded.count("**Routing Recommendation:**") == 1
    assert "**Routing Recommendation:** Route A (LASCO)" in guarded
    assert "First model route card" not in guarded
    assert "Repeated model route card" not in guarded
    assert "LASCO handles capital offences" in guarded


def test_validate_response_questions_rejects_multiple_primary_questions():
    entries = {
        "GEN3-T01": {
            "id": "GEN3-T01",
            "branching_logic": {
                "Q1": {"question": "Represented?"},
                "Q2": {"question": "Calling for self?"},
            },
        }
    }
    content = """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> **Q1: "Are you represented?"**
> **Q2: "Are you calling for yourself?"**"""

    is_valid, reason = validate_response_questions(content, entries)

    assert is_valid is False
    assert reason == "response contains more than one primary triage question"


@pytest.mark.asyncio
async def test_run_without_streaming_routes_obvious_capital_offence_without_llm(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("obvious capital offence should not call retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "user",
            "content": "Applicant has been charged for murder. Their court date is next week. What should they do?",
        }
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")
    content = result["message"]["content"]

    assert result["session_state"] == "session-1"
    assert content.count("**Selected Entry:**") == 1
    assert content.count("**Routing Recommendation:**") == 1
    assert "**Selected Entry:** GEN3-T02" in content
    assert "**Routing Recommendation:** Route A (LASCO)" in content
    assert "GEN3-T06 Q1" not in content
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T02"
    assert result["context"]["thoughts"][-1].description == "Answered from Golden Set branching logic without retrieval or LLM generation."


@pytest.mark.asyncio
async def test_run_without_streaming_defaults_vague_initial_turn_to_gen3_t01(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("vague first turn should not call retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "I need help"}],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]

    assert "**Selected Entry:** GEN3-T01" in content
    assert "Next question: Q1 from GEN3-T01" in content
    assert "Are you currently represented by a lawyer" in content
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"


@pytest.mark.asyncio
async def test_run_without_streaming_skips_initial_topic_llm_for_bare_greeting(chat_approach, monkeypatch):
    async def fail_if_classifier_called(*args, **kwargs):
        raise AssertionError("bare greeting should not call the initial topic classifier")

    async def fail_if_retrieval_called(*args, **kwargs):
        raise AssertionError("bare greeting should stay on deterministic first-contact triage")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_classifier_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_retrieval_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "hi"}],
        {},
        {},
        session_state="session-1",
    )

    assert "**Selected Entry:** GEN3-T01" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_entry_id"),
    [
        ("Applicant has a criminal charge in court.", "GEN3-T02"),
        ("Applicant wants a divorce and custody advice.", "GEN3-T03"),
        ("Applicant's employer has not paid salary for three months.", "GEN3-T04"),
    ],
)
async def test_run_without_streaming_selects_clear_initial_topic(chat_approach, monkeypatch, query, expected_entry_id):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("clear first turn should render deterministic first question")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": query}],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]

    assert f"**Selected Entry:** {expected_entry_id}" in content
    assert f"Next question: Q1 from {expected_entry_id}" in content
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == expected_entry_id


@pytest.mark.asyncio
async def test_run_without_streaming_initial_topic_llm_handles_ambiguous_criminal_and_matrimonial(
    chat_approach, monkeypatch
):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("validated topic classifier result should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "primary_entry_id": "GEN3-T02",
            "queued_entry_ids": ["GEN3-T03"],
            "monitor_entry_ids": [],
            "confidence": 0.91,
            "evidence": "stolen/caught by law indicates criminal issue; separate from husband indicates matrimonial issue",
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [
            {
                "role": "user",
                "content": (
                    "user wants to separate from her husband after he stole something in the past "
                    "and got caught by law"
                ),
            }
        ],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]
    triage_state = result["context"]["pbsg_triage_state"]

    assert "**Selected Entry:** GEN3-T02" in content
    assert "Topics identified:" in content
    assert "GEN3-T03" in content
    assert "Next question: Q1 from GEN3-T02" in content
    assert triage_state["active_workflow"] == "GEN3-T02"
    assert triage_state["queued_workflows"] == ["GEN3-T03"]
    assert result["context"]["thoughts"][-1].title == "Structured PBSG initial topic classifier"


@pytest.mark.asyncio
async def test_run_without_streaming_initial_topic_llm_carries_family_violence_into_matrimonial_q1(
    chat_approach, monkeypatch
):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("validated topic classifier result should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json_sequence(
        [
            {
                "primary_entry_id": "GEN3-T03",
                "queued_entry_ids": [],
                "monitor_entry_ids": ["GEN3-T06", "GEN3-T13"],
                "confidence": 0.92,
                "evidence": "ending relationship indicates matrimonial issue",
            },
            {
                "answered_questions": [
                    {
                        "entry_id": "GEN3-T03",
                        "question_id": "Q1",
                        "branch_key": "if_yes",
                        "confidence": 0.94,
                        "evidence": "husband is beating her up",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [
            {
                "role": "user",
                "content": "caller has mentioned that her husband is beating her up and wants to end their relationship",
            }
        ],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]
    triage_state = result["context"]["pbsg_triage_state"]

    assert "**Selected Entry:** GEN3-T03" in content
    assert "What I gathered from your description:" in content
    assert "Q1: Yes" in content
    assert "**Active stream:** GEN3-T06 urgent concurrent path" in content
    assert "GEN3-T06 Q1" in content
    assert 'Is there active or recent family violence' not in content
    assert triage_state["active_workflow"] == "GEN3-T06"
    assert triage_state["parent_workflow"] == "GEN3-T03"
    assert triage_state["resume_question_id"] == "Q2"
    assert result["context"]["thoughts"][-1].title == "Structured PBSG initial answer extractor"


@pytest.mark.asyncio
async def test_run_without_streaming_ignores_invalid_initial_answer_extraction(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("validated topic classifier result should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json_sequence(
        [
            {
                "primary_entry_id": "GEN3-T03",
                "queued_entry_ids": [],
                "monitor_entry_ids": [],
                "confidence": 0.92,
                "evidence": "ending relationship indicates matrimonial issue",
            },
            {
                "answered_questions": [
                    {
                        "entry_id": "GEN3-T03",
                        "question_id": "Q1",
                        "branch_key": "if_invalid",
                        "confidence": 0.94,
                        "evidence": "unsupported branch key",
                    }
                ]
            },
        ]
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "caller wants to end her relationship with her husband"}],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]

    assert "**Selected Entry:** GEN3-T03" in content
    assert "Next question: Q1 from GEN3-T03" in content
    assert 'Is there active or recent family violence' in content
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T03"


@pytest.mark.asyncio
async def test_run_without_streaming_falls_back_when_initial_topic_llm_returns_invalid_id(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("invalid topic classifier output should fall back to deterministic routing")

    chat_approach.openai_client = fake_openai_client_with_json(
        {"primary_entry_id": "GEN3-UNKNOWN", "queued_entry_ids": [], "monitor_entry_ids": [], "confidence": 0.95}
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Applicant has a criminal charge in court."}],
        {},
        {},
        session_state="session-1",
    )

    assert "**Selected Entry:** GEN3-T02" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T02"


@pytest.mark.asyncio
async def test_run_without_streaming_falls_back_when_initial_topic_llm_fails(chat_approach, monkeypatch):
    async def fail_classifier(*args, **kwargs):
        raise RuntimeError("classifier unavailable")

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("failed topic classifier should fall back to deterministic routing")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_classifier)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Applicant has a criminal charge in court."}],
        {},
        {},
        session_state="session-1",
    )

    assert "**Selected Entry:** GEN3-T02" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T02"


@pytest.mark.asyncio
async def test_run_without_streaming_initial_multi_topic_queues_secondary_topic(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("clear multi-topic first turn should stay deterministic")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    monkeypatch.setattr(pbsg_triage_state, "current_local_date", lambda: date(2026, 5, 24))

    result = await chat_approach.run_without_streaming(
        [
            {
                "role": "user",
                "content": (
                    "Applicant says she's a criminal. She also wants to divorce her husband. "
                    "She's a Singapore Citizen and will be charged on 28th May 2026."
                ),
            }
        ],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]
    triage_state = result["context"]["pbsg_triage_state"]

    assert "**Selected Entry:** GEN3-T02" in content
    assert "Topics identified:" in content
    assert "GEN3-T03" in content
    assert "GEN3-T06 noted as a monitor, not the active workflow" in content
    assert "topic signals were weak or ambiguous" not in content
    assert "Next question: Q1 from GEN3-T02" in content
    assert triage_state["active_workflow"] == "GEN3-T02"
    assert triage_state["queued_workflows"] == ["GEN3-T03"]
    assert "GEN3-T06" in triage_state["triggered_overlays"]
    assert "urgency" in triage_state["concurrent_monitors"]


@pytest.mark.asyncio
async def test_run_without_streaming_keeps_vulnerability_as_overlay_when_legal_issue_exists(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("vulnerability overlay should still render deterministic first question")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Elderly applicant is confused and has a debt issue."}],
        {},
        {},
        session_state="session-1",
    )
    content = result["message"]["content"]

    assert "**Selected Entry:** GEN3-T04" in content
    assert "GEN3-T13 noted as a monitor, not the active workflow" in content
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T04"
    assert "GEN3-T13" in result["context"]["pbsg_triage_state"]["triggered_overlays"]


@pytest.mark.asyncio
async def test_initial_topic_source_pack_injects_default_t01_over_high_ranked_t13(chat_approach):
    data_points = DataPoints(
        text=[f"GEN3-T13.json: {json.dumps(chat_approach.pbsg_golden_set_entries['GEN3-T13'])}"],
        citations=["GEN3-T13.json"],
    )

    chat_approach.ensure_initial_topic_sources(data_points, [{"role": "user", "content": "start triage"}], "start triage")
    entries = chat_approach.extract_golden_set_entries(data_points.text)

    assert "GEN3-T01" in entries
    assert "GEN3-T13" in entries
    assert "GEN3-T01.json" in data_points.citations


@pytest.mark.asyncio
async def test_initial_topic_source_pack_injects_queued_and_monitor_entries(chat_approach, monkeypatch):
    monkeypatch.setattr(pbsg_triage_state, "current_local_date", lambda: date(2026, 5, 24))
    data_points = DataPoints(text=[], citations=[])
    query = (
        "Applicant says she's a criminal. She also wants to divorce her husband. "
        "She's a Singapore Citizen and will be charged on 28th May 2026."
    )

    chat_approach.ensure_initial_topic_sources(data_points, [{"role": "user", "content": query}], query)
    entries = chat_approach.extract_golden_set_entries(data_points.text)

    assert {"GEN3-T02", "GEN3-T03", "GEN3-T06"}.issubset(entries)
    assert {"GEN3-T02.json", "GEN3-T03.json", "GEN3-T06.json"}.issubset(set(data_points.citations))


@pytest.mark.asyncio
async def test_run_until_final_call_returns_canonical_route_before_final_llm(chat_approach, monkeypatch):
    async def fake_run_search_approach(messages, overrides, auth_claims):
        return ExtraInfo(
            data_points=chat_approach.golden_set_data_points(
                {"GEN3-T02": chat_approach.pbsg_golden_set_entries["GEN3-T02"]}
            ),
            thoughts=[],
        )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("proved terminal route should not call final LLM")

    monkeypatch.setattr(chat_approach, "run_search_approach", fake_run_search_approach)
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **Q1: "Is the offence a capital offence (punishable with death)?"**""",
        },
        {"role": "user", "content": "Yes"},
    ]

    extra_info, chat_coroutine = await chat_approach.run_until_final_call(messages, {}, {}, should_stream=False)
    response = await chat_coroutine
    content = response.choices[0].message.content or ""

    assert extra_info.deterministic_transition is not None
    assert content.count("**Selected Entry:**") == 1
    assert content.count("**Routing Recommendation:**") == 1
    assert "**Routing Recommendation:** Route A (LASCO)" in content
    assert extra_info.thoughts[-1].title == "Deterministic PBSG routing"


@pytest.mark.asyncio
async def test_run_without_streaming_uses_deterministic_fast_path_for_locked_flow(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("locked deterministic flow should not call retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **Q1: "Is the offence a capital offence (punishable with death)?"**""",
        },
        {"role": "user", "content": "No"},
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    assert result["session_state"] == "session-1"
    assert result["message"]["content"].startswith("**Selected Entry:** GEN3-T02")
    assert "Next question: Q2 from GEN3-T02" in result["message"]["content"]
    assert "court date/deadline within 14 days" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["mode"] == "FAST_ROUTING"
    assert result["context"]["quick_reply"]["entryId"] == "GEN3-T02"
    assert result["context"]["quick_reply"]["questionId"] == "Q2"


@pytest.mark.asyncio
async def test_run_without_streaming_uses_structured_llm_fallback_for_complex_locked_answer(
    chat_approach, monkeypatch
):
    class FakeCompletions:
        async def create(self, **kwargs):
            return ChatCompletion.model_validate(
                {
                    "id": "classification",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4.1-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {"classification": "branch", "branch_key": "if_no_foreigner", "confidence": 0.9}
                                ),
                            },
                        }
                    ],
                },
                strict=False,
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAIClient:
        chat = FakeChat()

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("structured locked fallback should not run retrieval")

    chat_approach.openai_client = FakeOpenAIClient()
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T03

**Ask the applicant (read verbatim):**

> **Q2: "Is the applicant a Singapore Citizen or PR?"**""",
        },
        {"role": "user", "content": "She is here on a work permit."},
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    assert result["message"]["content"].startswith("**Selected Entry:** GEN3-T03")
    assert "Next question: Q4 from GEN3-T03" in result["message"]["content"]
    assert "Singaporean child" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["pending_entry_id"] == "GEN3-T03"


def fake_openai_client_with_json(payload):
    class FakeCompletions:
        async def create(self, **kwargs):
            return ChatCompletion.model_validate(
                {
                    "id": "classification",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4.1-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(payload)},
                        }
                    ],
                },
                strict=False,
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAIClient:
        chat = FakeChat()

    return FakeOpenAIClient()


def fake_openai_client_with_json_sequence(payloads):
    class FakeCompletions:
        def __init__(self):
            self.index = 0

        async def create(self, **kwargs):
            payload = payloads[min(self.index, len(payloads) - 1)]
            self.index += 1
            return ChatCompletion.model_validate(
                {
                    "id": "classification",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4.1-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(payload)},
                        }
                    ],
                },
                strict=False,
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAIClient:
        chat = FakeChat()

    return FakeOpenAIClient()


def gen3_t04_q4_messages(user_content):
    return [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T04

**Ask the applicant (read verbatim):**

> **Q4: "(Means) Is the applicant's Per Capita Household Income (PCHI) ≤ S$5,000, does the applicant have savings of ≤ $10,000 if younger than 60 years old (or ≤ $40,000 if 60 years old or older), and does the applicant stay in non-private housing (e.g. HDB, shelter, remand/prison)?"**""",
        },
        {"role": "user", "content": user_content},
    ]


def gen3_t02_q1_with_queued_divorce_messages(user_content):
    return [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

Triage progress:

- Topic resolved: criminal [GEN3-T02.json]
- Next question: Q1 from GEN3-T02

Topics identified:
1. GEN3-T02 — active workflow
2. GEN3-T03 — queued workflow (noted from: divorce)

**Ask the applicant (read verbatim):**

> **Q1: "Is the offence a capital offence (punishable with death)?"**""",
        },
        {"role": "user", "content": user_content},
    ]


@pytest.mark.asyncio
async def test_local_side_enquiry_answers_glossary_and_preserves_routing(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("known glossary side enquiry should not run retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("What is PCHI?"),
        {},
        {},
        session_state="session-1",
    )

    content = result["message"]["content"]
    assert "PCHI means per capita household income" in content
    assert "Current question remains: Q4 from GEN3-T04" in content
    assert result["context"]["pbsg_triage_state"]["active_side_enquiry"]["question"] == "What is PCHI?"
    assert result["context"]["pbsg_triage_state"]["pending_entry_id"] == "GEN3-T04"
    assert result["context"]["pbsg_triage_state"]["current_question_id"] == "Q4"


@pytest.mark.asyncio
async def test_terminal_route_with_queued_topic_returns_continue_button(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("terminal route with queued topic should stay deterministic")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t02_q1_with_queued_divorce_messages("Yes"),
        {},
        {},
        session_state="session-1",
    )

    content = result["message"]["content"]
    assert "**Routing Recommendation:** Route A" in content
    assert "**Queued topic ready:**" in content
    assert "GEN3-T03" in result["context"]["pbsg_triage_state"]["queued_workflows"]
    assert result["context"]["pbsg_triage_state"]["routing_completion_status"] == "awaiting_topic_resolution"
    assert result["context"]["quick_reply"]["questionId"] == "CONTINUE"
    assert result["context"]["quick_reply"]["options"][0]["id"] == "continue_queued_workflow:GEN3-T03"


@pytest.mark.asyncio
async def test_ack_after_terminal_route_does_not_start_queued_topic(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("route-complete acknowledgement should not run retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    routed = await chat_approach.run_without_streaming(
        gen3_t02_q1_with_queued_divorce_messages("Yes"),
        {},
        {},
        session_state="session-1",
    )
    messages = [
        *gen3_t02_q1_with_queued_divorce_messages("Yes"),
        {"role": "assistant", "content": routed["message"]["content"]},
        {"role": "user", "content": "ok"},
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    content = result["message"]["content"]
    assert "GEN3-T02 has been routed" in content
    assert "GEN3-T03" in result["context"]["pbsg_triage_state"]["queued_workflows"]
    assert result["context"]["quick_reply"]["questionId"] == "CONTINUE"
    assert "Have you applied to the Legal Aid Bureau" not in content


@pytest.mark.asyncio
async def test_continue_queued_topic_starts_at_q1_without_validated_facts(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("queued workflow continuation should not run retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    routed = await chat_approach.run_without_streaming(
        gen3_t02_q1_with_queued_divorce_messages("Yes"),
        {},
        {},
        session_state="session-1",
    )
    messages = [
        *gen3_t02_q1_with_queued_divorce_messages("Yes"),
        {"role": "assistant", "content": routed["message"]["content"]},
        {"role": "user", "content": "Continue queued workflow: GEN3-T03"},
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    content = result["message"]["content"]
    assert "**Selected Entry:** GEN3-T03" in content
    assert "Next question: Q1 from GEN3-T03" in content
    assert "family violence" in content
    assert "Have you applied to the Legal Aid Bureau" not in content
    assert result["context"]["pbsg_triage_state"]["pending_entry_id"] == "GEN3-T03"
    assert result["context"]["pbsg_triage_state"]["current_question_id"] == "Q1"


@pytest.mark.asyncio
async def test_known_singapore_citizen_skips_downstream_residency_question(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("known residency should be reused deterministically")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    initial = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Applicant is a Singapore Citizen and wants help with divorce."}],
        {},
        {},
        session_state="session-1",
    )
    result = await chat_approach.run_without_streaming(
        [
            {"role": "user", "content": "Applicant is a Singapore Citizen and wants help with divorce."},
            {"role": "assistant", "content": initial["message"]["content"]},
            {"role": "user", "content": "No urgency or family violence."},
        ],
        {},
        {},
        session_state="session-1",
    )

    content = result["message"]["content"]
    assert "Carried over from" in content
    assert "Singapore Citizen or PR" in content
    assert "Next question: Q3 from GEN3-T03" in content
    assert "Are you a Singapore Citizen or PR?" not in content


@pytest.mark.asyncio
async def test_work_permit_fact_reused_across_matrimonial_to_civil_handoff(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("known work permit/residency should be reused across workflows")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    initial_query = "Applicant is on a work permit and wants help with divorce."
    first = await chat_approach.run_without_streaming(
        [{"role": "user", "content": initial_query}],
        {},
        {},
        session_state="session-1",
    )
    second = await chat_approach.run_without_streaming(
        [
            {"role": "user", "content": initial_query},
            {"role": "assistant", "content": first["message"]["content"]},
            {"role": "user", "content": "No urgency or family violence."},
        ],
        {},
        {},
        session_state="session-1",
    )
    third = await chat_approach.run_without_streaming(
        [
            {"role": "user", "content": initial_query},
            {"role": "assistant", "content": first["message"]["content"]},
            {"role": "user", "content": "No urgency or family violence."},
            {"role": "assistant", "content": second["message"]["content"]},
            {"role": "user", "content": "No Singaporean child."},
        ],
        {},
        {},
        session_state="session-1",
    )

    content = third["message"]["content"]
    assert "**Selected Entry:** GEN3-T04" in content
    assert "Carried over from" in content
    assert "foreigner" in content
    assert "Next question: Q4 from GEN3-T04" in content
    assert "Are you a Singapore Citizen or PR?" not in content


@pytest.mark.asyncio
async def test_unknown_residency_still_asks_residency_question(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("unknown residency path should stay deterministic")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    initial_query = "Applicant wants help with divorce."
    first = await chat_approach.run_without_streaming(
        [{"role": "user", "content": initial_query}],
        {},
        {},
        session_state="session-1",
    )
    result = await chat_approach.run_without_streaming(
        [
            {"role": "user", "content": initial_query},
            {"role": "assistant", "content": first["message"]["content"]},
            {"role": "user", "content": "No urgency or family violence."},
        ],
        {},
        {},
        session_state="session-1",
    )

    content = result["message"]["content"]
    assert "Next question: Q2 from GEN3-T03" in content
    assert "Are you a Singapore Citizen or PR?" in content


def test_prerequisite_guard_repairs_hallucinated_queued_topic_answers(chat_approach):
    hallucinated_content = """**Selected Entry:** GEN3-T03

What I gathered from your description:

- Q1: family violence or court deadline within 14 days → No [GEN3-T03.json]
- Q2: Singapore Citizen or PR → Yes [GEN3-T03.json]

Triage progress:

- Next question: Q3 from GEN3-T03

**Ask the applicant (read verbatim):**

> **Q3: "Have you applied to the Legal Aid Bureau (LAB) for civil legal aid?"**"""
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A

Topics identified:
1. GEN3-T02 — routed workflow
2. GEN3-T03 — queued workflow""",
        },
        {"role": "user", "content": "ok"},
    ]

    repaired = chat_approach.repair_unvalidated_prerequisite_skip(hallucinated_content, messages, "ok")

    assert repaired is not None
    assert "Repaired skipped prerequisite: Q1 from GEN3-T03" in repaired
    assert "Next question: Q1 from GEN3-T03" in repaired
    assert "Have you applied to the Legal Aid Bureau" not in repaired


@pytest.mark.asyncio
async def test_pilot_no_income_condo_reuses_lab_and_routes_hardship_to_staff(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("pilot path should stay deterministic without RAG/LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    answers = []

    async def ask(question):
        messages = [
            message
            for prior_question, prior_response in answers
            for message in (
                {"role": "user", "content": prior_question},
                {"role": "assistant", "content": prior_response["message"]["content"]},
            )
        ]
        response = await chat_approach.run_without_streaming(
            [*messages, {"role": "user", "content": question}],
            {},
            {},
            session_state="session-1",
        )
        answers.append((question, response))
        return response

    await ask("Applicant married in December 2025, stays in condo and seeks help with divorce")
    await ask("No")
    await ask("Yes")
    await ask("Yes, failed means test")
    handoff = await ask("no income, stays condo")

    assert "**Selected Entry:** GEN3-T04" in handoff["message"]["content"]
    assert "Have you applied to the Legal Aid Bureau" not in handoff["message"]["content"]
    assert "Per Capita Household Income" not in handoff["message"]["content"]
    assert "Next question: Q2 from GEN3-T04" in handoff["message"]["content"]

    final = await ask("rep")
    content = final["message"]["content"]

    assert "**Routing Recommendation:** Route C" in content
    assert "Route D" not in content
    assert "No, marginal or exceptional" in content
    assert "No income / hardship" in content
    assert "Have you applied to the Legal Aid Bureau" not in content
    assert "Per Capita Household Income" not in content


@pytest.mark.asyncio
async def test_pilot_low_income_hdb_foreigner_with_child_routes_to_fjss(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("low-income HDB FJSS path should stay deterministic without RAG/LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    answers = []

    async def ask(question):
        messages = [
            message
            for prior_question, prior_response in answers
            for message in (
                {"role": "user", "content": prior_question},
                {"role": "assistant", "content": prior_response["message"]["content"]},
            )
        ]
        response = await chat_approach.run_without_streaming(
            [*messages, {"role": "user", "content": question}],
            {},
            {},
            session_state="session-1",
        )
        answers.append((question, response))
        return response

    await ask("35 yo, with 3 yo daughter seeks divorce")
    await ask("No")
    await ask("No, foreigner")
    await ask("Yes")
    final = await ask("currently earning $2k, staying in 3 rm HDB")
    content = final["message"]["content"]

    assert "**Routing Recommendation:** Route D (FJSS Pro Bono" in content
    assert "FJSS Pro Bono" in content
    assert "Route F" not in content
    assert "Not sure" not in content
    assert "Carried over from" in content


@pytest.mark.asyncio
async def test_pilot_criminal_urgent_reuses_initial_context_and_avoids_loop(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("criminal urgent pilot path should stay deterministic without RAG/LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    answers = []

    async def ask(question):
        messages = [
            message
            for prior_question, prior_response in answers
            for message in (
                {"role": "user", "content": prior_question},
                {"role": "assistant", "content": prior_response["message"]["content"]},
            )
        ]
        response = await chat_approach.run_without_streaming(
            [*messages, {"role": "user", "content": question}],
            {},
            {},
            session_state="session-1",
        )
        answers.append((question, response))
        return response

    first = await ask(
        "caller said he has been charged in court for assault and has no legal representation. "
        "he is singaporean, no money to hire a lawyer"
    )
    assert "Next question: Q2 from GEN3-T02" in first["message"]["content"]
    assert "Is the offence a capital offence" not in first["message"]["content"]

    urgent = await ask("yes, 7 days")
    content = urgent["message"]["content"]
    assert "Concurrent routing note" in content
    assert "Next question: Q5 from GEN3-T02" in content
    assert "Have you applied to, or been told about, the Public Defender" in content
    assert "Have you been charged in court?" not in content
    assert "Is the applicant a Singapore Citizen or PR?" not in content
    assert "GEN3-T01" not in content

    final = await ask("no")
    assert "**Routing Recommendation:** Route B (Refer to PDO First)" in final["message"]["content"]


def test_hardship_guard_repairs_llm_route_d_rejection(chat_approach):
    hallucinated_rejection = """**Selected Entry:** GEN3-T04

Triage progress:

- Last answered: Q4 = No, well over, no exceptions → Route D

**Routing Recommendation:** Route D (Reject and Share Self-Help Resources)"""
    messages = [
        {"role": "assistant", "content": """**Selected Entry:** GEN3-T04

**Ask the applicant (read verbatim):**

> **Q4: "Is your Per Capita Household Income (PCHI) ≤ S$5,000, does the applicant have savings of ≤ $10,000 if younger than 60 years old (or ≤ $40,000 if 60 years old or older), and does the applicant stay in non-private housing (e.g. HDB, shelter, remand/prison)?"**"""},
        {"role": "user", "content": "no income, stays condo"},
    ]

    repaired = chat_approach.repair_hardship_rejection(
        hallucinated_rejection,
        messages,
        "no income, stays condo",
    )

    assert repaired is not None
    assert "**Routing Recommendation:** Route C" in repaired
    assert "Route D (Reject" not in repaired
    assert "Repair note" in repaired


@pytest.mark.asyncio
async def test_structured_switch_queues_new_topic_while_routing_active_answer(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("compound locked turn should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "answer_plus_new_topic",
            "pending_answer": {"branch_key": "if_no_well_over_no_exceptions", "confidence": 0.92},
            "new_topics": [{"entry_id": "GEN3-T02", "evidence": "criminal", "confidence": 0.88}],
            "correction": {"affects_prior_answer": False, "reason": None},
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("well over the threshold, no exceptions. also the foreigner has a criminal issue"),
        {},
        {},
        session_state="session-1",
    )

    assert "**Routing Recommendation:** Route D" in result["message"]["content"]
    assert "Queued topic note" in result["message"]["content"]
    assert "GEN3-T02" in result["context"]["pbsg_triage_state"]["queued_workflows"]


@pytest.mark.asyncio
async def test_structured_switch_preserves_pending_question_for_new_topic_only(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("new topic switch should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "new_topic_only",
            "pending_answer": None,
            "new_topics": [{"entry_id": "GEN3-T02", "evidence": "criminal", "confidence": 0.9}],
            "correction": {"affects_prior_answer": False, "reason": None},
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("also the foreigner has a criminal issue"),
        {},
        {},
        session_state="session-1",
    )

    assert "Current question remains: Q4 from GEN3-T04" in result["message"]["content"]
    assert "Queued topic note" in result["message"]["content"]
    assert "GEN3-T02" in result["context"]["pbsg_triage_state"]["queued_workflows"]


@pytest.mark.asyncio
async def test_structured_switch_preserves_pending_question_for_correction(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("correction switch should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "correction",
            "pending_answer": None,
            "new_topics": [],
            "correction": {"affects_prior_answer": True, "reason": "means answer changed"},
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("actually sorry I meant something else"),
        {},
        {},
        session_state="session-1",
    )

    assert "I noted a correction" in result["message"]["content"]
    assert "Current question remains: Q4 from GEN3-T04" in result["message"]["content"]


@pytest.mark.asyncio
async def test_structured_switch_answers_clarification_without_advancing(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("clarification switch should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "clarification",
            "pending_answer": None,
            "new_topics": [],
            "correction": {"affects_prior_answer": False, "reason": None},
            "clarification_answer": "PCHI means total monthly household income divided by household members.",
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("what does PCHI mean?"),
        {},
        {},
        session_state="session-1",
    )

    assert "PCHI means total monthly household income" in result["message"]["content"]
    assert "Current question remains: Q4 from GEN3-T04" in result["message"]["content"]


def test_create_chat_completion_uses_max_completion_tokens_for_gpt5_variants(chat_approach):
    captured_kwargs = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return "completion"

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAIClient:
        chat = FakeChat()

    chat_approach.openai_client = FakeOpenAIClient()

    completion = chat_approach.create_chat_completion(
        chatgpt_deployment=None,
        chatgpt_model="gpt-5-chat",
        messages=[],
        overrides={},
        response_token_limit=123,
    )

    assert completion == "completion"
    assert captured_kwargs["max_completion_tokens"] == 123
    assert "max_tokens" not in captured_kwargs
    assert "reasoning_effort" not in captured_kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minimum_search_score,minimum_reranker_score,expected_result_count",
    [
        (0, 0, 1),
        (0, 2, 1),
        (0.03, 0, 1),
        (0.03, 2, 1),
        (1, 0, 0),
        (0, 4, 0),
        (1, 4, 0),
    ],
)
async def test_search_results_filtering_by_scores(
    chat_approach, monkeypatch, minimum_search_score, minimum_reranker_score, expected_result_count
):
    monkeypatch.setattr(SearchClient, "search", mock_search)

    filtered_results = await chat_approach.search(
        top=10,
        query_text="test query",
        filter=None,
        vectors=[],
        use_text_search=True,
        use_vector_search=True,
        use_semantic_ranker=True,
        use_semantic_captions=True,
        minimum_search_score=minimum_search_score,
        minimum_reranker_score=minimum_reranker_score,
    )

    assert (
        len(filtered_results) == expected_result_count
    ), f"Expected {expected_result_count} results with minimum_search_score={minimum_search_score} and minimum_reranker_score={minimum_reranker_score}"


@pytest.mark.asyncio
async def test_search_results_query_rewriting(chat_approach, monkeypatch):

    query_rewrites = None

    async def validate_qr_and_mock_search(*args, **kwargs):
        nonlocal query_rewrites
        query_rewrites = kwargs.get("query_rewrites")
        return await mock_search(*args, **kwargs)

    monkeypatch.setattr(SearchClient, "search", validate_qr_and_mock_search)

    results = await chat_approach.search(
        top=10,
        query_text="test query",
        filter=None,
        vectors=[],
        use_text_search=True,
        use_vector_search=True,
        use_semantic_ranker=True,
        use_semantic_captions=True,
        use_query_rewriting=True,
    )
    assert len(results) == 1
    assert query_rewrites == "generative"


@pytest.mark.asyncio
async def test_compute_multimodal_embedding(monkeypatch, chat_approach):
    # Create a mock for the ImageEmbeddings.create_embedding_for_text method
    async def mock_create_embedding_for_text(self, q: str):
        # Return a mock vector
        return [0.1, 0.2, 0.3, 0.4, 0.5]

    monkeypatch.setattr(ImageEmbeddings, "create_embedding_for_text", mock_create_embedding_for_text)

    # Create a mock ImageEmbeddings instance and set it on the chat_approach
    mock_image_embeddings = ImageEmbeddings(endpoint="https://mock-endpoint", token_provider=lambda: None)
    chat_approach.image_embeddings_client = mock_image_embeddings

    # Test the compute_multimodal_embedding method
    query = "What's in this image?"
    result = await chat_approach.compute_multimodal_embedding(query)

    # Verify the result is a VectorizedQuery with the expected properties
    assert isinstance(result, VectorizedQuery)
    assert result.vector == [0.1, 0.2, 0.3, 0.4, 0.5]
    assert result.k == 50
    assert result.fields == "images/embedding"


@pytest.mark.asyncio
async def test_compute_multimodal_embedding_no_client():
    """Test that compute_multimodal_embedding raises ValueError when image_embeddings_client is not set."""
    # Create a chat approach without an image_embeddings_client
    chat_approach = ChatReadRetrieveReadApproach(
        search_client=SearchClient(endpoint="", index_name="", credential=AzureKeyCredential("")),
        search_index_name=None,
        knowledgebase_model=None,
        knowledgebase_deployment=None,
        knowledgebase_client=None,
        openai_client=None,
        chatgpt_model="gpt-35-turbo",
        chatgpt_deployment="chat",
        embedding_deployment="embeddings",
        embedding_model=MOCK_EMBEDDING_MODEL_NAME,
        embedding_dimensions=MOCK_EMBEDDING_DIMENSIONS,
        embedding_field="embedding3",
        sourcepage_field="",
        content_field="",
        query_language="en-us",
        query_speller="lexicon",
        prompt_manager=PromptManager(),
        # Explicitly set image_embeddings_client to None
        image_embeddings_client=None,
    )

    # Test that calling compute_multimodal_embedding raises a ValueError
    with pytest.raises(ValueError, match="Approach is missing an image embeddings client for multimodal queries"):
        await chat_approach.compute_multimodal_embedding("What's in this image?")


@pytest.mark.asyncio
async def test_chat_prompt_render_with_image_directive(chat_approach):
    """Verify DocFX style :::image directive is sanitized (replaced with [image]) during prompt rendering."""
    image_directive = (
        "activator-introduction.md#page=1: Intro text before image. "
        ':::image type="content" source="./media/activator-introduction/activator.png" '
        'alt-text="Diagram that shows the architecture of Fabric Activator."::: More text after image.'
    )

    async def build_sources():
        return await chat_approach.get_sources_content(
            [
                Document(
                    id="doc1",
                    content=image_directive.split(": ", 1)[1],
                    sourcepage="activator-introduction.md#page=1",
                    sourcefile="activator-introduction.md",
                )
            ],
            use_semantic_captions=False,
            include_text_sources=True,
            download_image_sources=False,
            user_oid=None,
        )

    data_points = await build_sources()

    messages = chat_approach.prompt_manager.build_conversation(
        system_template_path="chat_answer.system.jinja2",
        system_template_variables={
            "include_follow_up_questions": False,
            "image_sources": data_points.images,
            "citations": data_points.citations,
        },
        user_template_path="chat_answer.user.jinja2",
        user_template_variables={
            "user_query": "What is Fabric Activator?",
            "text_sources": data_points.text,
        },
        user_image_sources=data_points.images,
        past_messages=[],
    )
    assert messages
    # Find the user message containing Sources and verify placeholder
    combined = "\n".join([m["content"] for m in messages if m["role"] == "user"])
    # Expect triple colons escaped
    assert "&#58;&#58;&#58;image" in combined
    assert "activator-introduction/activator.png" in combined
    assert "Diagram that shows the architecture of Fabric Activator." in combined
    # Original unescaped sequence should be gone
    assert ":::image" not in combined


@pytest.mark.asyncio
async def test_get_sources_content_downloads_images_from_images_container(chat_approach, monkeypatch):
    """Regression test: ensure image URLs in a non-default container download from that container."""

    called: dict[str, str] = {}

    async def fake_download_blob(blob_path: str, user_oid=None, container=None):
        called["blob_path"] = blob_path
        called["container"] = container
        assert user_oid is None
        return b"abc", {"content_settings": {"content_type": "image/png"}}

    monkeypatch.setattr(chat_approach.global_blob_manager, "download_blob", fake_download_blob)

    image_url = "https://examplestorage.blob.core.windows.net/images/doc1/page0/figure1.png"
    doc = Document(
        id="doc1",
        content="",
        sourcepage="doc1.pdf#page=1",
        sourcefile="doc1.pdf",
        images=[{"url": image_url}],
    )

    data_points = await chat_approach.get_sources_content(
        [doc],
        use_semantic_captions=False,
        include_text_sources=False,
        download_image_sources=True,
        user_oid=None,
    )

    assert called["container"] == "images"
    assert called["blob_path"] == "doc1/page0/figure1.png"
    assert data_points.images == [f"data:image/png;base64,{base64.b64encode(b'abc').decode('utf-8')}"]


def test_replace_all_ref_ids_unknown_fallback(chat_approach):
    """Test that unknown ref_ids remain unchanged (fallback case)."""
    answer = "This is an answer with [ref_id:999] that doesn't match any document or web result."
    documents = [
        Document(
            id="doc1",
            ref_id="1",
            content="Some content",
            sourcepage="page1.pdf",
            sourcefile="page1.pdf",
        )
    ]
    web_results = [
        WebResult(
            id="5",
            title="Web Result",
            url="https://example.com",
        )
    ]

    result = chat_approach.replace_all_ref_ids(answer, documents, web_results)

    # ref_id:999 doesn't exist in either documents or web_results, so it should remain unchanged
    assert "[ref_id:999]" in result
    assert result == "This is an answer with [ref_id:999] that doesn't match any document or web result."


def test_replace_all_ref_ids_mixed(chat_approach):
    """Test that ref_ids are replaced correctly for web, documents, and unknown refs."""
    answer = "Check [ref_id:1] and [ref_id:5] and also [ref_id:999]."
    documents = [
        Document(
            id="doc1",
            ref_id="1",
            content="Some content",
            sourcepage="page1.pdf",
            sourcefile="page1.pdf",
        )
    ]
    web_results = [
        WebResult(
            id="5",
            title="Web Result",
            url="https://example.com",
        )
    ]

    result = chat_approach.replace_all_ref_ids(answer, documents, web_results)

    # ref_id:1 should be replaced with document sourcepage
    assert "[page1.pdf]" in result
    # ref_id:5 should be replaced with web URL (web has priority)
    assert "[https://example.com]" in result
    # ref_id:999 doesn't exist, should remain unchanged
    assert "[ref_id:999]" in result
    assert result == "Check [page1.pdf] and [https://example.com] and also [ref_id:999]."


def test_replace_all_ref_ids_sharepoint_priority(chat_approach):
    """SharePoint URLs should be used when present."""

    answer = "See [ref_id:7] for the site link."
    documents = [
        Document(id="doc1", ref_id="7", sourcepage="page1.pdf", sourcefile="page1.pdf"),
    ]
    sharepoint_results = [
        SharePointResult(id="7", web_url="https://sharepoint.example.com/documents/7"),
    ]

    result = chat_approach.replace_all_ref_ids(answer, documents, [], sharepoint_results)

    # SharePoint extracts filename from URL (last part after /)
    assert result == "See [7] for the site link."


@pytest.mark.asyncio
async def test_get_sources_content_includes_sharepoint(chat_approach):

    documents = [
        Document(id="doc1", ref_id="1", sourcepage="page1.pdf", content="Doc content"),
    ]
    sharepoint_results = [
        SharePointResult(
            id="10",
            web_url="https://contoso.sharepoint.com/doc",
            content="SharePoint body",
            title="SharePoint Title",
            activity=ActivityDetail(id=3, number=1, type="remoteSharePoint", source="sharepoint", query="sp query"),
        )
    ]

    data_points = await chat_approach.get_sources_content(
        documents,
        use_semantic_captions=False,
        include_text_sources=True,
        download_image_sources=False,
        sharepoint_results=sharepoint_results,
    )

    # SharePoint extracts filename from URL (last part after /)
    assert "doc" in data_points.citations
    assert (
        data_points.external_results_metadata
        and data_points.external_results_metadata[0]["title"] == "SharePoint Title"
    )


def test_select_knowledgebase_client_priorities(chat_approach):
    primary = object()
    web = object()
    sharepoint = object()
    both = object()

    chat_approach.knowledgebase_client = primary
    chat_approach.knowledgebase_client_with_web = web
    chat_approach.knowledgebase_client_with_sharepoint = sharepoint
    chat_approach.knowledgebase_client_with_web_and_sharepoint = both

    selected, uses_web, uses_sp = chat_approach._select_knowledgebase_client(True, True)
    assert selected is both
    assert uses_web is True and uses_sp is True

    selected, uses_web, uses_sp = chat_approach._select_knowledgebase_client(True, False)
    assert selected is web and uses_web is True and uses_sp is False

    selected, uses_web, uses_sp = chat_approach._select_knowledgebase_client(False, True)
    assert selected is sharepoint and uses_web is False and uses_sp is True

    chat_approach.knowledgebase_client_with_web_and_sharepoint = None
    chat_approach.knowledgebase_client_with_sharepoint = None
    selected, uses_web, uses_sp = chat_approach._select_knowledgebase_client(True, True)
    assert selected is web and uses_web is True and uses_sp is False


def test_select_knowledgebase_client_requires_configuration(chat_approach):
    chat_approach.knowledgebase_client = None
    chat_approach.knowledgebase_client_with_web = None
    chat_approach.knowledgebase_client_with_sharepoint = None

    with pytest.raises(ValueError, match="Agentic retrieval requested but no knowledge base is configured"):
        chat_approach._select_knowledgebase_client(True, False)


@pytest.mark.asyncio
async def test_run_with_streaming_handles_non_stream_response(chat_approach, monkeypatch):
    extra_info = ExtraInfo(
        data_points=DataPoints(text=[], images=[], citations=[]),
        thoughts=[ThoughtStep("Final", None, props={})],
    )

    async def fake_completion():
        payload = {
            "id": "chatcmpl-stream",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "Answer text<<Follow up?>>"},
                }
            ],
            "usage": {"completion_tokens": 1, "prompt_tokens": 1, "total_tokens": 2},
        }
        return ChatCompletion.model_validate(payload, strict=False)

    async def fake_run_until_final_call(messages, overrides, auth_claims, should_stream):
        assert should_stream is True
        return extra_info, fake_completion()

    monkeypatch.setattr(chat_approach, "run_until_final_call", fake_run_until_final_call)

    events = []
    async for event in chat_approach.run_with_streaming(
        messages=[
            {"role": "user", "content": "Earlier non-PBSG question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Hello"},
        ],
        overrides={"suggest_followup_questions": True},
        auth_claims={},
        session_state="state",
    ):
        events.append(event)

    assert events[0]["context"] is extra_info
    assert events[1]["delta"]["content"] == "Answer text"
    assert events[2]["context"] is extra_info
    assert events[3]["context"]["followup_questions"] == ["Follow up?"]


@pytest.mark.asyncio
async def test_run_with_streaming_buffers_pbsg_output_and_collapses_duplicate_route_cards(chat_approach, monkeypatch):
    entry = chat_approach.pbsg_golden_set_entries["GEN3-T02"]
    extra_info = ExtraInfo(
        data_points=chat_approach.golden_set_data_points({"GEN3-T02": entry}),
        thoughts=[ThoughtStep("Final", None, props={})],
    )
    duplicate_content = """**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "First LASCO wording."

**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "Repeated LASCO wording."
"""

    class FakeStream:
        def __init__(self):
            self.responses = [
                {
                    "object": "chat.completion.chunk",
                    "choices": [{"delta": {"role": "assistant"}, "index": 0, "finish_reason": None}],
                    "id": "chunk-1",
                    "model": "gpt-4.1-mini",
                    "created": 1,
                },
                {
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "delta": {"role": "assistant", "content": duplicate_content},
                            "index": 0,
                            "finish_reason": None,
                        }
                    ],
                    "id": "chunk-1",
                    "model": "gpt-4.1-mini",
                    "created": 1,
                },
                {
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "id": "chunk-1",
                    "model": "gpt-4.1-mini",
                    "created": 1,
                    "usage": {"completion_tokens": 1, "prompt_tokens": 1, "total_tokens": 2},
                },
            ]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.responses:
                raise StopAsyncIteration
            return ChatCompletionChunk.model_validate(self.responses.pop(0), strict=False)

    async def fake_run_until_final_call(messages, overrides, auth_claims, should_stream):
        assert should_stream is True

        async def stream_response():
            return FakeStream()

        return extra_info, stream_response()

    monkeypatch.setattr(chat_approach, "run_until_final_call", fake_run_until_final_call)

    events = []
    async for event in chat_approach.run_with_streaming(
        messages=[
            {"role": "user", "content": "Earlier"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "Criminal charge"},
        ],
        overrides={},
        auth_claims={},
        session_state="state",
    ):
        events.append(event)

    content_events = [event for event in events if event.get("delta", {}).get("content")]
    assert len(content_events) == 1
    content = content_events[0]["delta"]["content"]
    assert content.count("**Selected Entry:**") == 1
    assert content.count("**Routing Recommendation:**") == 1
    assert "First LASCO wording" in content
    assert "Repeated LASCO wording" not in content
    assert events[-1]["context"] is extra_info


@pytest.mark.asyncio
async def test_run_until_final_call_rejects_web_streaming(chat_approach):
    with pytest.raises(Exception, match="web source is enabled"):
        await chat_approach.run_until_final_call(
            messages=[{"role": "user", "content": "Hello"}],
            overrides={"use_agentic_knowledgebase": True, "use_web_source": True},
            auth_claims={},
            should_stream=True,
        )


@pytest.mark.asyncio
async def test_run_with_streaming_uses_deterministic_fast_path_for_locked_flow(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("locked deterministic flow should not call retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Ask the applicant (read verbatim):**

> **Q1: "Is the offence a capital offence (punishable with death)?"**""",
        },
        {"role": "user", "content": "No"},
    ]

    events = []
    async for event in chat_approach.run_with_streaming(messages, {}, {}, session_state="session-1"):
        events.append(event)

    assert events[0]["session_state"] == "session-1"
    assert events[1]["delta"]["content"].startswith("**Selected Entry:** GEN3-T02")
    assert "Next question: Q2 from GEN3-T02" in events[1]["delta"]["content"]
    assert events[2]["context"]["pbsg_triage_state"]["mode"] == "FAST_ROUTING"


def fake_openai_client_with_json(payload):
    class FakeCompletions:
        async def create(self, **kwargs):
            return ChatCompletion.model_validate(
                {
                    "id": "classification",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4.1-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": json.dumps(payload)},
                        }
                    ],
                },
                strict=False,
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAIClient:
        chat = FakeChat()

    return FakeOpenAIClient()


def gen3_t04_q4_messages(user_content):
    return [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T04

**Ask the applicant (read verbatim):**

> **Q4: "(Means) Is the applicant's Per Capita Household Income (PCHI) ≤ S$5,000, does the applicant have savings of ≤ $10,000 if younger than 60 years old (or ≤ $40,000 if 60 years old or older), and does the applicant stay in non-private housing (e.g. HDB, shelter, remand/prison)?"**""",
        },
        {"role": "user", "content": user_content},
    ]


@pytest.mark.asyncio
async def test_structured_switch_queues_new_topic_while_routing_active_answer(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("compound locked turn should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "answer_plus_new_topic",
            "pending_answer": {"branch_key": "if_no_well_over_no_exceptions", "confidence": 0.92},
            "new_topics": [{"entry_id": "GEN3-T02", "evidence": "criminal", "confidence": 0.88}],
            "correction": {"affects_prior_answer": False, "reason": None},
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("well over the threshold, no exceptions. also the foreigner has a criminal issue"),
        {},
        {},
        session_state="session-1",
    )

    assert "**Routing Recommendation:** Route D" in result["message"]["content"]
    assert "Queued topic note" in result["message"]["content"]
    assert "GEN3-T02" in result["context"]["pbsg_triage_state"]["queued_workflows"]


@pytest.mark.asyncio
async def test_structured_switch_preserves_pending_question_for_new_topic_only(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("new topic switch should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "new_topic_only",
            "pending_answer": None,
            "new_topics": [{"entry_id": "GEN3-T02", "evidence": "criminal", "confidence": 0.9}],
            "correction": {"affects_prior_answer": False, "reason": None},
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("also the foreigner has a criminal issue"),
        {},
        {},
        session_state="session-1",
    )

    assert "Current question remains: Q4 from GEN3-T04" in result["message"]["content"]
    assert "Queued topic note" in result["message"]["content"]
    assert "GEN3-T02" in result["context"]["pbsg_triage_state"]["queued_workflows"]


@pytest.mark.asyncio
async def test_structured_switch_answers_clarification_without_advancing(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("clarification switch should not run retrieval")

    chat_approach.openai_client = fake_openai_client_with_json(
        {
            "turn_type": "clarification",
            "pending_answer": None,
            "new_topics": [],
            "correction": {"affects_prior_answer": False, "reason": None},
            "clarification_answer": "PCHI means total monthly household income divided by household members.",
        }
    )
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        gen3_t04_q4_messages("what does PCHI mean?"),
        {},
        {},
        session_state="session-1",
    )

    assert "PCHI means total monthly household income" in result["message"]["content"]
    assert "Current question remains: Q4 from GEN3-T04" in result["message"]["content"]
