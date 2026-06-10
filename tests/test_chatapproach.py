import json
import re

import pytest
from openai.types.chat import ChatCompletion

import pbsg_triage_state
from approaches.approach import DataPoints, ExtraInfo
from approaches.chatreadretrieveread import build_contact_capture_prompt_response
from pbsg_triage_state import branch_key_for_answer


def visible_text(content: str) -> str:
    return re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)


def fake_openai_client_with_json(payload: dict):
    class _Client:
        pass

    return _Client()


def fake_openai_client_with_json_sequence(payloads: list[dict]):
    class _Client:
        pass

    return _Client()


async def _completion_from_payload(payload: dict):
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


def test_build_contact_capture_prompt_response_uses_verbatim_block_format():
    response = build_contact_capture_prompt_response({}, "Applicant has a criminal matter")
    content = response["message"]["content"]

    assert content == "\n\n".join(
        [
            "**Before we begin:**",
            "**Ask the applicant (read verbatim):**",
            '> **"Can I take your name and phone number for follow-up? If you do not want to share your name, that is okay — a phone number alone is fine. If you do not want to share either, just let me know and we will continue."**',
        ]
    )
    assert response["context"]["quick_reply"] is None


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

> Q2: "Are you the person who needs legal help, or are you calling on behalf of someone else?"
"""

    quick_reply = chat_approach.build_quick_reply(content, extra_info)

    assert quick_reply is not None
    assert [(option.id, option.label, option.value) for option in quick_reply.options] == [
        (
            "if_calling_on_behalf_and_able_to_self_help",
            "Calling for someone else; that person can contact PBSG directly",
            "Calling for someone else; that person can contact PBSG directly",
        ),
        (
            "if_self_or_calling_on_behalf_and_unable_to_self_help",
            "I am the person who needs legal help, or they cannot contact PBSG themselves",
            "I am the person who needs legal help, or they cannot contact PBSG themselves",
        ),
        ("if_not_sure", "Not sure", "Not sure"),
    ]
    assert (
        branch_key_for_answer(
            entry["branching_logic"]["Q2"],
            "I am the person who needs legal help, or they cannot contact PBSG themselves",
        )
        == "if_self_or_calling_on_behalf_and_unable_to_self_help"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected_text"),
    [
        ("What is LASCO?", "Legal Assistance Scheme for Capital Offences"),
        ("Where is the PBSG counter?", "State Courts Help Centre"),
        ("Tell me more about Pro Bono SG.", "Pro Bono SG is the organisation referenced"),
        ("Start triage", "The triage flow asks only the facts needed"),
    ],
)
async def test_run_without_streaming_uses_initial_faq_handler_for_fixed_faqs(
    chat_approach, monkeypatch, question, expected_text
):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("fixed FAQ should not call retrieval or LLM")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": question}],
        {},
        {},
        session_state="session-1",
    )

    content = result["message"]["content"]
    assert "**General enquiry:**" in content
    assert expected_text in content
    assert "**Before we begin:**" not in content
    assert "pbsg_triage_state" not in result["context"]
    assert result["session_state"] == "session-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Applicant has a criminal charge in court.",
        "Applicant wants a divorce and custody advice.",
        "Applicant's employer has not paid salary for three months.",
        "Applicant needs help because employer has not paid salary for three months.",
        "New applicant asking for legal help. How should I start triaging this call?",
        "caller's life is in danger",
        "I need help",
        "hi",
    ],
)
async def test_run_without_streaming_asks_contact_capture_for_non_faq_first_turns(chat_approach, monkeypatch, query):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("non-FAQ first turn should ask contact capture before routing")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)

    result = await chat_approach.run_without_streaming(
        [{"role": "user", "content": query}],
        {},
        {},
        session_state="session-1",
    )

    assert "**Before we begin:**" in result["message"]["content"]
    assert result["session_state"]["pbsg_contact_capture"]["status"] == "awaiting_response"
    assert "pbsg_triage_state" not in result["context"]


@pytest.mark.asyncio
async def test_run_without_streaming_continues_to_gen3_t01_after_contact_for_criminal_case(chat_approach, monkeypatch):
    async def fail_if_classifier_called(*args, **kwargs):
        raise AssertionError("deterministic follow-up should not call the initial topic classifier")

    async def fail_if_retrieval_called(*args, **kwargs):
        raise AssertionError("deterministic follow-up should not fall through to retrieval")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_classifier_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_retrieval_called)

    first_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Applicant has a criminal charge in court."}],
        {},
        {},
        session_state="session-1",
    )

    second_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Jane 91234567"}],
        {},
        {},
        session_state=first_turn["session_state"],
    )

    content = second_turn["message"]["content"]
    assert "**Selected Entry:** GEN3-T01" in content
    assert "Next question: Q1 from GEN3-T01" in content
    assert "Topics identified:" in content
    assert "GEN3-T02" in content
    assert second_turn["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"
    assert second_turn["session_state"]["pbsg_contact_capture"]["status"] == "completed"
    assert second_turn["session_state"]["pbsg_contact_capture"]["name"] == "Jane"
    assert second_turn["session_state"]["pbsg_contact_capture"]["phone"] == "91234567"


@pytest.mark.asyncio
async def test_run_without_streaming_continues_to_gen3_t01_after_contact_for_urgent_case(chat_approach, monkeypatch):
    async def fail_if_classifier_called(*args, **kwargs):
        raise AssertionError("urgent follow-up should not call the initial topic classifier")

    async def fail_if_retrieval_called(*args, **kwargs):
        raise AssertionError("urgent follow-up should not fall through to retrieval")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_classifier_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_retrieval_called)

    first_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "caller's life is in danger"}],
        {},
        {},
        session_state="session-1",
    )

    second_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Jane 91234567"}],
        {},
        {},
        session_state=first_turn["session_state"],
    )

    content = second_turn["message"]["content"]
    assert "**Selected Entry:** GEN3-T01" in content
    assert second_turn["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"
    assert "Next question: Q1 from GEN3-T01" in content
    assert second_turn["session_state"]["pbsg_contact_capture"]["status"] == "completed"
    assert second_turn["session_state"]["pbsg_contact_capture"]["name"] == "Jane"
    assert second_turn["session_state"]["pbsg_contact_capture"]["phone"] == "91234567"
    assert "GEN3-T06" not in content
    assert "GEN3-T06" not in second_turn["context"]["pbsg_triage_state"].get("queued_workflows", [])
    assert second_turn["context"]["pbsg_triage_state"].get("triggered_overlays") in ([], None)
    assert second_turn["context"]["pbsg_triage_state"].get("concurrent_monitors") in ([], None)
    assert "in danger" not in content.lower()
    assert "urgent" not in content.lower()
    assert second_turn["context"]["pbsg_triage_state"]["pending_entry_id"] == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"]["current_question_id"] == "Q1"
    assert second_turn["context"]["pbsg_triage_state"]["queued_workflows"] == []
    assert second_turn["context"]["pbsg_triage_state"]["routing_completion_status"] == "in_progress"
    assert second_turn["context"]["thoughts"][-1].title == "Deterministic PBSG routing"
    assert second_turn["context"]["thoughts"][-1].description == "Answered from Golden Set branching logic without retrieval or LLM generation."
    assert "Are you currently represented by a lawyer on this same matter?" in content
    assert "Type the applicant's answer here" in content
    assert second_turn["context"]["quick_reply"]["questionId"] == "Q1"
    assert second_turn["context"]["quick_reply"]["entryId"] == "GEN3-T01"
    assert [option["label"] for option in second_turn["context"]["quick_reply"]["options"]] == ["Yes", "No", "Not sure"]
    assert second_turn["session_state"].get("pbsg_session_memory") is not None
    assert second_turn["session_state"]["pbsg_session_memory"].get("routing_completion_status") == "in_progress"
    assert second_turn["session_state"]["pbsg_session_memory"].get("active_thread_id") is not None
    assert second_turn["context"].get("pbsg_memory_pack") is not None
    assert second_turn["context"]["pbsg_memory_pack"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_memory_pack"].get("active_thread_id") is not None
    assert second_turn["context"]["pbsg_triage_state"].get("memory_pack") is not None
    assert second_turn["context"]["pbsg_triage_state"]["memory_pack"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"]["memory_pack"].get("active_thread_id") is not None
    assert second_turn["context"]["pbsg_triage_state"].get("memory_origin") in (None, "messages+session")
    assert second_turn["context"]["pbsg_triage_state"].get("already_resolved") is False
    assert second_turn["context"]["pbsg_triage_state"].get("should_recap") is False
    assert second_turn["context"]["pbsg_triage_state"].get("queued_workflows") == []
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_locked") is True
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("mode") == "FAST_ROUTING"
    assert second_turn["context"]["pbsg_triage_state"].get("current_question_id") == "Q1"
    assert second_turn["context"]["pbsg_triage_state"].get("pending_entry_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("active_workflow") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"].get("queued_workflows") == []
    assert second_turn["context"]["pbsg_triage_state"].get("triggered_overlays") == []
    assert second_turn["context"]["pbsg_triage_state"].get("concurrent_monitors") == []
    assert second_turn["context"]["pbsg_triage_state"].get("current_question_id") == "Q1"
    assert second_turn["context"]["pbsg_triage_state"].get("pending_entry_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("active_workflow") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"].get("queued_workflows") == []
    assert second_turn["context"]["pbsg_triage_state"].get("triggered_overlays") == []
    assert second_turn["context"]["pbsg_triage_state"].get("concurrent_monitors") == []
    assert second_turn["context"]["pbsg_triage_state"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"].get("current_question_id") == "Q1"
    assert second_turn["context"]["pbsg_triage_state"].get("pending_entry_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("active_workflow") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"].get("queued_workflows") == []
    assert second_turn["context"]["pbsg_triage_state"].get("triggered_overlays") == []
    assert second_turn["context"]["pbsg_triage_state"].get("concurrent_monitors") == []
    assert second_turn["context"]["pbsg_triage_state"]["fact_ledger"] == []
    assert second_turn["context"]["pbsg_triage_state"].get("memory_hash") is not None
    assert second_turn["context"]["pbsg_triage_state"].get("memory_pack") is not None
    assert second_turn["context"]["pbsg_triage_state"]["memory_pack"].get("memory_hash") is not None
    assert second_turn["context"]["pbsg_triage_state"].get("topic_threads") is not None
    assert isinstance(second_turn["context"]["pbsg_triage_state"].get("topic_threads"), list)
    assert second_turn["context"]["pbsg_triage_state"].get("active_thread_id") is not None
    assert second_turn["context"]["pbsg_triage_state"].get("referenced_thread_id") is not None
    assert second_turn["context"]["pbsg_triage_state"].get("active_thread_id") == second_turn["context"]["pbsg_triage_state"].get("referenced_thread_id")
    assert second_turn["context"]["pbsg_triage_state"].get("session_summary") in ("", None)
    assert second_turn["context"]["pbsg_triage_state"].get("resume_hint") is None
    assert second_turn["context"]["pbsg_triage_state"].get("active_side_enquiry") is None
    assert isinstance(second_turn["context"]["pbsg_triage_state"].get("interruption_stack"), list)
    assert second_turn["context"]["pbsg_triage_state"].get("parent_workflow") is None
    assert second_turn["context"]["pbsg_triage_state"].get("resume_question_id") is None
    assert second_turn["context"]["pbsg_triage_state"].get("suspended_contexts") == []
    assert second_turn["context"]["pbsg_triage_state"].get("topic_threads") is not None
    assert isinstance(second_turn["context"]["pbsg_triage_state"].get("topic_threads"), list)
    assert second_turn["context"]["pbsg_triage_state"].get("contradiction_signals") == []
    assert second_turn["context"]["pbsg_triage_state"].get("triggered_overlays") == []
    assert second_turn["context"]["pbsg_triage_state"].get("concurrent_monitors") == []
    assert second_turn["context"]["pbsg_triage_state"].get("queued_workflows") == []
    assert second_turn["context"]["pbsg_triage_state"].get("already_resolved") is False
    assert second_turn["context"]["pbsg_triage_state"].get("should_recap") is False
    assert second_turn["context"]["pbsg_triage_state"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_locked") is True
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("active_workflow") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("pending_entry_id") == "GEN3-T01"
    assert second_turn["context"]["pbsg_triage_state"].get("current_question_id") == "Q1"
    assert second_turn["context"]["pbsg_triage_state"].get("mode") == "FAST_ROUTING"
    assert second_turn["context"]["pbsg_triage_state"].get("routing_completion_status") == "in_progress"
    assert second_turn["context"]["pbsg_triage_state"].get("workflow_locked") is True
    assert second_turn["context"]["pbsg_triage_state"].get("queued_workflows") == []
    assert second_turn["context"]["pbsg_triage_state"].get("triggered_overlays") == []
    assert second_turn["context"]["pbsg_triage_state"].get("concurrent_monitors") == []
    assert second_turn["context"]["pbsg_triage_state"].get("fact_ledger") == []


@pytest.mark.asyncio
async def test_run_without_streaming_replays_original_message_after_contact_skip(chat_approach, monkeypatch):
    async def fail_if_classifier_called(*args, **kwargs):
        raise AssertionError("follow-up after contact skip should not call the initial topic classifier")

    async def fail_if_retrieval_called(*args, **kwargs):
        raise AssertionError("follow-up after contact skip should not fall through to retrieval")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_classifier_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_retrieval_called)

    first_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Applicant wants a divorce and custody advice."}],
        {},
        {},
        session_state="session-1",
    )

    second_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "skip"}],
        {},
        {},
        session_state=first_turn["session_state"],
    )

    assert "**Selected Entry:** GEN3-T01" in second_turn["message"]["content"]
    assert "GEN3-T03" in second_turn["message"]["content"]
    assert second_turn["session_state"]["pbsg_contact_capture"]["status"] == "skipped"
    assert second_turn["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"


@pytest.mark.asyncio
async def test_run_without_streaming_keeps_mid_stream_general_enquiry_and_resumes_current_stream(chat_approach, monkeypatch):
    async def fail_if_classifier_called(*args, **kwargs):
        raise AssertionError("mid-stream FAQ should not call the initial topic classifier")

    async def fail_if_retrieval_called(*args, **kwargs):
        raise AssertionError("mid-stream FAQ should not fall through to retrieval")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_classifier_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_retrieval_called)

    first_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Applicant has a criminal charge in court."}],
        {},
        {},
        session_state="session-1",
    )
    second_turn = await chat_approach.run_without_streaming(
        [{"role": "user", "content": "Jane 91234567"}],
        {},
        {},
        session_state=first_turn["session_state"],
    )
    messages = [
        {"role": "assistant", "content": second_turn["message"]["content"]},
        {"role": "user", "content": "What is LASCO?"},
    ]
    third_turn = await chat_approach.run_without_streaming(
        messages,
        {},
        {},
        session_state=second_turn["session_state"],
    )

    content = third_turn["message"]["content"]
    assert "**General enquiry:**" in content
    assert "Legal Assistance Scheme for Capital Offences" in content
    assert "Current question remains" in content
    assert "**Selected Entry:** GEN3-T01" in content
    assert third_turn["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"
    assert third_turn["context"]["thoughts"][0].title == "Deterministic PBSG general enquiry interrupt"
    assert third_turn["session_state"]["pbsg_contact_capture"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_until_final_call_returns_canonical_route_before_final_llm(chat_approach, monkeypatch):
    async def fake_run_search_approach(messages, overrides, auth_claims, session_state=None):
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

> **Q1: \"Is the offence a capital offence (punishable with death)?\"**""",
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

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> **Q1: \"Are you currently represented by a lawyer on this same matter?\"**""",
        },
        {"role": "user", "content": "No"},
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    assert "**Selected Entry:** GEN3-T01" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["active_workflow"] == "GEN3-T01"
    assert result["context"]["thoughts"][-1].title == "Deterministic PBSG routing"


