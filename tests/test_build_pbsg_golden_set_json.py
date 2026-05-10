import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BUILD = _ROOT / "scripts" / "build_pbsg_golden_set_json.py"


@pytest.fixture(scope="module")
def pbsg_build():
    spec = importlib.util.spec_from_file_location("pbsg_build_golden", _BUILD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gen3_part_a_not_truncated_by_prose_part_b_reference(pbsg_build):
    body = """
Query
Example?
Variations
	•	Variation one.
Part A — Intern Briefing
Before branching, interns read probes in Part B. This sentence must stay in Part A.
More guidance here.
Part B — Branching Questions
Q1: First?
	•	If Yes → Route A
Part C — Routing Recommendation
Route A (X):
Do step 1.
Guardrails
	•	Never give legal advice.
""".strip()
    entry = pbsg_build.parse_gen3_entry("GEN3-T99", "Test Entry", body)
    assert "probes in Part B" in entry["part_a_general_info"]
    assert "More guidance here" in entry["part_a_general_info"]
    assert "Part B — Branching" not in entry["part_a_general_info"]


def test_gen3_branching_logic_preserves_order_including_pch_i_line(pbsg_build):
    body = """
Query
Criminal triage help?
Variations
	•	Charged in court.
Part A — Intern Briefing
Brief.
Part B — Branching Questions
Q5: Short question?
	•	If Q5 = No → next
Q6 (Means): Is the applicant eligible on income?
(PCHI = total monthly household income ÷ number of persons.)
	•	If Q6 = Yes → Route E
NOTE: Escalate if unclear.
Part C — Routing Recommendation
Route E (CLAS):
Apply here.
Guardrails
	•	Guard one.
""".strip()
    entry = pbsg_build.parse_gen3_entry("GEN3-T98", "Criminal sample", body)
    bl = entry["branching_logic"]
    assert isinstance(bl, dict)
    assert "Q5" in bl and bl["Q5"]["question"].startswith("Short question")
    assert bl["Q5"]["if_no"] == "next"
    assert "Q6" in bl
    assert "PCHI" in bl["Q6"].get("definition", "")
    assert bl["Q6"]["if_yes"] == "Route E"
    assert bl["note"].startswith("NOTE")


def test_gen3_triage_questions_include_parenthetical_q_labels(pbsg_build):
    body = """
Query
Help?
Variations
	•	V
Part A — Intern Briefing
A.
Part B — Branching Questions
Q4: Fourth?
	•	If Yes → A
Q5 (SGC/PR path): Fifth question text?
	•	If No → B
Q6 (Means): Sixth question?
	•	If Yes → C
Part C — Routing Recommendation
Route A (X):
Step.
Guardrails
	•	G1
""".strip()
    entry = pbsg_build.parse_gen3_entry("GEN3-T96", "Triage labels", body)
    ids = [q.split(":")[0] for q in entry["triage_questions"]]
    assert ids == ["Q4", "Q5 (SGC/PR path)", "Q6 (Means)"]


def test_gen3_part_b_not_truncated_by_inline_part_c_reference(pbsg_build):
    """If Part B ever mentions 'Part C' in prose, require full section header as end marker."""
    body = """
Query
Q?
Variations
	•	V
Part A — Intern Briefing
A text.
Part B — Branching Questions
Q1: Question?
	•	If Yes → see Part C routing table later.
Q2: Second?
	•	If No → Route B
Part C — Routing Recommendation
Route B (Y):
Done.
Guardrails
	•	G
""".strip()
    entry = pbsg_build.parse_gen3_entry("GEN3-T97", "Test", body)
    bl = entry["branching_logic"]
    flat = json.dumps(bl, ensure_ascii=False)
    assert "see Part C routing" in flat
    assert "Q2" in bl and "Second" in bl["Q2"]["question"]
