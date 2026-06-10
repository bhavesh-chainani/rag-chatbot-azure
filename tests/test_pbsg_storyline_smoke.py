import re
from typing import Any

import pytest

from pbsg_triage_state import PBSGRoutingEngine, extract_question_targets, extract_selected_entry_id


ANSWER_SELF = "I am the person who needs legal help, or they cannot contact PBSG themselves"
ANSWER_NO = "No"
ANSWER_YES = "Yes"
ANSWER_FOREIGNER = "No, not a Singapore Citizen or PR"
ANSWER_URGENT_DEADLINE = "Legal or procedural deadline"


def make_case(
    *,
    test_id: str,
    scenario_name: str,
    input_text: str,
    manual_answers: dict[str, str],
    expected_route: str,
    expected_streams: list[str],
    expected_asked_questions: list[str],
    expected_auto_answered: list[str],
    caller_profile: str = "",
    pre_triage_response: str = "",
) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "scenario_name": scenario_name,
        "input_text": input_text,
        "context": {
            "caller_profile": caller_profile,
            "pre_triage_response": pre_triage_response,
        },
        "manual_answers": manual_answers,
        "expected": {
            "route": expected_route,
            "streams": expected_streams,
            "asked_questions": expected_asked_questions,
            "auto_answered": expected_auto_answered,
        },
    }


def first_contact_answers(
    q2: str = ANSWER_SELF,
    q3: str = ANSWER_NO,
    q4: str = ANSWER_NO,
) -> dict[str, str]:
    return {
        "GEN3-T01.Q2": q2,
        "GEN3-T01.Q3": q3,
        "GEN3-T01.Q4": q4,
    }


STORYLINE_CASES = [
    make_case(
        test_id="TC001",
        scenario_name="Standard criminal SGC charged",
        input_text=(
            "Caller is a Singapore Citizen charged in court for shop theft. "
            "He has no lawyer and has not applied to PDO."
        ),
        caller_profile="SGC, low-income, adult",
        pre_triage_response="Yes",
        manual_answers={
            **first_contact_answers(),
            "GEN3-T02.Q2": ANSWER_NO,
            "GEN3-T02.Q5": ANSWER_NO,
        },
        expected_route="Route B",
        expected_streams=["GEN3-T01", "GEN3-T02"],
        expected_asked_questions=[
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T02.Q2",
            "GEN3-T02.Q5",
        ],
        expected_auto_answered=[
            "GEN3-T01.Q1",
            "GEN3-T02.Q1",
            "GEN3-T02.Q3",
            "GEN3-T02.Q4",
        ],
    ),
    make_case(
        test_id="TC002",
        scenario_name="Foreigner criminal non-capital",
        input_text=(
            "Caller is a Filipino national charged in court for theft. He has no lawyer. "
            "His next court mention is in six weeks. He rents an HDB room, earns $1800, "
            "supports his wife and child, and has $2000 savings."
        ),
        caller_profile="Foreigner, charged, non-capital",
        pre_triage_response="No",
        manual_answers={
            **first_contact_answers(),
            "GEN3-T02.Q2": ANSWER_NO,
            "GEN3-T02.Q4": ANSWER_FOREIGNER,
        },
        expected_route="Route E",
        expected_streams=["GEN3-T01", "GEN3-T02"],
        expected_asked_questions=[
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T02.Q2",
            "GEN3-T02.Q4",
        ],
        expected_auto_answered=[
            "GEN3-T01.Q1",
            "GEN3-T02.Q1",
            "GEN3-T02.Q3",
            "GEN3-T02.Q6",
        ],
    ),
    make_case(
        test_id="TC003",
        scenario_name="Capital offence",
        input_text=(
            "Applicant has been charged with murder in State Courts. "
            "The offence is punishable with death. He has no lawyer."
        ),
        caller_profile="Capital charge, no lawyer",
        pre_triage_response="NA",
        manual_answers=first_contact_answers(),
        expected_route="Route A",
        expected_streams=["GEN3-T01", "GEN3-T02"],
        expected_asked_questions=[
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
        ],
        expected_auto_answered=[
            "GEN3-T01.Q1",
            "GEN3-T02.Q1",
        ],
    ),
    make_case(
        test_id="TC004",
        scenario_name="Criminal urgent court date",
        input_text="Caller is a foreigner charged in court for theft. He has no lawyer.",
        caller_profile="Foreigner",
        manual_answers={
            **first_contact_answers(),
            "GEN3-T02.Q2": ANSWER_YES,
            "GEN3-T06.Q1": ANSWER_URGENT_DEADLINE,
            "GEN3-T02.Q6": ANSWER_YES,
        },
        expected_route="Route E",
        expected_streams=["GEN3-T01", "GEN3-T02", "GEN3-T06"],
        expected_asked_questions=[
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T02.Q2",
            "GEN3-T06.Q1",
            "GEN3-T02.Q6",
        ],
        expected_auto_answered=[
            "GEN3-T01.Q1",
            "GEN3-T02.Q1",
            "GEN3-T02.Q3",
            "GEN3-T02.Q4",
            "GEN3-T06.Q4",
            "GEN3-T06.Q5",
        ],
    ),
]


