import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import pbsg_triage_state
from pbsg_triage_state import (
    GOLDEN_SET_RELATIVE_DIR,
    PBSGRoutingEngine,
    PBSGTriageFact,
    PBSGTriageState,
    PBSGWorkflowGraph,
    branch_key_for_answer,
    build_triage_state,
    candidate_golden_set_dirs,
    collapse_duplicate_route_cards,
    convert_question_to_second_person,
    initial_extracted_fact_supported,
    label_from_branch_key,
    load_golden_set_entries,
    parse_transition_outcome,
    deadline_branch_key_from_text,
    resolve_gen3_t13_cue_transition,
    resolve_initial_topic,
    resolve_initial_topics,
)


GOLDEN_SET_DIR = Path(__file__).resolve().parents[1] / "data" / "pbsg_golden_set_by_id"


def load_entries() -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for path in GOLDEN_SET_DIR.glob("*.json"):
        entry = json.loads(path.read_text())
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and isinstance(entry.get("branching_logic"), dict):
            entries[entry_id] = entry
    return entries


def test_default_golden_set_loader_resolves_repo_data():
    entries = load_golden_set_entries()

    assert {"GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04", "GEN3-T06"}.issubset(entries)


def test_golden_set_candidates_include_local_and_container_layouts():
    candidates = {candidate.resolve(strict=False) for candidate in candidate_golden_set_dirs()}

    assert (Path.cwd() / GOLDEN_SET_RELATIVE_DIR).resolve(strict=False) in candidates
    assert (Path(__file__).resolve().parents[1] / GOLDEN_SET_RELATIVE_DIR).resolve(strict=False) in candidates


@pytest.mark.parametrize("query", ["hi", "I need help", "start triage", "general enquiry"])
def test_initial_topic_resolver_defaults_vague_starts_to_gen3_t01(query):
    entries = load_entries()

    resolution = resolve_initial_topic(entries, query)

    assert resolution is not None
    assert resolution.entry_id == "GEN3-T01"
    assert resolution.confidence < 0.7


@pytest.mark.parametrize(
    ("query", "expected_entry_id"),
    [
        ("Applicant has a criminal charge in court.", "GEN3-T02"),
        ("Applicant wants a divorce and has custody issues.", "GEN3-T03"),
        ("Applicant's employer has not paid salary for three months.", "GEN3-T04"),
    ],
)
def test_initial_topic_resolver_selects_clear_specialty_pillars(query, expected_entry_id):
    entries = load_entries()

    resolution = resolve_initial_topic(entries, query)

    assert resolution is not None
    assert resolution.entry_id == expected_entry_id
    assert resolution.confidence >= 0.6


def test_initial_topic_resolver_queues_multiple_specialty_topics_and_deadline_monitor(monkeypatch):
    entries = load_entries()
    monkeypatch.setattr(pbsg_triage_state, "current_local_date", lambda: date(2026, 5, 24))

    resolution = resolve_initial_topic(
        entries,
        "Applicant says she's a criminal. She also wants to divorce her husband. "
        "She's a Singapore Citizen and will be charged on 28th May 2026.",
    )

    assert resolution is not None
    assert resolution.entry_id == "GEN3-T02"
    assert [topic.entry_id for topic in resolution.queued_topics] == ["GEN3-T03"]
    assert "GEN3-T06" in resolution.overlays
    assert "weak or ambiguous" not in resolution.reason


def test_initial_topic_resolver_treats_vulnerability_as_overlay_when_legal_issue_exists():
    entries = load_entries()

    resolution = resolve_initial_topic(entries, "Elderly applicant is confused and has a debt issue.")

    assert resolution is not None
    assert resolution.entry_id == "GEN3-T04"
    assert "GEN3-T13" in resolution.overlays


def test_initial_topic_resolver_allows_gen3_t13_when_vulnerability_is_the_topic():
    entries = load_entries()

    resolution = resolve_initial_topic(entries, "Applicant is elderly and confused.")

    assert resolution is not None
    assert resolution.entry_id == "GEN3-T13"


