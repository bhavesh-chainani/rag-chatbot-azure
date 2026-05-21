import json
import re
from pathlib import Path
from typing import Any

import pytest

from pbsg_triage_state import (
    GOLDEN_SET_RELATIVE_DIR,
    PBSGRoutingEngine,
    branch_key_for_answer,
    candidate_golden_set_dirs,
    label_from_branch_key,
    load_golden_set_entries,
    parse_transition_outcome,
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
