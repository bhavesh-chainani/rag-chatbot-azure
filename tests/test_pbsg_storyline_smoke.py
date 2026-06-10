import re
from typing import Any

import pytest

from pbsg_triage_state import PBSGRoutingEngine, extract_question_targets, extract_selected_entry_id


ANSWER_SELF = "I am the person who needs legal help, or they cannot contact PBSG themselves"
ANSWER_NO = "No"
ANSWER_YES = "Yes"
ANSWER_FOREIGNER = "No, not a Singapore Citizen or PR"
ANSWER_URGENT_DEADLINE = "Legal or procedural deadline"
ANSWER_MATTER_CIVIL = "Civil or others"
ANSWER_MATTER_MATRIMONIAL = "Matrimonial"


def make_case(
    *,
    test_id: str,
    scenario_name: str,
    input_text: str,
    manual_answers: dict[str, str],
    expected_route: str | None,
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
    make_case(
        test_id="TC005",
        scenario_name="Manageable vulnerability stays under main civil flow",
        input_text="Applicant has a debt issue, speaks limited English, and may need an interpreter.",
        caller_profile="Civil issue with one non-severe vulnerability cue",
        manual_answers={
            "GEN3-T01.Q1": ANSWER_NO,
            "GEN3-T01.Q2": ANSWER_SELF,
            "GEN3-T01.Q3": ANSWER_NO,
            "GEN3-T01.Q4": ANSWER_NO,
            "GEN3-T01.Q5": ANSWER_MATTER_CIVIL,
            "GEN3-T04.Q1": ANSWER_FOREIGNER,
        },
        expected_route="Route C",
        expected_streams=["GEN3-T01", "GEN3-T04"],
        expected_asked_questions=[
            "GEN3-T01.Q1",
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T04.Q1",
        ],
        expected_auto_answered=[
            "GEN3-T13.Q1",
            "GEN3-T04.Q4",
        ],
    ),
    make_case(
        test_id="TC006",
        scenario_name="Standalone immediate danger goes urgent first",
        input_text="Applicant is in danger right now and needs help.",
        caller_profile="Standalone urgent emergency",
        manual_answers={},
        expected_route="Route A",
        expected_streams=["GEN3-T06"],
        expected_asked_questions=[],
        expected_auto_answered=[],
    ),
    make_case(
        test_id="TC007",
        scenario_name="Standalone minor goes vulnerability first",
        input_text="Applicant is a minor under 18 and needs help.",
        caller_profile="Standalone severe vulnerability",
        manual_answers={
            "GEN3-T13.Q1": "No, the applicant is under 18",
        },
        expected_route="Route B",
        expected_streams=["GEN3-T13"],
        expected_asked_questions=["GEN3-T13.Q1"],
        expected_auto_answered=[],
    ),
    make_case(
        test_id="TC008",
        scenario_name="Mild vulnerability in family stays main first",
        input_text="Applicant wants a divorce. She speaks limited English and may need an interpreter.",
        caller_profile="Family issue with one non-severe vulnerability cue",
        manual_answers={
            "GEN3-T01.Q1": ANSWER_NO,
            "GEN3-T01.Q2": ANSWER_SELF,
            "GEN3-T01.Q3": ANSWER_NO,
            "GEN3-T01.Q4": ANSWER_NO,
            "GEN3-T03.Q1": ANSWER_NO,
            "GEN3-T03.Q2": ANSWER_FOREIGNER,
        },
        expected_route=None,
        expected_streams=["GEN3-T01", "GEN3-T03"],
        expected_asked_questions=[
            "GEN3-T01.Q1",
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T03.Q1",
            "GEN3-T03.Q2",
            "GEN3-T03.Q4",
        ],
        expected_auto_answered=["GEN3-T13.Q1"],
    ),
    make_case(
        test_id="TC009",
        scenario_name="Family safety issue stays main first then triggers urgent",
        input_text="Applicant wants a divorce. She is not safe tonight and is a Singapore Citizen.",
        caller_profile="Family issue with urgent safety overlay",
        manual_answers={
            "GEN3-T01.Q1": ANSWER_NO,
            "GEN3-T01.Q2": ANSWER_SELF,
            "GEN3-T01.Q3": ANSWER_NO,
            "GEN3-T01.Q4": ANSWER_NO,
            "GEN3-T03.Q1": ANSWER_YES,
            "GEN3-T03.Q3": "Yes, passed or processing",
        },
        expected_route="Route C",
        expected_streams=["GEN3-T01", "GEN3-T03", "GEN3-T06"],
        expected_asked_questions=[
            "GEN3-T01.Q1",
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T03.Q1",
            "GEN3-T03.Q3",
        ],
        expected_auto_answered=[],
    ),
    make_case(
        test_id="TC010",
        scenario_name="Social worker alone does not force vulnerability takeover",
        input_text="Applicant has a debt issue and says her social worker told her to call.",
        caller_profile="Civil issue with social worker cue only",
        manual_answers={
            "GEN3-T01.Q1": ANSWER_NO,
            "GEN3-T01.Q2": ANSWER_SELF,
            "GEN3-T01.Q3": ANSWER_NO,
            "GEN3-T01.Q4": ANSWER_NO,
            "GEN3-T04.Q1": ANSWER_FOREIGNER,
        },
        expected_route="Route C",
        expected_streams=["GEN3-T01", "GEN3-T04"],
        expected_asked_questions=[
            "GEN3-T01.Q1",
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T04.Q1",
        ],
        expected_auto_answered=["GEN3-T13.Q1", "GEN3-T04.Q4"],
    ),
    make_case(
        test_id="TC011",
        scenario_name="Civil deadline stays main first with urgent overlay",
        input_text="Applicant has an employment issue and must file a response in 7 days. He has no lawyer.",
        caller_profile="Civil deadline overlay",
        manual_answers={
            "GEN3-T01.Q2": ANSWER_SELF,
            "GEN3-T01.Q3": ANSWER_NO,
            "GEN3-T01.Q4": ANSWER_NO,
            "GEN3-T04.Q1": ANSWER_FOREIGNER,
        },
        expected_route=None,
        expected_streams=["GEN3-T01", "GEN3-T04"],
        expected_asked_questions=[
            "GEN3-T01.Q2",
            "GEN3-T01.Q3",
            "GEN3-T01.Q4",
            "GEN3-T04.Q1",
            "GEN3-T04.Q4",
        ],
        expected_auto_answered=["GEN3-T06.Q1"],
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
                "completed": True,
            }

        targets = extract_question_targets(content)
        if not targets:
            return {
                "asked_questions": asked_questions,
                "streams_seen": streams_seen,
                "route_label": None,
                "final_content": content,
                "completed": False,
            }

        target = targets[-1]
        question_key = f"{(target.entry_id or selected_entry_id)}.{target.question_id}"
        asked_questions.append(question_key)

        answer = case["manual_answers"].get(question_key)
        if answer is None:
            return {
                "asked_questions": asked_questions,
                "streams_seen": streams_seen,
                "route_label": None,
                "final_content": content,
                "completed": False,
            }

        result = engine.execute_locked_turn(messages, answer)
        assert result is not None, f"{case['test_id']} locked turn returned None for {question_key}={answer}"
        messages.extend(
            [
                {"role": "user", "content": answer},
                {"role": "assistant", "content": result.content},
            ]
        )

    pytest.fail(f"{case['test_id']} did not reach a route within the turn limit")


def assert_case_matches(case: dict[str, Any], result: dict[str, Any]) -> None:
    assert result["streams_seen"] == case["expected"]["streams"]
    assert result["asked_questions"] == case["expected"]["asked_questions"]
    for question_id in case["expected"]["auto_answered"]:
        assert question_id not in result["asked_questions"]
    expected_route = case["expected"]["route"]
    if expected_route is None:
        assert result["completed"] is False
        assert result["route_label"] is None
        assert "Routing Recommendation" not in result["final_content"]
    else:
        assert result["completed"] is True
        assert result["route_label"] == expected_route
        assert "Routing Recommendation" in result["final_content"]
    if "GEN3-T13.Q1" in case["expected"]["auto_answered"]:
        assert "GEN3-T13" not in result["streams_seen"]
    if "GEN3-T06.Q1" in case["expected"]["auto_answered"]:
        assert "GEN3-T06" not in result["streams_seen"]


@pytest.mark.parametrize("case", STORYLINE_CASES, ids=[case["test_id"] for case in STORYLINE_CASES])
def test_storyline_cases_match_expected_behavior(case: dict[str, Any]):
    result = run_storyline_case(case)
    assert_case_matches(case, result)


@pytest.mark.parametrize("case", STORYLINE_CASES, ids=[case["test_id"] for case in STORYLINE_CASES])
def test_storyline_cases_route_presence_matches_expectation(case: dict[str, Any]):
    result = run_storyline_case(case)

    if case["expected"]["route"] is None:
        assert result["route_label"] is None
        assert result["completed"] is False
    else:
        assert result["route_label"] == case["expected"]["route"]
        assert result["completed"] is True


@pytest.mark.parametrize("case", STORYLINE_CASES, ids=[case["test_id"] for case in STORYLINE_CASES])
def test_storyline_cases_keep_overlay_streams_nested_or_monitored(case: dict[str, Any]):
    result = run_storyline_case(case)

    if "GEN3-T13.Q1" in case["expected"]["auto_answered"]:
        assert "GEN3-T13" not in result["streams_seen"]
    if "GEN3-T06.Q1" in case["expected"]["auto_answered"]:
        assert "GEN3-T06" not in result["streams_seen"]
    if case["expected"]["route"] is None:
        assert result["completed"] is False
        assert result["final_content"]
        assert "Ask the applicant" in result["final_content"] or "Type the applicant's answer here" in result["final_content"]
        if "GEN3-T06.Q1" in case["expected"]["auto_answered"]:
            assert "Urgent Support Stream" in result["final_content"] or "GEN3-T06" not in result["streams_seen"]
        if "GEN3-T13.Q1" in case["expected"]["auto_answered"]:
            assert "Vulnerability Support Stream" in result["final_content"] or "GEN3-T13" not in result["streams_seen"]
