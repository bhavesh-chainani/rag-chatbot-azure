import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DOCX = ROOT / "data" / "PBSG_Golden_Set_Complete_v2.docx"
OUTPUT_DIR = ROOT / "data" / "pbsg_golden_set_by_id"

ENTRY_HEADER_RE = re.compile(
    r"^([A-Z]{3}-\d{2})\s+\|\s+(.+?)$",
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


def main() -> None:
    text = subprocess.check_output(["textutil", "-convert", "txt", "-stdout", str(SOURCE_DOCX)], text=True)
    text = text.replace("\r\n", "\n")

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

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        eid = entry["id"]
        out_path = OUTPUT_DIR / f"{eid}.json"
        out_path.write_text(json.dumps(entry, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} files to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
