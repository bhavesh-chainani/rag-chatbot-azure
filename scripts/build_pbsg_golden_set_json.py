import argparse
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "pbsg_golden_set_by_id"
DEFAULT_GEN3_DOCX = ROOT / "data" / "2026.04.16 PBSG_Golden_Set_General_Enquiries_v3.docx"
DEFAULT_LEGACY_DOCX = ROOT / "data" / "PBSG_Golden_Set_Complete_v2.docx"

ENTRY_HEADER_RE = re.compile(
    r"^([A-Z]{3}-\d{2})\s+\|\s+(.+?)$",
    re.MULTILINE,
)

GEN3_ENTRY_HEADER_RE = re.compile(
    r"^(GEN3-[A-Z0-9-]+)\s+—\s+(.+?)\s*$",
    re.MULTILINE,
)

CATEGORY_BY_PREFIX = {
    "EMP": "Employment Law",
    "FAM": "Family Law (Divorce & Related)",
    "EST": "Wills, Probate & Estate",
    "MCA": "Mental Capacity (LPA & Deputyship)",
    "HAR": "Personal Protection & Harassment (POHA)",
    "CON": "Consumer & Contracts",
    "CRM": "Criminal Law",
    "BKR": "Bankruptcy & Insolvency",
    "CIV": "Other Civil / Procedural",
}


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def apply_text_transform(obj: Any, fn: Callable[[str], str]) -> Any:
    """Recursively apply a text transform function to all strings in a nested structure."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [apply_text_transform(item, fn) for item in obj]
    if isinstance(obj, dict):
        return {k: apply_text_transform(v, fn) for k, v in obj.items()}
    return obj


def replace_caller_with_applicant(entry: dict) -> dict:
    """Replace 'caller' with 'applicant' in all string fields (preserving case)."""

    def replace_text(text: str) -> str:
        text = text.replace("Caller", "Applicant")
        text = text.replace("caller", "applicant")
        text = text.replace("CALLER", "APPLICANT")
        return text

    return cast(dict, apply_text_transform(entry, replace_text))


def normalize_gen3_handoff_ids(entry: dict) -> dict:
    """Map legacy placeholder ids from source documents to real per-id JSON filenames."""

    def fix_text(text: str) -> str:
        text = text.replace("GEN3-T-FAM", "GEN3-T03")
        text = text.replace("GEN3-T-CIV", "GEN3-T04")
        return text

    return cast(dict, apply_text_transform(entry, fix_text))


def normalize_gen3_t03_q4_not_sure(entry: dict) -> dict:
    """Q4 is the foreigner-path Singaporean-child question; Not Sure must not ask to clarify caller nationality (Q2)."""
    if entry.get("id") != "GEN3-T03":
        return entry
    bl = entry.get("branching_logic")
    replacement = (
        "Clarify whether the applicant has at least one child who is a "
        "Singapore Citizen and is below 21 years old (Q2 already established the applicant is not SGC/PR); "
        "if still unclear, Route F (Escalate to PBSG Staff)."
    )
    if isinstance(bl, dict):
        q4 = bl.get("Q4", {})
        for key, val in q4.items():
            if isinstance(val, str) and "nationality/residency" in val:
                q4[key] = replacement
        return entry
    if isinstance(bl, list):
        full_replacement = "If Q4 = Not Sure → " + replacement
        new_bl: list[str] = []
        for item in bl:
            if isinstance(item, str) and item.startswith("If Q4 = Not Sure") and "nationality/residency" in item:
                new_bl.append(full_replacement)
            else:
                new_bl.append(item)
        out = dict(entry)
        out["branching_logic"] = new_bl
        return out
    return entry


def extract_between(text: str, start_marker: str, end_markers: list[str]) -> str:
    try:
        start = text.index(start_marker) + len(start_marker)
    except ValueError:
        return ""
    end = len(text)
    for marker in end_markers:
        marker_pos = text.find(marker, start)
        if marker_pos != -1:
            end = min(end, marker_pos)
    return text[start:end].strip()


def section_between(text: str, start_marker: str, end_marker: str) -> str:
    try:
        start = text.index(start_marker) + len(start_marker)
        end = text.index(end_marker, start)
    except ValueError:
        return ""
    return text[start:end].strip()


def extract_list_items(section_text: str, prefixes: tuple[str, ...]) -> list[str]:
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(prefixes):
            cleaned = line
            if line.startswith("•") or line.startswith("◦"):
                cleaned = line[1:].strip()
            items.append(normalize_whitespace(cleaned).strip('"'))
    return items


def extract_gen3_routing(section_text: str) -> list[str]:
    """Extract full route descriptions (header + body) from Part C routing section.

    Each route starts with a 'Route <letter>' header and includes all following
    lines until the next route header or end of section.
    """
    route_header_re = re.compile(r"^Route\s+[A-Z]", re.MULTILINE)
    matches = list(route_header_re.finditer(section_text))
    if not matches:
        return extract_list_items(section_text, ("Route",))

    routes: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_text)
        block = section_text[start:end].strip()
        lines: list[str] = []
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("•", "◦")):
                line = line[1:].strip()
            lines.append(line)
        routes.append(normalize_whitespace(" ".join(lines)))
    return routes


def parse_entry(entry_id: str, topic: str, body: str) -> dict:
    user_query = normalize_whitespace(
        extract_between(body, "User Query", ["Variations", "Part A", "PART A: General Legal Information"]).replace(
            '"', ""
        )
    )
    variations_section = section_between(body, "Phrasing Variations (for RAG training)", "Part A")
    variations = extract_list_items(variations_section, ("•",))
    part_a = normalize_whitespace(
        extract_between(body, "PART A: General Legal Information", ["Part B", "PART B: Triage Clarifying Questions"])
    )
    triage_section = section_between(body, "Sequential Questions:", "Branching Logic:")
    triage_questions = extract_list_items(triage_section, ("•",))
    branching_section = section_between(body, "Branching Logic:", "Part C")
    branching_logic = extract_list_items(branching_section, ("◦",))
    routing_section = section_between(body, "PART C: Routing Recommendation", "LPA Guardrail")
    routing = extract_list_items(routing_section, ("Route",))
    guardrail = normalize_whitespace(extract_between(body, "LPA Guardrail", ["\n\n", "\n\f", "\f"]))

    prefix = entry_id.split("-")[0]
    category = CATEGORY_BY_PREFIX.get(prefix, topic.split("—")[0].strip() if "—" in topic else topic.strip())

    return {
        "id": entry_id,
        "category": category,
        "topic": topic.strip(),
        "user_query": user_query,
        "variations": variations,
        "part_a_general_info": part_a,
        "triage_questions": triage_questions,
        "branching_logic": branching_logic,
        "routing": routing,
        "guardrail": guardrail,
    }


def gen3_header_matches(text: str) -> list[re.Match[str]]:
    matches = list(GEN3_ENTRY_HEADER_RE.finditer(text))
    return [m for m in matches if "\t" not in m.group(0)]


def extract_gen3_variations(section_text: str) -> list[str]:
    items: list[str] = []
    for raw_line in section_text.splitlines():
        line_stripped = raw_line.strip()
        if not line_stripped:
            continue
        if line_stripped.startswith("•") or line_stripped.startswith("◦"):
            cleaned = line_stripped[1:].strip()
            if cleaned:
                items.append(normalize_whitespace(cleaned).strip('"'))
    return items


def extract_gen3_triage_questions(part_b: str) -> list[str]:
    # Q1:, Q4A:, Q5 (SGC/PR path):, Q6 (Means):
    line_re = re.compile(r"^Q\d+(?:[A-Z]+|\s*\([^)]*\))?\s*:\s*.+$", re.MULTILINE)
    return [normalize_whitespace(m.group(0)) for m in line_re.finditer(part_b)]


def extract_gen3_branching_logic(part_b: str) -> dict:
    """Extract Part B branching questions into a structured dict keyed by question id.

    Each question becomes a dict with 'question' and 'if_*' keys mapping conditions
    to outcomes (Route or Proceed).
    """
    lines: list[str] = []
    for raw in part_b.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^Q\d+", line):
            lines.append(normalize_whitespace(line))
        elif line.startswith("("):
            lines.append(normalize_whitespace(line))
        elif line.startswith("NOTE"):
            lines.append(normalize_whitespace(line))
        elif line.startswith(("•", "◦")):
            bullet = line[1:].strip()
            if bullet:
                lines.append(normalize_whitespace(bullet).strip('"'))

    return branching_lines_to_dict(lines)


def condition_to_key(condition: str) -> str:
    """Convert a branching condition like 'Yes', 'No (foreigner)', 'Not Sure' to a snake_case if_* key."""
    c = condition.strip().rstrip(".")
    paren_match = re.search(r"\(([^)]+)\)", c)
    paren_suffix = ""
    if paren_match:
        paren_text = paren_match.group(1).strip()
        short = re.sub(r"[^a-z0-9]", "_", paren_text.lower())
        short = re.sub(r"_+", "_", short).strip("_")
        if len(short) <= 20:
            paren_suffix = f"_{short}"
        c = re.sub(r"\s*\(.*?\)\s*", " ", c).strip()
    c = c.lower()
    c = c.replace("not sure", "not_sure")
    c = c.replace("/", "_or_")
    c = re.sub(r"[^a-z0-9_]", "_", c)
    c = re.sub(r"_+", "_", c).strip("_")
    c += paren_suffix
    if len(c) > 60:
        c = c[:60].rstrip("_")
    return f"if_{c}"


def branching_lines_to_dict(lines: list[str]) -> dict:
    """Convert flat branching logic lines into a structured dict keyed by question id."""
    result: dict = {}
    current_q: str | None = None
    q_header_re = re.compile(r"^(Q\d+[A-Z]?)(?:\s*\([^)]*\))?\s*[:\u2014]\s*(.+)$")
    arrow = r"(?:\u2192|->)"
    if_re = re.compile(rf"^If\s+(Q\d+[A-Z]?)\s*=\s*(.+?)\s*{arrow}\s*(.+)$")
    short_if_re = re.compile(
        rf"^If\s+(Yes|No|Not\s+Sure)\s*{arrow}\s*(.+)$",
        re.IGNORECASE,
    )

    for line in lines:
        q_match = q_header_re.match(line)
        if q_match:
            current_q = q_match.group(1)
            question_text = q_match.group(2).strip()
            if current_q not in result:
                result[current_q] = {}
            result[current_q]["question"] = question_text
            continue

        if_match = if_re.match(line)
        if if_match:
            q_id = if_match.group(1)
            cond = if_match.group(2).strip()
            outcome = if_match.group(3).strip()
            key = condition_to_key(cond)
            if q_id not in result:
                result[q_id] = {}
            if q_id != current_q and current_q and q_id not in result:
                result[q_id] = {}
            result[q_id][key] = outcome
            continue

        short_match = short_if_re.match(line)
        if short_match and current_q:
            word = short_match.group(1).lower().replace(" ", "_")
            outcome = short_match.group(2).strip()
            if current_q not in result:
                result[current_q] = {}
            result[current_q][f"if_{word}"] = outcome
            continue

        if line.startswith("(") and current_q and current_q in result:
            result[current_q]["definition"] = line
            continue

        if line.startswith("NOTE"):
            result["note"] = line
            continue

    return result


def parse_gen3_entry(entry_id: str, topic: str, body: str) -> dict:
    user_query = normalize_whitespace(
        extract_between(body, "Query", ["Variations", "Part A — Intern Briefing", "Part A"]).replace('"', "")
    )
    variations_section = section_between(body, "Variations", "Part A — Intern Briefing")
    if not variations_section.strip():
        variations_section = section_between(body, "Variations", "Part A")
    variations = extract_gen3_variations(variations_section)
    # Do not use the short substring "Part B" as an end marker — prose may say e.g. "probes in Part B."
    part_a = normalize_whitespace(
        extract_between(
            body,
            "Part A — Intern Briefing",
            ["Part B — Branching Questions"],
        )
    )
    part_b = extract_between(
        body,
        "Part B — Branching Questions",
        ["Part C — Routing Recommendation"],
    )
    triage_questions = extract_gen3_triage_questions(part_b)
    branching_bullets = extract_gen3_branching_logic(part_b)
    routing_section = extract_between(
        body,
        "Part C — Routing Recommendation",
        ["Guardrails", "LPA Guardrail"],
    )
    routing = extract_gen3_routing(routing_section)
    guardrail = normalize_whitespace(extract_between(body, "Guardrails", ["\n\n", "\n\f", "\f"]))

    entry = {
        "id": entry_id,
        "category": "PBSG Hotline — General Enquiries (v3)",
        "topic": topic.strip(),
        "user_query": user_query,
        "variations": variations,
        "part_a_general_info": part_a,
        "triage_questions": triage_questions,
        "branching_logic": branching_bullets,
        "routing": routing,
        "guardrail": guardrail,
    }
    return normalize_gen3_t03_q4_not_sure(normalize_gen3_handoff_ids(replace_caller_with_applicant(entry)))


def docx_to_text(docx_path: Path) -> str:
    return subprocess.check_output(["textutil", "-convert", "txt", "-stdout", str(docx_path)], text=True).replace(
        "\r\n", "\n"
    )


def build_legacy_entries(text: str) -> list[dict]:
    matches = list(ENTRY_HEADER_RE.finditer(text))
    entries: list[dict] = []
    for i, match in enumerate(matches):
        entry_id = match.group(1).strip()
        topic = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else text.find("11. Implementation Notes for PwC", start)
        if end == -1:
            end = len(text)
        body = text[start:end].strip()
        entries.append(parse_entry(entry_id, topic, body))
    return entries


def build_gen3_entries(text: str) -> list[dict]:
    matches = gen3_header_matches(text)
    entries: list[dict] = []
    for i, match in enumerate(matches):
        entry_id = match.group(1).strip()
        topic = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        entries.append(parse_gen3_entry(entry_id, topic, body))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build data/pbsg_golden_set_by_id/<id>.json from a PBSG Golden Set .docx (macOS textutil)."
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help=f"Parse domain layout from {DEFAULT_LEGACY_DOCX.name} (XXX-NN | topic headers).",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to source .docx (defaults: General Enquiries v3, or Complete v2 with --legacy).",
    )
    args = parser.parse_args()

    if args.source is not None:
        source = args.source.resolve()
    elif args.legacy:
        source = DEFAULT_LEGACY_DOCX
    else:
        source = DEFAULT_GEN3_DOCX

    if not source.is_file():
        raise SystemExit(f"Source docx not found: {source}")

    text = docx_to_text(source)
    legacy = args.legacy or source.name == "PBSG_Golden_Set_Complete_v2.docx"
    entries = build_legacy_entries(text) if legacy else build_gen3_entries(text)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        eid = entry["id"]
        out_path = OUTPUT_DIR / f"{eid}.json"
        out_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} files from {source.name} to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