@pytest.mark.asyncio
async def test_run_without_streaming_uses_deterministic_fast_path_for_full_quick_reply_label(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("quick-reply label should not call retrieval or LLM")

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "create_chat_completion", fail_if_called)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> **Q2: \"Are you the person who needs legal help, or are you calling on behalf of someone else?\"**""",
        },
        {
            "role": "user",
            "content": "I am the person who needs legal help, or they cannot contact PBSG themselves",
        },
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    assert "**Selected Entry:** GEN3-T01" in result["message"]["content"]
    assert result["context"]["pbsg_triage_state"]["current_question_id"] == "Q3"
    assert result["context"]["thoughts"][-1].title == "Deterministic PBSG routing"


@pytest.mark.asyncio
async def test_run_without_streaming_routes_correction_turns_to_contextual_handler(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("correction turn should not fall through to retrieval")

    async def fake_contextual_response(messages, overrides, session_state=None):
        return {
            "message": {"role": "assistant", "content": "contextual correction handler"},
            "context": {"thoughts": [], "pbsg_triage_state": {"active_workflow": "GEN3-T01"}},
            "session_state": session_state,
        }

    chat_approach.openai_client = object()
    monkeypatch.setattr(chat_approach, "try_contextual_locked_response", fake_contextual_response)
    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> **Q1: \"Are you currently represented by a lawyer on this same matter?\"**""",
        },
        {"role": "user", "content": "Actually no"},
    ]

    result = await chat_approach.run_without_streaming(messages, {}, {}, session_state="session-1")

    assert result["message"]["content"] == "contextual correction handler"


@pytest.mark.asyncio
async def test_run_without_streaming_keeps_faq_interrupt_during_locked_flow(chat_approach, monkeypatch):
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("mid-stream FAQ interrupt should not call retrieval or LLM")

    monkeypatch.setattr(chat_approach, "run_until_final_call", fail_if_called)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T01

**Ask the applicant (read verbatim):**

> **Q1: \"Are you currently represented by a lawyer on this same matter?\"**""",
        },
        {"role": "user", "content": "What is LASCO?"},
    ]

    result = await chat_approach.run_without_streaming(
        messages,
        {},
        {},
        session_state={"pbsg_contact_capture": {"status": "completed", "name": "Jane", "phone": "91234567"}},
    )

    assert "**General enquiry:**" in result["message"]["content"]
    assert "Current question remains" in result["message"]["content"]
    assert result["context"]["thoughts"][0].title == "Deterministic PBSG general enquiry interrupt"