def route_labels(entry: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    routing = entry.get("routing")
    if not isinstance(routing, list):
        return labels
    for route in routing:
        if not isinstance(route, str):
            continue
        match = re.match(r"Route\s+([A-Z])\b", route)
        if match:
            labels.add(f"Route {match.group(1)}")
    return labels


def test_gen3_routes_all_have_structured_read_aloud_cards():
    entries = load_entries()

    for entry_id, entry in entries.items():
        if not entry_id.startswith("GEN3-"):
            continue
        routes = route_labels(entry)
        routing_structured = entry.get("routing_structured")
        assert isinstance(routing_structured, dict), f"{entry_id} must define routing_structured"
        assert routes <= set(routing_structured), f"{entry_id} missing structured routes: {sorted(routes - set(routing_structured))}"

        for route_label in sorted(routes):
            card = routing_structured[route_label]
            assert isinstance(card, dict), f"{entry_id} {route_label} structured card must be an object"
            script = card.get("script")
            assert isinstance(script, str) and script.strip(), f"{entry_id} {route_label} must define script"
            assert not re.search(
                r"\b(Inform applicant|Inform the applicant|Share about|Advise the applicant|Ask the applicant to|Take down|Forward the details)\b",
                script,
                flags=re.IGNORECASE,
            ), f"{entry_id} {route_label} script contains internal instruction wording: {script}"

            access = card.get("access")
            if isinstance(access, list) and any(isinstance(item, str) and "http" in item for item in access):
                assert "http" not in script, f"{entry_id} {route_label} script should keep raw URLs in access"


def test_duplicate_route_card_guard_keeps_first_matching_route_card():
    content = """**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "First LASCO wording."

**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "Repeated LASCO wording."
"""

    collapsed = collapse_duplicate_route_cards(content)

    assert collapsed is not None
    assert collapsed.count("**Selected Entry:**") == 1
    assert "First LASCO wording" in collapsed
    assert "Repeated LASCO wording" not in collapsed


def test_duplicate_route_card_guard_collapses_repeated_cards_without_dropping_distinct_routes():
    content = """Intro note.

**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "First LASCO wording."

**Selected Entry:** GEN3-T03

**Routing Recommendation:** Route B (LAB)

**Tell the applicant:**

> "Family wording."

**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

**Tell the applicant:**

> "Repeated LASCO wording."
"""

    collapsed = collapse_duplicate_route_cards(content)

    assert collapsed is not None
    assert collapsed.startswith("Intro note.")
    assert collapsed.count("**Selected Entry:** GEN3-T02") == 1
    assert collapsed.count("**Selected Entry:** GEN3-T03") == 1
    assert "First LASCO wording" in collapsed
    assert "Family wording" in collapsed
    assert "Repeated LASCO wording" not in collapsed


def test_pbsg_golden_set_branching_graph_targets_are_valid():
    entries = load_entries()
    assert entries

    for entry_id, entry in entries.items():
        branching_logic = entry["branching_logic"]
        routes = route_labels(entry)
        for question_id, question_node in branching_logic.items():
            if not isinstance(question_node, dict):
                continue
            for branch_key, outcome in question_node.items():
                if not branch_key.startswith("if_"):
                    continue
                assert isinstance(outcome, str), f"{entry_id} {question_id}.{branch_key} must be text"
                transition = parse_transition_outcome(entries, entry_id, question_id, branch_key, outcome)
                if transition.transition_type in {"proceed_question", "concurrent_route_question"}:
                    assert transition.target_question_id in branching_logic, (
                        f"{entry_id} {question_id}.{branch_key} points to missing "
                        f"{transition.target_question_id}"
                    )
                    assert transition.target_entry_id == entry_id
                elif transition.transition_type in {"handoff_entry", "nested_stream", "cross_reference"}:
                    assert transition.target_entry_id in entries, (
                        f"{entry_id} {question_id}.{branch_key} points to missing "
                        f"{transition.target_entry_id}"
                    )
                elif transition.transition_type == "terminal_route":
                    assert transition.route_label in routes, (
                        f"{entry_id} {question_id}.{branch_key} references missing "
                        f"{transition.route_label}"
                    )
                elif transition.transition_type == "clarification":
                    assert transition.target_entry_id == entry_id
                    assert transition.target_question_id == question_id
                    assert transition.clarification_text
                elif transition.transition_type == "instruction":
                    assert outcome
                else:
                    pytest.fail(f"{entry_id} {question_id}.{branch_key} has unsupported outcome: {outcome}")


def test_pbsg_branch_resolver_supports_every_branch_label():
    entries = load_entries()

    for entry_id, entry in entries.items():
        for question_id, question_node in entry["branching_logic"].items():
            for branch_key in question_node:
                if not branch_key.startswith("if_"):
                    continue
                label = label_from_branch_key(branch_key)
                resolved = branch_key_for_answer(question_node, label)
                assert resolved == branch_key, f"{entry_id} {question_id} did not resolve label {label}"


def test_pbsg_workflow_graph_has_edge_for_every_gen3_branch():
    entries = load_entries()
    graph = PBSGWorkflowGraph(entries)

    for entry_id, entry in entries.items():
        if not entry_id.startswith("GEN3-"):
            continue
        for question_id, question_node in entry["branching_logic"].items():
            if not isinstance(question_node, dict):
                continue
            for branch_key in question_node:
                if branch_key.startswith("if_"):
                    assert graph.edge_for(entry_id, question_id, branch_key), f"Missing graph edge for {entry_id} {question_id}.{branch_key}"


def test_pbsg_routing_engine_renders_every_deterministic_branch():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)

    for entry_id, entry in entries.items():
        for question_id, question_node in entry["branching_logic"].items():
            if not isinstance(question_node, dict):
                continue
            for branch_key, outcome in question_node.items():
                if not branch_key.startswith("if_"):
                    continue
                transition = parse_transition_outcome(entries, entry_id, question_id, branch_key, outcome)
                content = engine.render_transition(transition)
                if transition.transition_type == "instruction":
                    assert content is None
                    continue
                assert content, f"{entry_id} {question_id}.{branch_key} did not render"
                assert "**Selected Entry:**" in content


