import json
import re
from pathlib import Path
from typing import Any

import pytest

from pbsg_triage_state import (
    GOLDEN_SET_RELATIVE_DIR,
    PBSGRoutingEngine,
    PBSGTriageState,
    PBSGWorkflowGraph,
    branch_key_for_answer,
    candidate_golden_set_dirs,
    label_from_branch_key,
    load_golden_set_entries,
    parse_transition_outcome,
    resolve_gen3_t13_cue_transition,
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
    assert '**GEN3-T06 Q1: "Is there an immediate threat to your (or someone else\'s) life or physical safety right now?"**' in content
    assert "> **GEN3-T06 Q1" not in content


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

    t06_q2_not_sure = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q2",
        "if_not_sure",
        entries["GEN3-T06"]["branching_logic"]["Q2"]["if_not_sure"],
    )
    assert t06_q2_not_sure.transition_type == "concurrent_route_question"
    assert t06_q2_not_sure.route_label == "Route B"
    assert t06_q2_not_sure.target_entry_id == "GEN3-T06"
    assert t06_q2_not_sure.target_question_id == "Q3"

    t06_q3_not_sure = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q3",
        "if_not_sure",
        entries["GEN3-T06"]["branching_logic"]["Q3"]["if_not_sure"],
    )
    assert t06_q3_not_sure.transition_type == "concurrent_route_question"
    assert t06_q3_not_sure.route_label == "Route C"
    assert t06_q3_not_sure.target_entry_id == "GEN3-T06"
    assert t06_q3_not_sure.target_question_id == "Q4"

    t06_q4_yes = parse_transition_outcome(
        entries,
        "GEN3-T06",
        "Q4",
        "if_yes",
        entries["GEN3-T06"]["branching_logic"]["Q4"]["if_yes"],
    )
    assert t06_q4_yes.transition_type == "handoff_entry"
    assert t06_q4_yes.target_entry_id == "GEN3-T01"
    assert t06_q4_yes.target_question_id == "Q1"


def test_nested_gen3_t06_resumes_parent_stream_after_urgent_route():
    entries = load_entries()
    engine = PBSGRoutingEngine(entries)
    state = PBSGTriageState(
        mode="FAST_ROUTING",
        workflow_id="GEN3-T02",
        workflow_locked=True,
        active_workflow="GEN3-T06",
        pending_entry_id="GEN3-T06",
        current_question_id="Q3",
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
        "Q4",
        "if_yes",
        entries["GEN3-T06"]["branching_logic"]["Q4"]["if_yes"],
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
        "if_yes",
        entries["GEN3-T06"]["branching_logic"]["Q1"]["if_yes"],
    )

    content = engine.render_transition(transition)

    assert content is not None
    assert "**Routing Recommendation:** Route A (Emergency Services — IMMEDIATE)" in content
    assert "Please call 999 for police or 995 for ambulance immediately" in content
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