def route_label_from_content(content: str) -> str | None:
    match = re.search(r"\*\*Routing Recommendation:\*\*\s+(Route\s+[A-Z])\b", content)
    return match.group(1) if match else None


def run_storyline_case(case: dict[str, Any]) -> dict[str, Any]:
    engine = PBSGRoutingEngine.from_default_golden_set()
    messages: list[dict[str, str]] = []
    asked_questions: list[str] = []
    streams_seen: list[str] = []

    result = engine.execute_initial_turn(case["input_text"])
    assert result is not None, f"{case['test_id']} initial turn returned None"
    messages.extend(
        [
            {"role": "user", "content": case["input_text"]},
            {"role": "assistant", "content": result.content},
        ]
    )

    for _ in range(20):
        content = messages[-1]["content"]
        selected_entry_id = extract_selected_entry_id(content)
        if selected_entry_id and selected_entry_id not in streams_seen:
            streams_seen.append(selected_entry_id)

        route_label = route_label_from_content(content)
        if route_label:
            return {
                "asked_questions": asked_questions,
                "streams_seen": streams_seen,
                "route_label": route_label,
                "final_content": content,
            }

        targets = extract_question_targets(content)
        assert targets, f"{case['test_id']} had no pending question.\n{content}"

        target = targets[-1]
        question_key = f"{(target.entry_id or selected_entry_id)}.{target.question_id}"
        asked_questions.append(question_key)

        answer = case["manual_answers"].get(question_key)
        assert answer is not None, f"{case['test_id']} missing answer for {question_key}.\n{content}"

        result = engine.execute_locked_turn(messages, answer)
        assert result is not None, f"{case['test_id']} locked turn returned None for {question_key}={answer}"
        messages.extend(
            [
                {"role": "user", "content": answer},
                {"role": "assistant", "content": result.content},
            ]
        )

    pytest.fail(f"{case['test_id']} did not reach a route within the turn limit")


@pytest.mark.parametrize("case", STORYLINE_CASES, ids=[case["test_id"] for case in STORYLINE_CASES])
def test_storyline_cases_reach_expected_route(case: dict[str, Any]):
    result = run_storyline_case(case)

    assert result["route_label"] == case["expected"]["route"]


@pytest.mark.parametrize("case", STORYLINE_CASES, ids=[case["test_id"] for case in STORYLINE_CASES])
def test_storyline_cases_trigger_expected_streams(case: dict[str, Any]):
    result = run_storyline_case(case)

    assert result["streams_seen"] == case["expected"]["streams"]


@pytest.mark.parametrize("case", STORYLINE_CASES, ids=[case["test_id"] for case in STORYLINE_CASES])
def test_storyline_cases_follow_expected_question_path(case: dict[str, Any]):
    result = run_storyline_case(case)

    assert result["asked_questions"] == case["expected"]["asked_questions"]
    for question_id in case["expected"]["auto_answered"]:
        assert question_id not in result["asked_questions"]