def test_pbsg_terminal_routes_render_single_route_card_for_every_gen3_branch():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)

    for entry_id, entry in entries.items():
        if not entry_id.startswith("GEN3-"):
            continue
        for question_id, question_node in entry["branching_logic"].items():
            if not isinstance(question_node, dict):
                continue
            for branch_key, outcome in question_node.items():
                if not branch_key.startswith("if_") or not isinstance(outcome, str):
                    continue
                transition = parse_transition_outcome(entries, entry_id, question_id, branch_key, outcome)
                if transition.transition_type != "terminal_route":
                    continue
                content = engine.render_transition(transition)
                assert content is not None
                assert content.count("**Selected Entry:**") == 1, f"{entry_id} {question_id}.{branch_key}"
                assert content.count("**Routing Recommendation:**") == 1, f"{entry_id} {question_id}.{branch_key}"


def test_pbsg_cross_reference_routes_are_explicit_nested_edges():
    entries = load_entries()

    t02_q2_yes = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q2",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q2"]["if_yes"],
    )
    assert t02_q2_yes.transition_type == "nested_stream"
    assert t02_q2_yes.nested_entry_id == "GEN3-T06"
    assert t02_q2_yes.resume_entry_id == "GEN3-T02"
    assert t02_q2_yes.resume_question_id == "Q3"

    t03_q1_yes = parse_transition_outcome(
        entries,
        "GEN3-T03",
        "Q1",
        "if_yes",
        entries["GEN3-T03"]["branching_logic"]["Q1"]["if_yes"],
    )
    assert t03_q1_yes.transition_type == "nested_stream"
    assert t03_q1_yes.nested_entry_id == "GEN3-T06"
    assert t03_q1_yes.resume_entry_id == "GEN3-T03"
    assert t03_q1_yes.resume_question_id == "Q2"


def test_nested_urgent_transition_renders_structured_card():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q2",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q2"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Active stream:** GEN3-T06 urgent concurrent path" in content
    assert "**Why this stream is triggered:**" in content
    assert "**Tell the applicant:**" in content
    assert "Your criminal matter may also have an urgent deadline or safety concern" in content
    assert "Now checking: GEN3-T06 Q1" in content
    assert "After this urgent path: resume GEN3-T02 Q3" in content
    assert "Which urgent concern is actually present" in content
    assert "**Ask the applicant (read verbatim):**" in content


def test_pbsg_known_cross_routing_regressions():
    entries = load_entries()

    t02_q3_yes = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q3",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q3"]["if_yes"],
    )
    assert t02_q3_yes.transition_type == "proceed_question"
    assert t02_q3_yes.target_entry_id == "GEN3-T02"
    assert t02_q3_yes.target_question_id == "Q4"

    t02_q3_no = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q3",
        "if_no",
        entries["GEN3-T02"]["branching_logic"]["Q3"]["if_no"],
    )
    assert t02_q3_no.transition_type == "handoff_entry"
    assert t02_q3_no.target_entry_id == "GEN3-T04"

    t03_q2_foreigner = parse_transition_outcome(
        entries,
        "GEN3-T03",
        "Q2",
        "if_no_foreigner",
        entries["GEN3-T03"]["branching_logic"]["Q2"]["if_no_foreigner"],
    )
    assert t03_q2_foreigner.transition_type == "proceed_question"
    assert t03_q2_foreigner.target_entry_id == "GEN3-T03"
    assert t03_q2_foreigner.target_question_id == "Q4"

    t03_q4_no = parse_transition_outcome(
        entries,
        "GEN3-T03",
        "Q4",
        "if_no",
        entries["GEN3-T03"]["branching_logic"]["Q4"]["if_no"],
    )
    assert t03_q4_no.transition_type == "handoff_entry"
    assert t03_q4_no.target_entry_id == "GEN3-T04"

    t04_q1_foreigner = parse_transition_outcome(
        entries,
        "GEN3-T04",
        "Q1",
        "if_no_foreigner",
        entries["GEN3-T04"]["branching_logic"]["Q1"]["if_no_foreigner"],
    )
    assert t04_q1_foreigner.transition_type == "proceed_question"
    assert t04_q1_foreigner.target_entry_id == "GEN3-T04"
    assert t04_q1_foreigner.target_question_id == "Q4"

    t06_q1_basic = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q1",
        "if_basic_needs_or_child_welfare",
        entries["GEN3-T06"]["branching_logic"]["Q1"]["if_basic_needs_or_child_welfare"],
    )
    assert t06_q1_basic.transition_type == "concurrent_route_question"
    assert t06_q1_basic.route_label == "Route B"
    assert t06_q1_basic.target_entry_id == "GEN3-T06"
    assert t06_q1_basic.target_question_id == "Q5"

    t06_q4_yes = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q4",
        "if_yes",
        entries["GEN3-T06"]["branching_logic"]["Q4"]["if_yes"],
    )
    assert t06_q4_yes.transition_type == "concurrent_route_question"
    assert t06_q4_yes.route_label == "Route C"
    assert t06_q4_yes.target_entry_id == "GEN3-T06"
    assert t06_q4_yes.target_question_id == "Q5"

    t06_q5_yes = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q5",
        "if_yes",
        entries["GEN3-T06"]["branching_logic"]["Q5"]["if_yes"],
    )
    assert t06_q5_yes.transition_type == "handoff_entry"
    assert t06_q5_yes.target_entry_id == "GEN3-T01"
    assert t06_q5_yes.target_question_id == "Q1"


def test_nested_gen3_t06_resumes_parent_stream_after_urgent_route():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    state = PBSGTriageState(
        mode="FAST_ROUTING",
        workflow_id="GEN3-T02",
        workflow_locked=True,
        active_workflow="GEN3-T06",
        pending_entry_id="GEN3-T06",
        current_question_id="Q4",
    )

    transition = engine.resolve_transition_from_branch(state, "if_yes")

    assert transition is not None
    assert transition.transition_type == "concurrent_route_question"
    assert transition.route_label == "Route C"
    assert transition.target_entry_id == "GEN3-T02"
    assert transition.target_question_id == "Q3"
    assert transition.resume_entry_id == "GEN3-T02"
    assert transition.resume_question_id == "Q3"


def test_standalone_gen3_t06_route_d_hands_off_to_gen3_t01():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q5",
        "if_yes",
        entries["GEN3-T06"]["branching_logic"]["Q5"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Selected Entry:** GEN3-T01" in content
    assert "Handoff: GEN3-T06 → GEN3-T01" in content
    assert "Next question: Q1 from GEN3-T01" in content


def test_gen3_t13_cue_router_routes_minor_to_special_handling():
    transition = resolve_gen3_t13_cue_transition("The applicant is a minor under 18 and needs help.", "Q5")

    assert transition is not None
    assert transition.transition_type == "terminal_route"
    assert transition.route_label == "Route B"


def test_gen3_t13_cue_router_routes_multiple_cues_to_high_vulnerability():
    transition = resolve_gen3_t13_cue_transition("The applicant is elderly, confused, and has a social worker.", "Q4")

    assert transition is not None
    assert transition.transition_type == "terminal_route"
    assert transition.route_label == "Route A"


def test_gen3_t13_cue_router_routes_single_non_severe_cue_to_adapted_triage():
    transition = resolve_gen3_t13_cue_transition("The applicant has limited English and may need an interpreter.", "Q2")

    assert transition is not None
    assert transition.transition_type == "terminal_route"
    assert transition.route_label == "Route C"


def test_gen3_t13_safety_cue_triggers_urgent_stream():
    transition = resolve_gen3_t13_cue_transition("The applicant is not safe tonight and has no shelter.", "Q3")

    assert transition is not None
    assert transition.transition_type == "cross_reference"
    assert transition.target_entry_id == "GEN3-T06"
    assert transition.target_question_id == "Q1"


def test_routing_engine_executes_gen3_t13_cue_without_llm():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T13

**Ask the applicant (read verbatim):**

> **Q5: "Can I just check — are you 18 or older?"**""",
        }
    ]

    result = engine.execute_locked_turn(messages, "No, the applicant is under 18.")

    assert result is not None
    assert result.transition.entry_id == "GEN3-T13"
    assert result.transition.route_label == "Route B"
    assert "Minor — Special Handling" in result.content


def test_pbsg_routing_engine_renders_terminal_route_from_active_entry():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q1",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q1"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Selected Entry:** GEN3-T02" in content
    assert "**Routing Recommendation:** Route A" in content
    assert "LASCO" in content
    assert "**Tell the applicant:**" in content
    assert f'> **"{entries["GEN3-T02"]["routing"][0]}"**' not in content


def test_pbsg_routing_engine_renders_handoff_to_target_entry_q1():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q3",
        "if_no",
        entries["GEN3-T02"]["branching_logic"]["Q3"]["if_no"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Selected Entry:** GEN3-T04" in content
    assert "Handoff: GEN3-T02 → GEN3-T04" in content
    assert "Next question: Q1 from GEN3-T04" in content


def test_pbsg_routing_engine_reuses_fact_on_handoff_to_avoid_reasking_q1():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T03

**Ask the applicant (read verbatim):**

> **Q2: "Is the applicant a Singapore Citizen or PR?"**""",
        },
        {"role": "user", "content": "She is here on a work permit."},
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T03

What I gathered from your description:

- Q2: No, foreigner [GEN3-T03.json]

Triage progress:

- Last answered: Q2 = No, foreigner → Proceed to Q4 [GEN3-T03.json]
- Next question: Q4 from GEN3-T03

**Ask the applicant (read verbatim):**

> **Q4: "(Foreigner path) Does the applicant have at least one Singaporean child (under 21)?"**""",
        }
    ]

    result = engine.execute_locked_turn(messages, "No")

    assert result is not None
    content = result.content
    assert "**Selected Entry:** GEN3-T04" in content
    assert "Carried over from user_turn_1: foreigner" in content
    assert "Next question: Q4 from GEN3-T04" in content
    assert 'Q1: "Are you a Singapore Citizen or PR?"' not in content
    assert result.state.pending_entry_id == "GEN3-T04"
    assert result.state.current_question_id == "Q4"
    assert result.state.unanswered_required_fields == ["applicant.means_status"]


def test_pbsg_triage_state_preserves_queued_workflows_after_terminal_route():
    entries = load_entries()
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

Topics identified:
1. GEN3-T02 — active workflow
2. GEN3-T03 — queued workflow (noted from: divorce)

**Ask the applicant (read verbatim):**

> **Q1: "Is the offence a capital offence (punishable with death)?"**""",
        },
        {"role": "user", "content": "Yes"},
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T02

**Routing Recommendation:** Route A (LASCO)

Topics identified:
1. GEN3-T02 — routed workflow
2. GEN3-T03 — queued workflow""",
        },
    ]

    state = build_triage_state(messages, entries, "ok")

    assert state.completed_workflows == ["GEN3-T02"]
    assert state.queued_workflows == ["GEN3-T03"]
    assert state.routing_completion_status == "awaiting_topic_resolution"


def test_user_fact_ledger_supersedes_corrected_residency_fact():
    entries = load_entries()
    messages = [
        {"role": "user", "content": "Applicant is on a work permit and wants help with divorce."},
        {"role": "user", "content": "Actually she is a PR."},
    ]

    state = build_triage_state(messages, entries, "")
    residency_facts = [fact for fact in state.fact_ledger if fact.fact_key == "applicant.residency_status"]

    assert len(residency_facts) == 2
    assert residency_facts[0].normalized_value == "foreigner"
    assert residency_facts[0].status == "superseded"
    assert residency_facts[1].normalized_value == "sgc_pr"
    assert residency_facts[1].status == "active"


def test_user_fact_ledger_extracts_no_income_private_housing_as_hardship():
    entries = load_entries()

    state = build_triage_state(
        [{"role": "user", "content": "no income, stays condo"}],
        entries,
        "",
    )
    active_facts = {fact.fact_key: fact for fact in state.fact_ledger if fact.status == "active"}

    assert active_facts["applicant.income_status"].normalized_value == "no_income"
    assert active_facts["applicant.housing_type"].normalized_value == "private_housing"
    assert active_facts["applicant.financial_hardship"].normalized_value == "true"
    assert active_facts["applicant.means_status"].normalized_value == "marginal_or_exceptional"
    assert active_facts["applicant.means_status"].branch_value == "if_no_marginal_or_exceptional"


def test_user_fact_ledger_derives_fjss_pchi_from_income_child_and_hdb():
    entries = load_entries()
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T03

**Ask the applicant (read verbatim):**

> **Q4: "Do you have at least one Singaporean child (under 21)?"**""",
        },
        {"role": "user", "content": "Yes"},
    ]

    state = build_triage_state(messages, entries, "currently earning $2k, staying in 3 rm HDB")
    active_facts = {fact.fact_key: fact for fact in state.fact_ledger if fact.status == "active"}

    assert active_facts["applicant.monthly_income"].normalized_value == "2000"
    assert active_facts["applicant.housing_type"].normalized_value == "non_private_housing"
    assert active_facts["applicant.means_status"].normalized_value == "fjss_pro_bono_qualifying"
    assert active_facts["applicant.means_status"].branch_value == "if_yes"


def test_structured_means_metadata_present_for_means_nodes():
    entries = load_entries()

    assert "means_test_structured" in entries["GEN3-T02"]["branching_logic"]["Q6"]
    assert "means_test_structured" in entries["GEN3-T03"]["branching_logic"]["Q5"]
    assert "means_test_structured" in entries["GEN3-T04"]["branching_logic"]["Q4"]


def test_private_housing_from_initial_query_does_not_auto_skip_gen3_t03_q5():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    messages: list[dict[str, str]] = []
    turns = [
        "Applicant married in December 2025, stays in condo and seeks help with divorce",
        "No",
        "Yes",
        "Yes, failed means test",
    ]

    for index, turn in enumerate(turns):
        result = engine.execute_initial_turn(turn) if index == 0 else engine.execute_locked_turn(messages, turn)
        assert result is not None
        messages.extend([{"role": "user", "content": turn}, {"role": "assistant", "content": result.content}])

    assert "Next question: Q5 from GEN3-T03" in messages[-1]["content"]
    assert "Route F" not in messages[-1]["content"]


def test_criminal_initial_context_extracts_reusable_routing_facts():
    entries = load_entries()
    state = build_triage_state(
        [],
        entries,
        "caller said he has been charged in court for assault and has no legal representation. "
        "he is singaporean, no money to hire a lawyer",
    )
    active_facts = {fact.fact_key: fact for fact in state.fact_ledger if fact.status == "active"}

    assert active_facts["matter.capital_offence"].branch_value == "if_no"
    assert active_facts["matter.charged_in_court"].branch_value == "if_yes"
    assert active_facts["applicant.residency_status"].branch_value == "if_yes"
    assert active_facts["applicant.representation_status"].branch_value == "if_no"


def test_initial_structured_not_sure_fact_requires_explicit_user_uncertainty():
    fact = PBSGTriageFact(
        fact_key="applicant.residency_status",
        value="Not sure",
        normalized_value="not sure",
        source="structured_initial_extraction:GEN3-T04.Q1",
        source_type="structured_extraction",
        branch_value="if_not_sure",
        confidence=0.9,
    )

    assert not initial_extracted_fact_supported(
        fact, "Applicant has a landlord-tenant dispute and requires help. How do I triage?"
    )
    assert initial_extracted_fact_supported(
        fact, "Applicant has a landlord-tenant dispute and is not sure about their PR or citizenship status."
    )


def test_initial_routing_ignores_unsupported_not_sure_extraction():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    resolution = resolve_initial_topic(
        entries, "Applicant has a landlord-tenant dispute and requires help. How do I triage?"
    )
    assert resolution is not None
    bogus_fact = PBSGTriageFact(
        fact_key="applicant.residency_status",
        value="Not sure",
        normalized_value="not sure",
        source="structured_initial_extraction:GEN3-T04.Q1",
        source_type="structured_extraction",
        branch_value="if_not_sure",
        confidence=0.9,
    )

    result = engine.execute_initial_resolution(
        "Applicant has a landlord-tenant dispute and requires help. How do I triage?",
        resolution,
        extracted_facts=[bogus_fact],
    )

    assert result is not None
    assert result.state.pending_entry_id == "GEN3-T04"
    assert result.state.current_question_id == "Q1"
    assert "Applicant response: Not sure" not in result.content
    assert "Are you a Singapore Citizen or PR?" in result.content


def test_nested_urgent_deadline_resumes_criminal_parent_without_reasking_known_facts():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    messages: list[dict[str, str]] = []
    turns = [
        "caller said he has been charged in court for assault and has no legal representation. he is singaporean, no money to hire a lawyer",
        "yes, 7 days",
    ]

    for index, turn in enumerate(turns):
        result = engine.execute_initial_turn(turn) if index == 0 else engine.execute_locked_turn(messages, turn)
        assert result is not None
        messages.extend([{"role": "user", "content": turn}, {"role": "assistant", "content": result.content}])

    content = messages[-1]["content"]
    assert "Concurrent routing note" in content
    assert "Next question: Q5 from GEN3-T02" in content
    assert "GEN3-T02 Q5" in content
    assert "Have you been charged in court?" not in content
    assert "Is the applicant a Singapore Citizen or PR?" not in content
    assert "Immediate threat" not in content


def test_explicit_court_date_outside_14_days_does_not_trigger_urgent_stream(monkeypatch):
    monkeypatch.setattr("pbsg_triage_state.current_local_date", lambda: date(2026, 5, 23))
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    first = engine.execute_initial_turn("caller has been charged in court for assault")
    assert first is not None
    messages = [
        {"role": "user", "content": "caller has been charged in court for assault"},
        {"role": "assistant", "content": first.content},
    ]

    result = engine.execute_locked_turn(messages, "court date is 30 June")

    assert result is not None
    assert "GEN3-T06 urgent concurrent path" not in result.content
    assert "Next question: Q4 from GEN3-T02" in result.content


def test_explicit_court_date_inside_14_days_triggers_urgent_stream(monkeypatch):
    monkeypatch.setattr("pbsg_triage_state.current_local_date", lambda: date(2026, 5, 23))
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    first = engine.execute_initial_turn("caller has been charged in court for assault")
    assert first is not None
    messages = [
        {"role": "user", "content": "caller has been charged in court for assault"},
        {"role": "assistant", "content": first.content},
    ]

    result = engine.execute_locked_turn(messages, "court date is 1 June")

    assert result is not None
    assert "Concurrent routing note" in result.content
    assert "Route C" in result.content
    assert "Next question: Q4 from GEN3-T02" in result.content
    assert "Which urgent concern is actually present" not in result.content


def test_generic_criminal_terms_do_not_trigger_urgent_overlay():
    entries = load_entries()
    resolution = resolve_initial_topics(
        entries,
        "caller was charged by police and wants to know if he needs a lawyer for sentencing",
    )

    assert resolution is not None
    assert resolution.entry_id == "GEN3-T02"
    assert "GEN3-T06" not in resolution.overlays


def test_safety_and_basic_needs_facts_still_trigger_urgent_overlay():
    entries = load_entries()
    resolution = resolve_initial_topics(
        entries,
        "caller has a family matter and is not safe tonight with no shelter",
    )

    assert resolution is not None
    assert "GEN3-T06" in resolution.overlays


def test_next_week_without_deadline_context_is_not_deadline_urgency():
    assert deadline_branch_key_from_text("I am meeting my lawyer next week") is None


def test_numeric_court_date_outside_14_days_maps_no(monkeypatch):
    monkeypatch.setattr("pbsg_triage_state.current_local_date", lambda: date(2026, 5, 23))
    entries = load_entries()
    question_node = entries["GEN3-T02"]["branching_logic"]["Q2"]

    assert branch_key_for_answer(question_node, "court date is 30/6") == "if_no"


def test_gen3_t02_structured_means_routes_clas_standard_intake():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    state = build_triage_state([], entries, "35 yo, earning $1200, household size 1, no savings, staying in HDB")

    transition = engine.resolve_fact_transition(state, "GEN3-T02", "Q6")

    assert transition is not None
    assert transition.branch_key == "if_yes"
    assert transition.route_label == "Route E"


def test_gen3_t04_structured_means_routes_clear_well_over_rejection():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    state = build_triage_state([], entries, "45 yo, earning $12000, household size 1, savings $100000, stays condo")

    transition = engine.resolve_fact_transition(state, "GEN3-T04", "Q4")

    assert transition is not None
    assert transition.branch_key == "if_no_well_over_no_exceptions"
    assert transition.route_label == "Route D"


def test_gen3_t04_structured_means_routes_no_income_hardship_to_staff():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    state = build_triage_state([], entries, "no income, stays condo")

    transition = engine.resolve_fact_transition(state, "GEN3-T04", "Q4")

    assert transition is not None
    assert transition.branch_key == "if_no_marginal_or_exceptional"
    assert transition.route_label == "Route C"


def test_gen3_t03_lab_failure_maps_to_gen3_t04_lab_unable_to_assist():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    messages = [
        {
            "role": "assistant",
            "content": """**Selected Entry:** GEN3-T03

**Ask the applicant (read verbatim):**

> **Q3: "Has the applicant applied to the Legal Aid Bureau (LAB) for civil legal aid?"**""",
        },
        {"role": "user", "content": "Yes, failed means test"},
    ]
    state = build_triage_state(messages, entries, "")

    transition = engine.resolve_fact_transition(state, "GEN3-T04", "Q3")

    assert transition is not None
    assert transition.branch_key == "if_yes_lab_unable_to_assist"
    assert transition.target_entry_id == "GEN3-T04"
    assert transition.target_question_id == "Q4"


@pytest.mark.parametrize(
    ("entry_id", "question_id", "branch_key", "expected_route", "expected_text"),
    [
        ("GEN3-T01", "Q2", "if_calling_on_behalf_and_able_to_self_help", "Route B", "Email: help@probono.sg"),
        ("GEN3-T02", "Q6", "if_yes", "Route E", "Website / application link: https://www.probono.sg/get-legal-help/legal-representation"),
        ("GEN3-T04", "Q4", "if_no_well_over_no_exceptions", "Route D", "Third-party resources"),
        ("GEN3-T04", "Q4", "if_not_sure", "Route C", "Take down"),
    ],
)
def test_pbsg_terminal_route_cards_are_scannable(entry_id, question_id, branch_key, expected_route, expected_text):
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        entry_id,
        question_id,
        branch_key,
        entries[entry_id]["branching_logic"][question_id][branch_key],
    )

    content = engine.render_transition(transition)
    route_text = next(route for route in entries[entry_id]["routing"] if route.startswith(expected_route))

    assert content is not None
    assert f"**Routing Recommendation:** {expected_route}" in content
    assert "**Why this route applies:**" in content
    assert "**Tell the applicant:**" in content
    assert (
        "**What the applicant needs to know:**" in content
        or "**How to access this route:**" in content
        or "**Next steps for you (the intern):**" in content
    )
    assert expected_text in content
    assert f'> **"{route_text}"**' not in content


def test_clas_route_card_has_clean_script_and_no_duplicate_long_prose():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q6",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q6"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "you is" not in content.lower()
    assert "If you cannot self-apply" in content
    assert "Website / application link: https://www.probono.sg/get-legal-help/legal-representation" in content
    assert "Financial documents for the means test" in content
    assert "Charge details and other case details if asked on application" in content
    assert "Share the CLAS application link with the applicant" in content
    assert "If the applicant cannot self-apply, direct them to the PBSG Counter" in content

    tell_section = content.split("**Tell the applicant:**", 1)[1].split("**How to access this route:**", 1)[0]
    assert "https://www.probono.sg" not in tell_section
    assert "the PBSG Counter at the State Courts Help Centre with the required documents" in tell_section
    assert len(tell_section) < 520
    duplicated_source_sentence = (
        "Inform applicant to apply for CLAS: https://www.probono.sg/get-legal-help/legal-representation. "
        "If applicant is unable to self-apply, inform applicant to go to PBSG Counter"
    )
    assert duplicated_source_sentence not in content


def test_legal_clinic_route_card_uses_first_person_structured_script():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T04",
        "Q4",
        "if_yes",
        entries["GEN3-T04"]["branching_logic"]["Q4"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    tell_section = content.split("**Tell the applicant:**", 1)[1].split("**What the applicant needs to know:**", 1)[0]
    assert "You may be eligible for a Pro Bono SG Legal Clinic" in tell_section
    assert "Share about Legal Clinics" not in tell_section
    assert "Inform applicant" not in tell_section
    assert "https://www.probono.sg" not in tell_section
    assert "Website / application link: https://www.probono.sg/get-legal-help/legal-guidance/" in content


def test_terminal_route_prefers_structured_route_card_copy():
    entries = load_entries()
    entries["GEN3-T02"]["routing_structured"]["Route E"]["script"] = "Use this exact structured CLAS script."
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q6",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q6"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert '> **"Use this exact structured CLAS script."**' in content
    assert "Share about CLAS" not in content


def test_terminal_route_falls_back_to_parser_when_structured_card_missing():
    entries = load_entries()
    entries["GEN3-T02"] = json.loads(json.dumps(entries["GEN3-T02"]))
    entries["GEN3-T02"].pop("routing_structured", None)
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T02",
        "Q6",
        "if_yes",
        entries["GEN3-T02"]["branching_logic"]["Q6"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "You may be eligible for CLAS" in content
    assert "Share the CLAS application link with the applicant" in content


def test_gen3_t06_structured_route_card_renders_urgent_script():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q1",
        "if_immediate_safety_or_crisis",
        entries["GEN3-T06"]["branching_logic"]["Q1"]["if_immediate_safety_or_crisis"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Routing Recommendation:** Route A (Emergency / Crisis Support — IMMEDIATE)" in content
    assert "Please call 999 for police or 995 for ambulance now" in content
    assert "Police emergency: 999" in content


def test_gen3_t13_structured_route_card_renders_overlay_script():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    transition = parse_transition_outcome(
        entries,
        "GEN3-T13",
        "Q5",
        "if_under_18",
        entries["GEN3-T13"]["branching_logic"]["Q5"]["if_under_18"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Routing Recommendation:** Route B (Minor — Special Handling)" in content
    assert "I need PBSG Staff to handle this carefully" in content
    assert "Do not continue triage independently" in content


@pytest.mark.parametrize(
    ("entry_id", "question_id"),
    [
        ("GEN3-T02", "Q6"),
        ("GEN3-T03", "Q5"),
        ("GEN3-T04", "Q4"),
    ],
)
def test_means_questions_use_lowercase_mid_sentence_clauses(entry_id: str, question_id: str):
    entries = load_entries()
    question_node = entries[entry_id]["branching_logic"][question_id]
    converted = convert_question_to_second_person(question_node["question"])

    assert ", Do " not in converted
    assert ", do you " in converted
    assert " and do you " in converted
    assert "if you are younger than" in converted
    assert "if you are 60 years old or older" in converted


def test_convert_question_to_second_person_preserves_sentence_start_capitalization():
    assert convert_question_to_second_person("Is the applicant a Singapore Citizen or PR?") == (
        "Are you a Singapore Citizen or PR?"
    )
    assert convert_question_to_second_person("Has the applicant applied to the Legal Aid Bureau (LAB)?") == (
        "Have you applied to the Legal Aid Bureau (LAB)?"
    )
