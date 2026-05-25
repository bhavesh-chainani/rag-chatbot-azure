import os
import re
from datetime import date, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessageParam


@dataclass
class PBSGTriageQuestionTarget:
    entry_id: str | None
    question_id: str


@dataclass
class PBSGTriageFact:
    fact_key: str
    value: str
    normalized_value: str
    source: str
    scope: str = "global"
    confidence: float = 1.0
    status: str = "active"
    provenance: str | None = None
    source_type: str = "assistant_visible_state"
    branch_value: str | None = None
    source_text: str | None = None
    source_turn_index: int | None = None
    workflow_scope: str = "global"


@dataclass
class PBSGInterruption:
    question: str
    parent_workflow: str | None
    parent_question_id: str | None
    status: str = "active"
    depth: int = 1


@dataclass
class PBSGTriageState:
    mode: str = "ORCHESTRATION"
    workflow_id: str | None = None
    workflow_locked: bool = False
    active_workflow: str | None = None
    queued_workflows: list[str] = field(default_factory=list)
    completed_workflows: list[str] = field(default_factory=list)
    concurrent_monitors: list[str] = field(default_factory=list)
    current_question_id: str | None = None
    pending_entry_id: str | None = None
    previous_states: list[str] = field(default_factory=list)
    answered_lines: list[str] = field(default_factory=list)
    allowed_branch_keys: list[str] = field(default_factory=list)
    allowed_transitions: list[str] = field(default_factory=list)
    latest_answer_classification: str | None = None
    contradiction_signals: list[str] = field(default_factory=list)
    active_monitors: dict[str, bool] = field(
        default_factory=lambda: {"urgent": False, "vulnerable": False, "representation": False}
    )
    repair_required: bool = False
    escalation_required: bool = False
    parent_workflow: str | None = None
    resume_question_id: str | None = None
    triggered_overlays: list[str] = field(default_factory=list)
    fact_ledger: list[PBSGTriageFact] = field(default_factory=list)
    unanswered_required_fields: list[str] = field(default_factory=list)
    suspended_workflows: list[str] = field(default_factory=list)
    active_side_enquiry: PBSGInterruption | None = None
    interruption_stack: list[PBSGInterruption] = field(default_factory=list)
    routing_completion_status: str = "not_started"


@dataclass
class PBSGTransition:
    entry_id: str
    question_id: str
    branch_key: str
    outcome: str
    transition_type: str
    target_entry_id: str | None = None
    target_question_id: str | None = None
    route_label: str | None = None
    nested_entry_id: str | None = None
    resume_entry_id: str | None = None
    resume_question_id: str | None = None
    clarification_text: str | None = None


@dataclass
class PBSGWorkflowEdge:
    entry_id: str
    question_id: str
    branch_key: str
    transition: PBSGTransition


@dataclass
class PBSGDeterministicResult:
    content: str
    state: PBSGTriageState
    transition: PBSGTransition
    entries: dict[str, dict[str, Any]]


@dataclass
class PBSGQueuedTopic:
    entry_id: str
    evidence: str
    confidence: float


@dataclass
class PBSGTopicCandidate:
    entry_id: str
    confidence: float
    evidence: str
    matched_facts: list[str] = field(default_factory=list)
    candidate_type: str = "primary_topic"


@dataclass
class PBSGTopicResolution:
    entry_id: str
    confidence: float
    reason: str
    overlays: list[str] = field(default_factory=list)
    queued_topics: list[PBSGQueuedTopic] = field(default_factory=list)
    candidates: list[PBSGTopicCandidate] = field(default_factory=list)


@dataclass
class PBSGTurnClassification:
    turn_type: str
    should_call_llm: bool
    reason: str | None = None
    pending_branch_key: str | None = None
    pending_answer_confidence: float | None = None
    new_topics: list[PBSGQueuedTopic] = field(default_factory=list)
    affects_prior_answer: bool = False


@dataclass
class StructuredRoute:
    route_label: str
    route_name: str
    script: str
    needs_to_know: list[str] = field(default_factory=list)
    access: list[str] = field(default_factory=list)
    prepare: list[str] = field(default_factory=list)
    intern_steps: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


QUESTION_FACT_MAP = {
    ("GEN3-T01", "Q1"): "applicant.representation_status",
    ("GEN3-T01", "Q2"): "applicant.caller_capacity",
    ("GEN3-T01", "Q3"): "matter.business_or_commercial",
    ("GEN3-T01", "Q4"): "applicant.prior_legal_advice",
    ("GEN3-T01", "Q5"): "matter.legal_topic",
    ("GEN3-T02", "Q1"): "matter.capital_offence",
    ("GEN3-T02", "Q2"): "matter.urgency_or_safety",
    ("GEN3-T02", "Q3"): "matter.charged_in_court",
    ("GEN3-T02", "Q4"): "applicant.residency_status",
    ("GEN3-T02", "Q5"): "applicant.pdo_application_status",
    ("GEN3-T02", "Q6"): "applicant.means_status",
    ("GEN3-T03", "Q1"): "matter.urgency_or_safety",
    ("GEN3-T03", "Q2"): "applicant.residency_status",
    ("GEN3-T03", "Q3"): "applicant.lab_application_status",
    ("GEN3-T03", "Q4"): "applicant.singaporean_child_under_21",
    ("GEN3-T03", "Q5"): "applicant.means_status",
    ("GEN3-T04", "Q1"): "applicant.residency_status",
    ("GEN3-T04", "Q2"): "applicant.help_type_requested",
    ("GEN3-T04", "Q3"): "applicant.lab_application_status",
    ("GEN3-T04", "Q4"): "applicant.means_status",
    ("GEN3-T06", "Q1"): "matter.immediate_safety",
    ("GEN3-T06", "Q2"): "matter.urgent_deprivation",
    ("GEN3-T06", "Q3"): "matter.deadline_within_14_days",
    ("GEN3-T13", "Q1"): "applicant.vulnerability_status",
}


ASK_MARKERS = [
    "Ask the applicant (read verbatim):",
    "Back to triage",
]

URGENT_PATTERN = re.compile(
    r"\b("
    r"court|deadline|within 14 days|tomorrow|next week|arrest|custody|detention|bail|deport|"
    r"homeless|evict|violence|safe|safety|self[- ]?harm|suicid|no food|no money"
    r")\b",
    flags=re.IGNORECASE,
)
VULNERABLE_PATTERN = re.compile(
    r"\b("
    r"elderly|minor|under 18|disabled|disability|abuse|domestic violence|language barrier|"
    r"interpreter|mental distress|social worker|fsc|financial hardship"
    r")\b",
    flags=re.IGNORECASE,
)
REPRESENTED_PATTERN = re.compile(
    r"\b(represented|lawyer|solicitor|counsel|legal advice|filed documents)\b",
    flags=re.IGNORECASE,
)
WORKFLOW_ID_PATTERN = re.compile(r"\bGEN3-[A-Z0-9-]+\b", flags=re.IGNORECASE)
ROUTE_PATTERN = re.compile(r"\bRoute\s+([A-Z])\b", flags=re.IGNORECASE)
GOLDEN_SET_RELATIVE_DIR = Path("data") / "pbsg_golden_set_by_id"
CAPITAL_OFFENCE_PATTERN = re.compile(
    r"\b(murder|capital offence|capital offense|death penalty|punishable with death)\b",
    flags=re.IGNORECASE,
)
ADDITIVE_PATTERN = re.compile(r"\b(also|and also|another issue|separate matter|by the way)\b", flags=re.IGNORECASE)
CORRECTION_PATTERN = re.compile(r"\b(actually|sorry|correction|i meant|not anymore)\b", flags=re.IGNORECASE)
CLARIFICATION_QUESTION_PATTERN = re.compile(
    r"\b(what is|what's|what does|can you explain|could you explain|meaning of)\b", flags=re.IGNORECASE
)
SAFETY_INTERRUPT_PATTERN = re.compile(
    r"\b(danger|violence|self[- ]?harm|homeless|court tomorrow|deadline|immediate threat)\b", flags=re.IGNORECASE
)
TOPIC_SIGNAL_RULES = [
    ("GEN3-T02", re.compile(r"\b(criminal|charged|charge|police|arrest|offence|offense)\b", flags=re.IGNORECASE)),
    ("GEN3-T03", re.compile(r"\b(divorce|custody|maintenance|matrimonial|family violence|ppo)\b", flags=re.IGNORECASE)),
    ("GEN3-T04", re.compile(r"\b(employment|landlord|tenant|contract|estate|probate|civil|debt)\b", flags=re.IGNORECASE)),
    ("GEN3-T06", re.compile(r"\b(urgent|danger|violence|homeless|deadline|court tomorrow)\b", flags=re.IGNORECASE)),
    ("GEN3-T13", re.compile(r"\b(vulnerable|elderly|minor|disabled|language barrier|social worker)\b", flags=re.IGNORECASE)),
]
MONTH_LOOKUP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
INITIAL_TOPIC_PATTERNS = {
    "GEN3-T02": re.compile(
        r"\b(criminal|charged|charge|police|arrest|offence|offense|murder|bail|remand|court date)\b",
        flags=re.IGNORECASE,
    ),
    "GEN3-T03": re.compile(
        r"\b(divorce|custody|maintenance|matrimonial|family violence|ppo|personal protection order|spouse|ex[- ]?(?:wife|husband|spouse)|children?)\b",
        flags=re.IGNORECASE,
    ),
    "GEN3-T04": re.compile(
        r"\b(employment|employer|salary|wages?|dismissal|fired|landlord|tenant|contract|estate|probate|civil|debt)\b",
        flags=re.IGNORECASE,
    ),
    "GEN3-T06": re.compile(
        r"\b(urgent|immediate|danger|homeless|no shelter|deadline|court tomorrow|court date|next week)\b",
        flags=re.IGNORECASE,
    ),
    "GEN3-T13": re.compile(
        r"\b(vulnerable|elderly|minor|under 18|disabled|disability|language barrier|interpreter|social worker|fsc|confused)\b",
        flags=re.IGNORECASE,
    ),
}
GEN3_T13_PRIMARY_PATTERN = re.compile(
    r"\b(vulnerable applicant|assess (?:this )?vulnerab|adapt my response|handle (?:a )?vulnerable|elderly and confused|speaks very little english|under 18|minor)\b",
    flags=re.IGNORECASE,
)
SUBSTANTIVE_FACT_PATTERN = re.compile(
    r"\b(applicant|charged|charge|court|divorce|custody|maintenance|employer|salary|landlord|tenant|debt|"
    r"estate|probate|criminal|police|arrest|urgent|deadline|vulnerable|elderly|minor|disabled|social worker)\b",
    flags=re.IGNORECASE,
)
BARE_START_PATTERN = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|start triage|i need help|need help|help|general enquiry|general inquiry)\s*[.!?]*\s*$",
    flags=re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s|,)\"“”]+")
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE)
PHONE_PATTERN = re.compile(r"\b(?:\d{4}\s?\d{4}|1800\s?\d{4}\s?\d{3}|65\d{6})\b")
ADDRESS_PATTERN = re.compile(
    r"\b(?:\d+\s+[A-Z][^.|]*?(?:Square|Road|Centre|Center|Courts|Singapore\s+\d{6})[^.|]*)",
    flags=re.IGNORECASE,
)
HOURS_PATTERN = re.compile(r"\b(?:Mon|Mondays?|Fri|Fridays?|weekends?|PH|am|pm|appointment)[^.]*(?:\.|$)", flags=re.IGNORECASE)


def candidate_golden_set_dirs() -> list[Path]:
    env_dir = os.getenv("PBSG_GOLDEN_SET_DIR")
    module_path = Path(__file__).resolve()
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    candidates.extend(base / GOLDEN_SET_RELATIVE_DIR for base in (Path.cwd(), module_path.parent, *module_path.parents))

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(candidate)
    return deduped


def resolve_default_golden_set_dir() -> Path:
    for candidate in candidate_golden_set_dirs():
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent / GOLDEN_SET_RELATIVE_DIR


DEFAULT_GOLDEN_SET_DIR = resolve_default_golden_set_dir()


def message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def load_golden_set_entries(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    import json

    entries: dict[str, dict[str, Any]] = {}
    golden_set_dir = directory or DEFAULT_GOLDEN_SET_DIR
    if not golden_set_dir.exists():
        return entries
    for path in sorted(golden_set_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entry_id = entry.get("id")
        if isinstance(entry_id, str) and isinstance(entry.get("branching_logic"), dict):
            entries[entry_id] = entry
    return entries


def latest_assistant_content(messages: list[ChatCompletionMessageParam]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "assistant":
            content = message_content_to_text(message.get("content"))
            if content:
                return content
    return None


def extract_selected_entry_id(content: str | None) -> str | None:
    if not content:
        return None
    matches = re.findall(r"\*\*Selected Entry:\*\*\s*([A-Z0-9-]+)", content, flags=re.IGNORECASE)
    return matches[-1] if matches else None


def question_region(content: str | None) -> str:
    if not content:
        return ""
    marker_positions = [content.rfind(marker) for marker in ASK_MARKERS]
    marker_position = max(marker_positions)
    return content[marker_position:] if marker_position >= 0 else ""


def extract_question_targets(content: str | None) -> list[PBSGTriageQuestionTarget]:
    region = question_region(content)
    if not region:
        return []

    matches = re.findall(r"(?:(GEN3-[A-Z0-9-]+)\s+)?(Q\d+[A-Z]?)\s*:", region, flags=re.IGNORECASE)
    if not matches:
        matches = re.findall(r"(?:(GEN3-[A-Z0-9-]+)\s+)?(Q\d+[A-Z]?)\b", region, flags=re.IGNORECASE)
    return [PBSGTriageQuestionTarget(entry_id or None, question_id.upper()) for entry_id, question_id in matches]


def extract_answered_lines(content: str | None) -> list[str]:
    if not content:
        return []
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r"^[-*]?\s*(?:GEN3-[A-Z0-9-]+\s+)?Q\d+[A-Z]?\b", stripped, flags=re.IGNORECASE):
            lines.append(stripped)
        elif stripped.startswith("- Last answered:") or stripped.startswith("- Carried over:"):
            lines.append(stripped)
    return lines[-8:]


def normalize_question_text_for_fact(question: str) -> str:
    normalized = re.sub(r"^\([^)]*\)\s*", "", question.lower()).strip()
    normalized = re.sub(r"[^a-z0-9\s/]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def canonical_fact_key_for_question(question: str | None) -> str | None:
    if not question:
        return None
    normalized = normalize_question_text_for_fact(question)
    if (
        "singapore citizen" in normalized
        or re.search(r"\bpr\b", normalized)
        or "permanent resident" in normalized
    ):
        return "applicant.residency_status"
    if "represented by a lawyer" in normalized or "currently represented" in normalized:
        return "applicant.representation_status"
    if "received legal advice" in normalized:
        return "applicant.prior_legal_advice"
    if "court date" in normalized or "deadline" in normalized or "family violence" in normalized:
        return "matter.urgency_or_safety"
    if "capital offence" in normalized or "punishable with death" in normalized:
        return "matter.capital_offence"
    if "legal aid bureau" in normalized or " lab" in f" {normalized}":
        return "applicant.lab_application_status"
    if "public defender" in normalized or " pdo" in f" {normalized}":
        return "applicant.pdo_application_status"
    if "per capita household income" in normalized or " pchi" in f" {normalized}":
        return "applicant.means_status"
    if "singaporean child" in normalized or "child under 21" in normalized:
        return "applicant.singaporean_child_under_21"
    if "representation" in normalized and "guidance" in normalized:
        return "applicant.help_type_requested"
    if "business commercial" in normalized or "company dispute" in normalized:
        return "matter.business_or_commercial"
    if "what type of matter" in normalized:
        return "matter.legal_topic"
    if "person who needs legal help" in normalized or "calling on behalf" in normalized:
        return "applicant.caller_capacity"
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:80]
    return f"workflow.question.{slug}" if slug else None


def canonical_fact_key_for_node(entry_id: str | None, question_id: str | None, question: str | None = None) -> str | None:
    if entry_id and question_id:
        mapped = QUESTION_FACT_MAP.get((entry_id, question_id))
        if mapped:
            return mapped
    return canonical_fact_key_for_question(question)


def question_text_from_entry(entries: dict[str, dict[str, Any]], entry_id: str | None, question_id: str | None) -> str | None:
    if not entry_id or not question_id:
        return None
    entry = entries.get(entry_id)
    branching_logic = entry.get("branching_logic") if entry else None
    node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
    question = node.get("question") if isinstance(node, dict) else None
    return question if isinstance(question, str) else None


def fact_from_answered_line(entries: dict[str, dict[str, Any]], default_entry_id: str | None, line: str) -> PBSGTriageFact | None:
    match = re.search(
        r"(?:(GEN3-[A-Z0-9-]+)\s+)?(Q\d+[A-Z]?)\b[^=→-]*(?:=|→|->)\s*([^;\[\n]+)",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    entry_id = (match.group(1) or default_entry_id or "").upper()
    question_id = match.group(2).upper()
    value = match.group(3).strip(" .")
    question = question_text_from_entry(entries, entry_id, question_id)
    fact_key = canonical_fact_key_for_node(entry_id, question_id, question)
    if not fact_key:
        return None
    normalized_value = normalize_branch_label(value)
    scope = "workflow" if fact_key.startswith("workflow.question.") else "global"
    return PBSGTriageFact(
        fact_key=fact_key,
        value=value,
        normalized_value=normalized_value,
        source=f"{entry_id}.{question_id}",
        scope=scope,
        provenance=line,
        source_type="assistant_visible_state",
        source_text=line,
    )


def extract_fact_ledger(
    entries: dict[str, dict[str, Any]],
    selected_entry_id: str | None,
    answered_lines: list[str],
) -> list[PBSGTriageFact]:
    facts: list[PBSGTriageFact] = []
    seen: set[tuple[str, str]] = set()
    for line in answered_lines:
        fact = fact_from_answered_line(entries, selected_entry_id, line)
        if not fact:
            continue
        dedupe_key = (fact.fact_key, fact.source)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        facts.append(fact)
    return facts


def make_user_fact(
    fact_key: str,
    value: str,
    normalized_value: str,
    source: str,
    source_text: str,
    source_turn_index: int | None,
    branch_value: str | None,
    confidence: float = 0.95,
    workflow_scope: str = "global",
) -> PBSGTriageFact:
    return PBSGTriageFact(
        fact_key=fact_key,
        value=value,
        normalized_value=normalized_value,
        source=source,
        scope=workflow_scope,
        confidence=confidence,
        provenance=source_text,
        source_type="user_message",
        branch_value=branch_value,
        source_text=source_text,
        source_turn_index=source_turn_index,
        workflow_scope=workflow_scope,
    )


def current_local_date() -> date:
    return date.today()


def normalize_explicit_year(year_text: str | None) -> int | None:
    if not year_text:
        return None
    year = int(year_text)
    return 2000 + year if year < 100 else year


def candidate_date_for_day_month(day: int, month: int, year: int | None, today: date) -> date | None:
    resolved_year = year or today.year
    try:
        candidate = date(resolved_year, month, day)
    except ValueError:
        return None
    if year is None and candidate < today:
        return None
    return candidate


def branch_for_deadline_date(candidate: date | None, today: date) -> str | None:
    if not candidate:
        return "if_not_sure"
    days_until = (candidate - today).days
    if days_until < 0:
        return "if_not_sure"
    return "if_yes" if days_until <= 14 else "if_no"


def explicit_deadline_date(text: str, today: date) -> date | None:
    iso_match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
        return candidate_date_for_day_month(day, month, year, today)

    numeric_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        year = normalize_explicit_year(numeric_match.group(3))
        return candidate_date_for_day_month(day, month, year, today)

    month_pattern = "|".join(sorted(MONTH_LOOKUP, key=len, reverse=True))
    day_month_match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})(?:\s+(\d{{2,4}}))?\b", text)
    if day_month_match:
        day = int(day_month_match.group(1))
        month = MONTH_LOOKUP[day_month_match.group(2)]
        year = normalize_explicit_year(day_month_match.group(3))
        return candidate_date_for_day_month(day, month, year, today)

    month_day_match = re.search(rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+(\d{{2,4}}))?\b", text)
    if month_day_match:
        month = MONTH_LOOKUP[month_day_match.group(1)]
        day = int(month_day_match.group(2))
        year = normalize_explicit_year(month_day_match.group(3))
        return candidate_date_for_day_month(day, month, year, today)

    return None


def deadline_branch_key_from_text(text: str, today: date | None = None) -> str | None:
    today = today or current_local_date()
    normalized = normalize_branch_label(text)
    if re.search(r"\b(no deadline|no court date|not urgent|no urgency)\b", normalized):
        return "if_no"
    if re.search(r"\b(today|tonight|tomorrow|next week)\b", normalized):
        return "if_yes"
    relative_match = re.search(r"\b(?:in|within)?\s*(\d{1,3})\s+days?\b", normalized)
    if relative_match:
        return "if_yes" if int(relative_match.group(1)) <= 14 else "if_no"
    explicit_date = explicit_deadline_date(text.lower(), today) or explicit_deadline_date(normalized, today)
    if explicit_date:
        return branch_for_deadline_date(explicit_date, today)
    if re.search(r"\b(court date|deadline)\b", normalized):
        return "if_not_sure"
    return None


def is_deadline_question(question_node: dict[str, Any]) -> bool:
    question = question_node.get("question")
    if not isinstance(question, str):
        return False
    normalized = normalize_question_text_for_fact(question)
    return "deadline" in normalized or "court date" in normalized


def deterministic_facts_from_user_text(text: str, source_turn_index: int | None = None) -> list[PBSGTriageFact]:
    normalized = normalize_branch_label(text)
    facts: list[PBSGTriageFact] = []

    def add_fact(
        fact_key: str,
        value: str,
        normalized_value: str,
        branch_value: str | None,
        confidence: float = 0.95,
    ) -> None:
        facts.append(
            make_user_fact(
                fact_key=fact_key,
                value=value,
                normalized_value=normalized_value,
                source=f"user_turn_{source_turn_index}" if source_turn_index is not None else "latest_user_turn",
                source_text=text,
                source_turn_index=source_turn_index,
                branch_value=branch_value,
                confidence=confidence,
            )
        )

    if re.search(r"\bsingaporean\b(?!\s+child)|\b(singapore citizen|sg citizen|sgc|permanent resident|\bpr\b)\b", normalized):
        add_fact("applicant.residency_status", "Singapore Citizen or PR", "sgc_pr", "if_yes")
    elif re.search(r"\b(foreigner|work permit|employment pass|s pass|dependent pass|not singapore citizen|not a citizen|not pr|not a pr)\b", normalized):
        add_fact("applicant.residency_status", "foreigner", "foreigner", "if_no_foreigner")

    if re.search(
        r"\b(no lawyer|no legal representation|not represented|unrepresented|does not have a lawyer|don t have a lawyer|do not have a lawyer)\b",
        normalized,
    ):
        add_fact("applicant.representation_status", "No lawyer", "no", "if_no")
    elif re.search(r"\b(has a lawyer|have a lawyer|represented|lawyer filed|solicitor|counsel)\b", normalized):
        add_fact("applicant.representation_status", "Has lawyer", "yes", "if_yes")

    if re.search(r"\b(no urgency|not urgent|no deadline|no court date|no safety issue|no family violence)\b", normalized):
        add_fact("matter.urgency_or_safety", "No urgency or safety issue", "no", "if_no", confidence=0.9)
    elif deadline_branch_key_from_text(text) == "if_yes" or re.search(r"\b(urgent|family violence|violence|unsafe|danger)\b", normalized):
        add_fact("matter.urgency_or_safety", "Urgency or safety issue", "yes", "if_yes", confidence=0.9)
    deadline_branch_key = deadline_branch_key_from_text(text)
    if deadline_branch_key in {"if_yes", "if_no", "if_not_sure"}:
        deadline_value = {
            "if_yes": "Deadline within 14 days",
            "if_no": "Deadline outside 14 days",
            "if_not_sure": "Deadline unclear",
        }[deadline_branch_key]
        add_fact(
            "matter.deadline_within_14_days",
            deadline_value,
            normalize_branch_label(deadline_value),
            deadline_branch_key,
            confidence=0.95,
        )

    if re.search(r"\b(capital offence|capital offense|death penalty|punishable with death|murder)\b", normalized):
        add_fact("matter.capital_offence", "Capital offence", "yes", "if_yes")
    elif re.search(r"\b(assault|voluntarily causing hurt|vch|theft|shoplifting|molest|outrage of modesty|rioting)\b", normalized):
        add_fact("matter.capital_offence", "Non-capital offence", "no", "if_no", confidence=0.9)

    if re.search(r"\b(charged in court|been charged|has been charged|charged with|charge sheet|court charge)\b", normalized):
        add_fact("matter.charged_in_court", "Charged in court", "yes", "if_yes")

    if re.search(r"\b(no lab|not applied to lab|has not applied to lab|haven t applied to lab|never applied to lab)\b", normalized):
        add_fact("applicant.lab_application_status", "No LAB application", "no", "if_no")
    elif re.search(r"\b(lab.*failed|failed.*lab|lab rejection|rejected by lab)\b", normalized):
        add_fact("applicant.lab_application_status", "LAB failed means test", "lab_failed_means_test", "if_yes_failed_means_test")
    elif re.search(r"\b(lab.*processing|processing.*lab|lab.*pending|applied to lab)\b", normalized):
        add_fact("applicant.lab_application_status", "LAB applied or processing", "passed_or_processing", "if_yes_passed_or_processing")

    has_no_income = bool(re.search(r"\b(no income|housewife|unemployed|not working|zero income)\b", normalized))
    has_private_housing = bool(re.search(r"\b(condo|condominium|private property|private housing)\b", normalized))
    has_non_private_housing = bool(
        re.search(r"\b(hdb|[12345]\s*rm|[12345]\s*room|shelter|remand|prison|rental flat)\b", normalized)
    )
    age_matches = [
        int(match)
        for match in re.findall(r"\b(\d{1,2})\s*(?:yo|y/o|years?\s*old|yrs?\s*old)\b", normalized)
        if int(match) >= 18
    ]
    if age_matches:
        age = max(age_matches)
        add_fact("applicant.age", f"Age {age}", str(age), None)
    child_count_match = re.search(r"\b(\d+)\s*(?:children|kids|dependants?|dependents?)\b", normalized)
    if child_count_match:
        dependant_count = int(child_count_match.group(1))
        add_fact("applicant.dependant_count", f"{dependant_count} dependant(s)", str(dependant_count), None)
        add_fact("applicant.household_size", f"Household size {dependant_count + 1}", str(dependant_count + 1), None)
    elif re.search(r"\b(daughter|son|child|kid|dependant|dependent)\b", normalized):
        add_fact("applicant.dependant_count", "1 dependant", "1", None)
        add_fact("applicant.household_size", "Household size 2", "2", None)
    household_match = re.search(r"\b(?:household of|household size|family of)\s*(\d+)\b", normalized)
    if household_match:
        add_fact("applicant.household_size", f"Household size {household_match.group(1)}", household_match.group(1), None)
    income_match = re.search(
        r"(?:income|earning|earn|salary|monthly pay|paid)\D{0,20}(?:s\$|\$)?\s*(\d+(?:\.\d+)?)\s*(k|000)?\b|"
        r"(?:s\$|\$)\s*(\d+(?:\.\d+)?)\s*(k|000)?\b",
        normalized,
    )
    if income_match:
        amount_text = income_match.group(1) or income_match.group(3)
        suffix = income_match.group(2) or income_match.group(4)
        amount = float(amount_text)
        if suffix in {"k", "000"}:
            amount *= 1000
        add_fact("applicant.monthly_income", f"Monthly income S${amount:,.0f}", str(int(amount)), None)
    savings_match = re.search(
        r"\b(?:savings?|cash|bank balance)\D{0,20}(?:s\$|\$)?\s*(\d+(?:\.\d+)?)\s*(k|000)?\b|"
        r"(?:s\$|\$)\s*(\d+(?:\.\d+)?)\s*(k|000)?\s*(?:savings?|cash|bank balance)\b",
        normalized,
    )
    if re.search(r"\b(no savings|zero savings|no cash)\b", normalized):
        add_fact("applicant.savings", "Savings S$0", "0", None)
    elif savings_match:
        savings_text = savings_match.group(1) or savings_match.group(3)
        suffix = savings_match.group(2) or savings_match.group(4)
        savings = float(savings_text)
        if suffix in {"k", "000"}:
            savings *= 1000
        add_fact("applicant.savings", f"Savings S${savings:,.0f}", str(int(savings)), None)
    if has_no_income:
        add_fact("applicant.monthly_income", "Monthly income S$0", "0", None)
        add_fact("applicant.income_status", "No income", "no_income", None)
        add_fact("applicant.financial_hardship", "Financial hardship", "true", None)
        add_fact("applicant.exception_cue", "Financial hardship", "true", None)
    elif re.search(
        r"\b(no money to hire (?:a )?lawyer|cannot afford (?:a )?lawyer|financial hardship|hardship|medical condition|medical bills?|liabilities|debt|supporting dependants?|supporting dependents?)\b",
        normalized,
    ):
        add_fact("applicant.exception_cue", "Exceptional circumstances", "true", None)
    if has_private_housing:
        add_fact("applicant.housing_type", "Private housing", "private_housing", None)
    elif has_non_private_housing:
        add_fact("applicant.housing_type", "Non-private housing", "non_private_housing", None)
    if has_no_income:
        add_fact(
            "applicant.means_status",
            "No income / hardship",
            "marginal_or_exceptional",
            "if_no_marginal_or_exceptional",
            confidence=0.9,
        )
    elif has_private_housing:
        add_fact(
            "applicant.means_status",
            "Private housing only",
            "private_housing_only",
            None,
            confidence=0.6,
        )

    if re.search(r"\b(singaporean child|singapore citizen child|sg child)\b", normalized):
        add_fact("applicant.singaporean_child_under_21", "Has Singaporean child", "yes", "if_yes")
    elif re.search(r"\b(no children|no singaporean child|child is not singaporean)\b", normalized):
        add_fact("applicant.singaporean_child_under_21", "No Singaporean child", "no", "if_no")

    if re.search(r"\b(divorce|matrimonial|custody|maintenance|family)\b", normalized):
        add_fact("matter.legal_topic", "Matrimonial", "matrimonial", "if_matrimonial", confidence=0.85)
    elif re.search(r"\b(criminal|charged|charge|arrest|police)\b", normalized):
        add_fact("matter.legal_topic", "Criminal", "criminal", "if_criminal", confidence=0.85)
    elif re.search(r"\b(employment|salary|landlord|tenant|contract|civil|debt)\b", normalized):
        add_fact("matter.legal_topic", "Civil or others", "civil_or_others", "if_civil_or_others", confidence=0.85)

    return facts


def branch_value_for_fact(question_node: dict[str, Any], fact: PBSGTriageFact) -> str | None:
    branch_keys = [key for key in question_node if key.startswith("if_")]
    if fact.branch_value in branch_keys:
        return fact.branch_value
    if fact.fact_key == "applicant.lab_application_status" and fact.normalized_value == "lab_failed_means_test":
        if "if_yes_lab_unable_to_assist" in branch_keys:
            return "if_yes_lab_unable_to_assist"
        if "if_yes_failed_means_test" in branch_keys:
            return "if_yes_failed_means_test"
    if fact.fact_key == "applicant.means_status":
        if fact.normalized_value == "fjss_pro_bono_qualifying" and "if_yes" in branch_keys:
            return "if_yes"
        if fact.normalized_value == "fjss_modest_means_qualifying" and "if_no_marginal" in branch_keys:
            return "if_no_marginal"
        if fact.normalized_value == "marginal_or_exceptional":
            if "if_no_marginal_or_exceptional" in branch_keys:
                return "if_no_marginal_or_exceptional"
            if "if_no_well_over" in branch_keys:
                return "if_no_well_over"
            if "if_not_sure" in branch_keys:
                return "if_not_sure"
        if fact.normalized_value == "private_housing_only":
            return None
    if fact.normalized_value in {"sgc_pr", "yes", "criminal", "matrimonial", "civil_or_others"}:
        preferred = {
            "sgc_pr": "if_yes",
            "yes": "if_yes",
            "criminal": "if_criminal",
            "matrimonial": "if_matrimonial",
            "civil_or_others": "if_civil_or_others",
        }.get(fact.normalized_value)
        if preferred in branch_keys:
            return preferred
    if fact.normalized_value in {"foreigner", "no"}:
        if "if_no_foreigner" in branch_keys and fact.fact_key == "applicant.residency_status":
            return "if_no_foreigner"
        if "if_no" in branch_keys:
            return "if_no"
    return branch_key_for_answer(question_node, fact.value)


def user_fact_for_question(
    entries: dict[str, dict[str, Any]],
    facts: list[PBSGTriageFact],
    entry_id: str | None,
    question_id: str | None,
) -> PBSGTriageFact | None:
    question = question_text_from_entry(entries, entry_id, question_id)
    fact_key = canonical_fact_key_for_node(entry_id, question_id, question)
    if not fact_key:
        return None
    for fact in reversed(facts):
        if fact.fact_key != fact_key or fact.status != "active":
            continue
        if fact.source_type not in {"user_message", "deterministic_transition", "structured_extraction", "routing_answer"}:
            continue
        return fact
    return None


def routing_answer_fact_from_user_turn(
    entries: dict[str, dict[str, Any]],
    assistant_content: str | None,
    user_text: str,
    source_turn_index: int | None,
) -> PBSGTriageFact | None:
    selected_entry_id = extract_selected_entry_id(assistant_content)
    targets = extract_question_targets(assistant_content)
    pending_target = targets[-1] if targets else None
    pending_entry_id = pending_target.entry_id if pending_target and pending_target.entry_id else selected_entry_id
    question_id = pending_target.question_id if pending_target else None
    if not pending_entry_id or not question_id:
        return None
    entry = entries.get(pending_entry_id)
    branching_logic = entry.get("branching_logic") if entry else None
    question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
    if not isinstance(question_node, dict):
        return None
    branch_key = branch_key_for_answer(question_node, user_text)
    if not branch_key:
        return None
    question = question_node.get("question")
    fact_key = canonical_fact_key_for_node(pending_entry_id, question_id, question if isinstance(question, str) else None)
    if not fact_key:
        return None
    value = label_from_branch_key(branch_key)
    normalized_value = normalize_branch_label(value)
    if fact_key == "applicant.lab_application_status" and branch_key == "if_yes_failed_means_test":
        value = "LAB failed means test"
        normalized_value = "lab_failed_means_test"
    return PBSGTriageFact(
        fact_key=fact_key,
        value=value,
        normalized_value=normalized_value,
        source=f"{pending_entry_id}.{question_id}",
        scope="global" if not fact_key.startswith("workflow.question.") else "workflow",
        confidence=1.0,
        provenance=user_text,
        source_type="routing_answer",
        branch_value=branch_key,
        source_text=user_text,
        source_turn_index=source_turn_index,
        workflow_scope="global" if not fact_key.startswith("workflow.question.") else pending_entry_id,
    )


def merge_fact_ledger(facts: list[PBSGTriageFact]) -> list[PBSGTriageFact]:
    merged: list[PBSGTriageFact] = []
    latest_by_key: dict[str, PBSGTriageFact] = {}
    for fact in facts:
        existing = latest_by_key.get(fact.fact_key)
        if existing and fact.fact_key in {
            "applicant.residency_status",
            "applicant.representation_status",
            "applicant.prior_legal_advice",
            "matter.urgency_or_safety",
            "applicant.lab_application_status",
            "applicant.age",
            "applicant.dependant_count",
            "applicant.household_size",
            "applicant.income_status",
            "applicant.monthly_income",
            "applicant.savings",
            "applicant.housing_type",
            "applicant.financial_hardship",
            "applicant.exception_cue",
            "applicant.means_status",
            "applicant.singaporean_child_under_21",
            "matter.legal_topic",
        }:
            existing.status = "superseded"
        latest_by_key[fact.fact_key] = fact
        merged.append(fact)
    return merged


def active_fact_by_key(facts: list[PBSGTriageFact]) -> dict[str, PBSGTriageFact]:
    return {fact.fact_key: fact for fact in facts if fact.status == "active"}


def numeric_fact_value(active_by_key: dict[str, PBSGTriageFact], fact_key: str) -> float | None:
    fact = active_by_key.get(fact_key)
    if not fact:
        return None
    try:
        return float(fact.normalized_value)
    except ValueError:
        return None


def bool_fact_value(active_by_key: dict[str, PBSGTriageFact], fact_key: str) -> bool | None:
    fact = active_by_key.get(fact_key)
    if not fact:
        return None
    if fact.normalized_value in {"true", "yes"}:
        return True
    if fact.normalized_value in {"false", "no"}:
        return False
    return None


def household_size_for_means(active_by_key: dict[str, PBSGTriageFact]) -> float | None:
    household_size = numeric_fact_value(active_by_key, "applicant.household_size")
    if household_size and household_size > 0:
        return household_size
    dependant_count = numeric_fact_value(active_by_key, "applicant.dependant_count")
    if dependant_count is not None:
        return max(1.0, dependant_count + 1)
    child_fact = active_by_key.get("applicant.singaporean_child_under_21")
    if child_fact and child_fact.normalized_value in {"yes", "has_singaporean_child"}:
        return 2
    income = numeric_fact_value(active_by_key, "applicant.monthly_income")
    if income == 0:
        return 1
    return None


def structured_means_values(active_by_key: dict[str, PBSGTriageFact]) -> dict[str, Any]:
    income = numeric_fact_value(active_by_key, "applicant.monthly_income")
    household_size = household_size_for_means(active_by_key)
    values: dict[str, Any] = {
        "applicant.monthly_income": income,
        "applicant.household_size": household_size,
        "applicant.savings": numeric_fact_value(active_by_key, "applicant.savings"),
        "applicant.age": numeric_fact_value(active_by_key, "applicant.age"),
        "applicant.housing_type": active_by_key.get("applicant.housing_type").normalized_value
        if active_by_key.get("applicant.housing_type")
        else None,
        "applicant.income_status": active_by_key.get("applicant.income_status").normalized_value
        if active_by_key.get("applicant.income_status")
        else None,
        "applicant.financial_hardship": bool_fact_value(active_by_key, "applicant.financial_hardship"),
        "applicant.exception_cue": bool_fact_value(active_by_key, "applicant.exception_cue") or False,
    }
    if income is not None and household_size:
        values["applicant.pchi"] = income / household_size
    return values


def condition_matches(condition: dict[str, Any], values: dict[str, Any]) -> bool | None:
    if condition.get("missing_required_facts"):
        return values.get("missing_required_facts")
    if condition.get("contradictory_financial_facts"):
        return False
    fact_key = condition.get("fact")
    if not isinstance(fact_key, str):
        return None
    value = values.get(fact_key)
    if value is None:
        return True if condition.get("missing") == "defer_to_application" else None
    if "equals" in condition:
        return value == condition["equals"]
    if "not_equals" in condition:
        return value != condition["not_equals"]
    if "lte" in condition and isinstance(value, (int, float)):
        return value <= float(condition["lte"])
    if "gt" in condition and isinstance(value, (int, float)):
        return value > float(condition["gt"])
    if "lte_by_age" in condition and isinstance(value, (int, float)):
        age_thresholds = condition["lte_by_age"]
        if not isinstance(age_thresholds, dict):
            return None
        age = values.get("applicant.age")
        threshold = age_thresholds.get("60_or_over") if isinstance(age, (int, float)) and age >= 60 else age_thresholds.get("under_60")
        return value <= float(threshold) if threshold is not None else None
    return None


def branch_matches(branch_rule: dict[str, Any], values: dict[str, Any]) -> bool:
    conditions_all = branch_rule.get("conditions_all")
    if isinstance(conditions_all, list):
        return all(condition_matches(condition, values) is True for condition in conditions_all if isinstance(condition, dict))
    conditions_any = branch_rule.get("conditions_any")
    if isinstance(conditions_any, list):
        return any(condition_matches(condition, values) is True for condition in conditions_any if isinstance(condition, dict))
    return False


def means_status_value_for_branch(branch_key: str, branch_rule: dict[str, Any]) -> tuple[str, str]:
    label = branch_rule.get("label") if isinstance(branch_rule.get("label"), str) else label_from_branch_key(branch_key)
    normalized_label = normalize_branch_label(label)
    if "fjss pro bono" in normalized_label:
        return label, "fjss_pro_bono_qualifying"
    if "fjss modest means" in normalized_label:
        return label, "fjss_modest_means_qualifying"
    if "clas" in normalized_label:
        return label, "clas_qualifying"
    if "legal clinic" in normalized_label:
        return label, "legal_clinic_qualifying"
    normalized_by_branch = {
        "if_yes": "standard_eligible",
        "if_no_marginal": "marginal_eligible",
        "if_no_marginal_or_exceptional": "marginal_or_exceptional",
        "if_no_well_over": "well_over",
        "if_no_well_over_no_exceptions": "well_over_no_exceptions",
        "if_no": "not_eligible",
        "if_not_sure": "not_sure",
    }
    return label, normalized_by_branch.get(branch_key, normalize_branch_label(label))


def evaluate_means_test_structured(
    question_node: dict[str, Any],
    facts: list[PBSGTriageFact],
) -> PBSGTriageFact | None:
    means_test_structured = question_node.get("means_test_structured")
    if not isinstance(means_test_structured, dict):
        return None
    active_by_key = active_fact_by_key(facts)
    existing_means = active_by_key.get("applicant.means_status")
    if existing_means and existing_means.branch_value in question_node:
        return existing_means
    values = structured_means_values(active_by_key)
    has_any_means_fact = any(
        active_by_key.get(fact_key)
        for fact_key in (
            "applicant.monthly_income",
            "applicant.household_size",
            "applicant.dependant_count",
            "applicant.savings",
            "applicant.housing_type",
            "applicant.financial_hardship",
            "applicant.exception_cue",
            "applicant.income_status",
        )
    )
    values["missing_required_facts"] = bool(
        has_any_means_fact and (values.get("applicant.monthly_income") is None or values.get("applicant.housing_type") is None)
    )
    branches = means_test_structured.get("branches")
    if not isinstance(branches, dict):
        return None
    for branch_key, branch_rule in branches.items():
        if not isinstance(branch_key, str) or branch_key not in question_node or not isinstance(branch_rule, dict):
            continue
        if branch_key == "if_not_sure":
            continue
        if not branch_matches(branch_rule, values):
            continue
        value, normalized_value = means_status_value_for_branch(branch_key, branch_rule)
        source_fact = next((fact for fact in reversed(facts) if fact.status == "active" and fact.fact_key in active_by_key), None)
        return PBSGTriageFact(
            fact_key="applicant.means_status",
            value=value,
            normalized_value=normalized_value,
            source=f"derived_from_{source_fact.source if source_fact else 'means_test_structured'}",
            scope="global",
            confidence=0.9,
            provenance=source_fact.source_text if source_fact else None,
            source_type="structured_extraction",
            branch_value=branch_key,
            source_text=source_fact.source_text if source_fact else None,
            source_turn_index=source_fact.source_turn_index if source_fact else None,
            workflow_scope="global",
        )
    return None


def synthesize_means_status_facts(entries: dict[str, dict[str, Any]], facts: list[PBSGTriageFact]) -> list[PBSGTriageFact]:
    active_by_key = active_fact_by_key(facts)
    existing_means = active_by_key.get("applicant.means_status")
    if existing_means and existing_means.normalized_value in {"marginal_or_exceptional", "fjss_pro_bono_qualifying"}:
        return facts
    for entry_id, question_id in (("GEN3-T03", "Q5"), ("GEN3-T04", "Q4"), ("GEN3-T02", "Q6")):
        entry = entries.get(entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict):
            continue
        means_fact = evaluate_means_test_structured(question_node, facts)
        if means_fact:
            if means_fact.branch_value == "if_not_sure":
                continue
            if existing_means:
                existing_means.status = "superseded"
            facts.append(means_fact)
            break
    return facts


def extract_user_fact_ledger(
    entries: dict[str, dict[str, Any]],
    messages: list[ChatCompletionMessageParam],
    latest_user_query: str,
) -> list[PBSGTriageFact]:
    facts: list[PBSGTriageFact] = []
    previous_assistant: str | None = None
    for index, message in enumerate(messages):
        role = message.get("role")
        content = message_content_to_text(message.get("content"))
        if role == "assistant":
            previous_assistant = content
            continue
        if role != "user" or not content:
            continue
        facts.extend(deterministic_facts_from_user_text(content, index))
        routing_fact = routing_answer_fact_from_user_turn(entries, previous_assistant, content, index)
        if routing_fact:
            facts.append(routing_fact)
    if latest_user_query:
        facts.extend(deterministic_facts_from_user_text(latest_user_query, len(messages)))
        routing_fact = routing_answer_fact_from_user_turn(entries, latest_assistant_content(messages), latest_user_query, len(messages))
        if routing_fact:
            facts.append(routing_fact)
    return synthesize_means_status_facts(entries, merge_fact_ledger(facts))


def active_fact_for_question(
    facts: list[PBSGTriageFact],
    question: str | None,
    excluded_source_prefixes: list[str] | None = None,
) -> PBSGTriageFact | None:
    fact_key = canonical_fact_key_for_question(question)
    if not fact_key:
        return None
    excluded_source_prefixes = excluded_source_prefixes or []
    for fact in reversed(facts):
        if any(fact.source.startswith(prefix) for prefix in excluded_source_prefixes):
            continue
        if fact.fact_key == fact_key and fact.status == "active":
            return fact
    return None


def unanswered_required_fields_for_state(
    entries: dict[str, dict[str, Any]],
    pending_entry_id: str | None,
    current_question_id: str | None,
    facts: list[PBSGTriageFact],
) -> list[str]:
    question = question_text_from_entry(entries, pending_entry_id, current_question_id)
    fact_key = canonical_fact_key_for_node(pending_entry_id, current_question_id, question)
    if not fact_key or user_fact_for_question(entries, facts, pending_entry_id, current_question_id):
        return []
    return [fact_key]


def extract_topic_workflows(content: str | None) -> list[str]:
    if not content or "Topics identified:" not in content:
        return []
    topics_region = content.split("Topics identified:", 1)[1]
    topics_region = re.split(r"\n\s*(?:Then immediately|What I gathered|Triage progress|\*\*Ask)", topics_region, 1)[0]
    workflows: list[str] = []
    for match in WORKFLOW_ID_PATTERN.findall(topics_region):
        workflow_id = match.upper()
        if workflow_id not in workflows:
            workflows.append(workflow_id)
    return workflows


def extract_topic_workflows_from_messages(messages: list[ChatCompletionMessageParam]) -> list[str]:
    workflows: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for workflow_id in extract_topic_workflows(message_content_to_text(message.get("content"))):
            if workflow_id not in workflows:
                workflows.append(workflow_id)
    return workflows


def extract_completed_workflows(content: str | None) -> list[str]:
    if not content:
        return []
    completed: list[str] = []
    for match in re.findall(r"\*\*✓\s*(GEN3-[A-Z0-9-]+)\s+routed\.\*\*", content, flags=re.IGNORECASE):
        workflow_id = match.upper()
        if workflow_id not in completed:
            completed.append(workflow_id)
    selected_entry_id = extract_selected_entry_id(content)
    if selected_entry_id and "**Routing Recommendation:**" in content and selected_entry_id not in completed:
        completed.append(selected_entry_id)
    return completed


def extract_completed_workflows_from_messages(messages: list[ChatCompletionMessageParam]) -> list[str]:
    completed: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for workflow_id in extract_completed_workflows(message_content_to_text(message.get("content"))):
            if workflow_id not in completed:
                completed.append(workflow_id)
    return completed


def allowed_branch_keys(entries: dict[str, dict[str, Any]], entry_id: str | None, question_id: str | None) -> list[str]:
    if not entry_id or not question_id:
        return []
    entry = entries.get(entry_id)
    branching_logic = entry.get("branching_logic") if entry else None
    question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
    if not isinstance(question_node, dict):
        return []
    return [key for key in question_node if key.startswith("if_")]


def normalize_simple_answer(text: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9\s']", " ", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return None

    yes_phrases = {
        "yes",
        "yeah",
        "yep",
        "yup",
        "correct",
        "that's right",
        "that is right",
        "i do",
        "i have one",
        "i am",
        "he does",
        "she does",
        "they do",
    }
    no_phrases = {
        "no",
        "nope",
        "nah",
        "not really",
        "don't have",
        "do not have",
        "never",
        "i don't",
        "i do not",
        "none",
    }
    unclear_phrases = {
        "not sure",
        "unsure",
        "don't know",
        "do not know",
        "i don't know",
        "i do not know",
        "unknown",
        "maybe",
    }

    if normalized in yes_phrases:
        return "YES"
    if normalized in no_phrases:
        return "NO"
    if normalized in unclear_phrases:
        return "UNCLEAR"
    if any(phrase in normalized for phrase in unclear_phrases):
        return "UNCLEAR"
    return None


def normalize_branch_label(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def label_from_branch_key(branch_key: str) -> str:
    labels = {
        "yes": "Yes",
        "no": "No",
        "not_sure": "Not sure",
        "not_sure_or_both": "Not sure or both",
        "no_foreigner": "No, foreigner",
        "criminal": "Criminal",
        "matrimonial": "Matrimonial",
        "civil_or_others": "Civil or others",
        "guidance": "Guidance",
        "representation": "Representation",
        "yes_and_nonprofit": "Yes, nonprofit",
        "yes_and_for_profit": "Yes, for-profit business",
        "calling_on_behalf_and_able_to_self_help": "Calling on behalf, can self-help",
        "self_or_calling_on_behalf_and_unable_to_self_help": "Self, or cannot self-help",
        "yes_passed_or_processing": "Yes, passed or processing",
        "yes_failed_means_test": "Yes, failed means test",
        "yes_pdo_unable_to_assist": "Yes, PDO unable to assist",
        "no_or_has_not_applied": "No, or has not applied",
        "no_marginal": "No, marginal",
        "no_well_over": "No, well over",
        "yes_lab_unable_to_assist": "Yes, LAB unable to assist",
        "yes_lab_able_or_not_sure": "Yes, LAB able or not sure",
        "no_marginal_or_exceptional": "No, marginal or exceptional",
        "no_well_over_no_exceptions": "No, well over, no exceptions",
        "helper_present": "Helper present",
        "no_or_unclear": "No or unclear",
        "under_18": "Under 18",
    }
    suffix = branch_key.removeprefix("if_")
    if suffix in labels:
        return labels[suffix]
    return suffix.replace("_", " ").capitalize()


def convert_question_to_second_person(question: str) -> str:
    question = re.sub(r"^\([^)]*\)\s*", "", question).strip()
    replacements = [
        (r"\bIs the applicant's\b", "Is your"),
        (r"\bDoes the applicant's\b", "Does your"),
        (r"\bHas the applicant's\b", "Has your"),
        (r"\bIs the applicant\b", "Are you"),
        (r"\bHas the applicant\b", "Have you"),
        (r"\bDoes the applicant\b", "Do you"),
        (r"\bthe applicant's\b", "your"),
        (r"\bthe applicant\b", "you"),
        (r"\bapplicant's\b", "your"),
        (r"\bapplicant\b", "you"),
    ]
    for pattern, replacement in replacements:
        question = re.sub(pattern, replacement, question, flags=re.IGNORECASE)
    return question


def first_quoted_text(text: str) -> str | None:
    match = re.search(r'"([^"]+)"|“([^”]+)”', text)
    if not match:
        return None
    return match.group(1) or match.group(2)


def route_sort_key(route_label: str) -> str:
    return route_label.lower()


def choose_single_branch(candidates: list[str]) -> str | None:
    return candidates[0] if len(candidates) == 1 else None


def branch_key_for_answer(question_node: dict[str, Any], latest_user_query: str) -> str | None:
    branch_keys = [key for key in question_node if key.startswith("if_")]
    if not branch_keys:
        return None

    normalized_query = normalize_branch_label(latest_user_query)
    label_matches = [
        key for key in branch_keys if normalize_branch_label(label_from_branch_key(key)) == normalized_query
    ]
    if label_matches:
        return label_matches[0]

    simple_answer = normalize_simple_answer(latest_user_query)
    if simple_answer == "YES":
        if "if_yes" in branch_keys:
            return "if_yes"
        return choose_single_branch([key for key in branch_keys if key.startswith("if_yes")])
    if simple_answer == "NO":
        if "if_no" in branch_keys:
            return "if_no"
        if "if_no_foreigner" in branch_keys:
            return "if_no_foreigner"
        if "if_no_or_has_not_applied" in branch_keys:
            return "if_no_or_has_not_applied"
        return choose_single_branch([key for key in branch_keys if key.startswith("if_no")])
    if simple_answer == "UNCLEAR":
        if "if_not_sure" in branch_keys:
            return "if_not_sure"
        if "if_not_sure_or_both" in branch_keys:
            return "if_not_sure_or_both"

    if is_deadline_question(question_node):
        deadline_branch_key = deadline_branch_key_from_text(latest_user_query)
        if deadline_branch_key in branch_keys:
            return deadline_branch_key

    keyword_rules = [
        (r"\b(foreigner|not singapore citizen|not a citizen|not pr|not a pr)\b", "if_no_foreigner"),
        (r"\b(no urgency|not urgent|no deadline|no court date|no safety issue|no family violence|no violence)\b", "if_no"),
        (r"\b(urgent|within 14 days|within fourteen days|\d+\s+days?|family violence|violence|unsafe|danger)\b", "if_yes"),
        (r"\b(no children|no child|no singaporean child|no singapore citizen child|child is not singaporean)\b", "if_no"),
        (r"\b(singaporean child|singapore citizen child|sg child|child under 21)\b", "if_yes"),
        (r"\b(rep|representation|represent|lawyer to act|lawyer)\b", "if_representation"),
        (r"\b(guidance|initial advice|advice|consultation)\b", "if_guidance"),
        (r"\b(nonprofit|non profit|charity|social enterprise)\b", "if_yes_and_nonprofit"),
        (r"\b(for profit|company|business|commercial)\b", "if_yes_and_for_profit"),
        (
            r"\b(applicant is calling|person who needs help|calling for myself|for myself|my matter)\b",
            "if_self_or_calling_on_behalf_and_unable_to_self_help",
        ),
        (r"\b(can call|able to call|can contact|able to contact)\b", "if_calling_on_behalf_and_able_to_self_help"),
        (
            r"\b(cannot call|can't call|unable to call|cannot contact|detained|hospitali[sz]ed|minor|overseas)\b",
            "if_self_or_calling_on_behalf_and_unable_to_self_help",
        ),
        (r"\b(passed|processing|pending|ongoing)\b", "if_yes_passed_or_processing"),
        (r"\b(failed means|means test failed|failed the means)\b", "if_yes_failed_means_test"),
        (r"\b(pdo.*unable|unable.*pdo|pdo.*reject|reject.*pdo)\b", "if_yes_pdo_unable_to_assist"),
        (r"\b(lab.*unable|unable.*lab|lab.*reject|reject.*lab)\b", "if_yes_lab_unable_to_assist"),
        (r"\b(lab.*able|able.*lab|lab.*not sure|not sure.*lab)\b", "if_yes_lab_able_or_not_sure"),
        (r"\b(no income|housewife|unemployed|not working|financial hardship)\b", "if_no_marginal_or_exceptional"),
        (r"\b(marginal|exceptional|hardship|medical|liabilities|dependents?)\b", "if_no_marginal_or_exceptional"),
        (r"\b(marginal)\b", "if_no_marginal"),
        (r"\b(well over|over threshold|no exceptions?)\b", "if_no_well_over_no_exceptions"),
        (r"\b(well over)\b", "if_no_well_over"),
        (r"\b(criminal|charge|charged|arrest|police)\b", "if_criminal"),
        (r"\b(divorce|matrimonial|family|custody|maintenance)\b", "if_matrimonial"),
        (r"\b(civil|employment|contract|property|estate|neighbou?r|others?)\b", "if_civil_or_others"),
        (r"\b(helper|caregiver|someone helped|calling for)\b", "if_helper_present"),
        (r"\b(unsafe|not safe|no shelter|no place|homeless)\b", "if_no_or_unclear"),
        (r"\b(under 18|minor|child|17|16|15)\b", "if_under_18"),
    ]
    for pattern, branch_key in keyword_rules:
        if branch_key in branch_keys and re.search(pattern, normalized_query):
            return branch_key

    return None


def text_has_yes_answer(text: str) -> bool:
    return bool(re.search(r"(?:→|->)\s*(?:yes|sgc/pr|singapore citizen|citizen|represented)", text))


def text_has_no_answer(text: str) -> bool:
    return bool(re.search(r"(?:→|->)\s*(?:no|no, foreigner|foreigner|not represented)", text))


def detect_contradiction_signals(answered_lines: list[str], latest_user_query: str) -> list[str]:
    prior = "\n".join(answered_lines).lower()
    latest = latest_user_query.lower()
    signals: list[str] = []

    prior_citizen_yes = ("citizen" in prior or "pr" in prior) and text_has_yes_answer(prior)
    prior_foreigner = "foreigner" in prior or (("citizen" in prior or "pr" in prior) and text_has_no_answer(prior))
    latest_foreigner = bool(re.search(r"\b(foreigner|not singapore citizen|not a citizen|not pr|not a pr)\b", latest))
    latest_citizen = bool(re.search(r"\b(singapore citizen|citizen|sgc|pr|permanent resident)\b", latest))
    if prior_citizen_yes and latest_foreigner:
        signals.append("nationality/residency changed from SGC/PR to foreigner")
    elif prior_foreigner and latest_citizen:
        signals.append("nationality/residency changed from foreigner to SGC/PR")

    prior_rep_no = "represented" in prior and text_has_no_answer(prior)
    prior_rep_yes = "represented" in prior and text_has_yes_answer(prior)
    latest_has_lawyer = bool(re.search(r"\b(my|his|her|their)?\s*(lawyer|solicitor|counsel)\b", latest))
    latest_no_lawyer = bool(re.search(r"\b(no lawyer|not represented|don't have a lawyer|do not have a lawyer)\b", latest))
    if prior_rep_no and latest_has_lawyer:
        signals.append("representation status changed to existing lawyer")
    elif prior_rep_yes and latest_no_lawyer:
        signals.append("representation status changed to no current lawyer")

    prior_advice_no = "legal advice" in prior and text_has_no_answer(prior)
    latest_prior_advice = bool(re.search(r"\b(consulted|saw|spoke to|advised by)\s+(a\s+)?lawyer\b", latest))
    if prior_advice_no and latest_prior_advice:
        signals.append("prior legal advice status changed")

    prior_personal = "business" in prior and text_has_no_answer(prior)
    prior_business = "business" in prior and text_has_yes_answer(prior)
    latest_business = bool(re.search(r"\b(company|business|shareholder|commercial|b2b)\b", latest))
    latest_personal = bool(re.search(r"\b(personal|family|divorce|housing|criminal)\b", latest))
    if prior_personal and latest_business:
        signals.append("matter type changed from personal to business/commercial")
    elif prior_business and latest_personal:
        signals.append("matter type changed from business/commercial to personal")

    prior_deadline_no = ("deadline" in prior or "court date" in prior) and text_has_no_answer(prior)
    latest_deadline = bool(re.search(r"\b(tomorrow|next week|in \d+ days|court date|deadline)\b", latest))
    if prior_deadline_no and latest_deadline:
        signals.append("urgency/deadline status changed")

    return signals


def monitor_flags(text: str) -> dict[str, bool]:
    has_deadline_within_14_days = deadline_branch_key_from_text(text) == "if_yes"
    return {
        "urgent": bool(URGENT_PATTERN.search(text)) or has_deadline_within_14_days,
        "vulnerable": bool(VULNERABLE_PATTERN.search(text)),
        "representation": bool(REPRESENTED_PATTERN.search(text)),
    }


def concurrent_monitors_from_flags(flags: dict[str, bool]) -> list[str]:
    monitors: list[str] = []
    if flags.get("urgent"):
        monitors.append("urgency")
        monitors.append("safety")
    if flags.get("vulnerable"):
        monitors.append("vulnerability")
    if flags.get("representation"):
        monitors.append("representation conflict")
    return monitors


def triggered_overlays_from_flags(flags: dict[str, bool]) -> list[str]:
    overlays: list[str] = []
    if flags.get("urgent"):
        overlays.append("GEN3-T06")
    if flags.get("vulnerable"):
        overlays.append("GEN3-T13")
    return overlays


def initial_topic_overlays(text: str, primary_entry_id: str, entries: dict[str, dict[str, Any]]) -> list[str]:
    overlays: list[str] = []
    flags = monitor_flags(text)
    has_deadline_within_14_days = deadline_branch_key_from_text(text) == "if_yes"
    if (flags.get("urgent") or has_deadline_within_14_days) and primary_entry_id != "GEN3-T06" and "GEN3-T06" in entries:
        overlays.append("GEN3-T06")
    if flags.get("vulnerable") and primary_entry_id != "GEN3-T13" and "GEN3-T13" in entries:
        overlays.append("GEN3-T13")
    return overlays


def topic_metadata_text(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in ("id", "topic", "user_query", "part_a_general_info"):
        value = entry.get(field)
        if isinstance(value, str):
            parts.append(value)
    variations = entry.get("variations")
    if isinstance(variations, list):
        parts.extend(variation for variation in variations if isinstance(variation, str))
    return " ".join(parts)


def metadata_overlap_score(query: str, entry: dict[str, Any]) -> float:
    query_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) > 2 and token not in {"applicant", "help", "need", "what", "should", "can"}
    }
    if not query_tokens:
        return 0.0
    metadata_tokens = set(re.findall(r"[a-z0-9]+", topic_metadata_text(entry).lower()))
    return min(len(query_tokens & metadata_tokens) / max(len(query_tokens), 1), 0.35)


PRIMARY_TOPIC_PRIORITY = {
    "GEN3-T02": 20,
    "GEN3-T03": 30,
    "GEN3-T04": 40,
    "GEN3-T06": 50,
    "GEN3-T13": 60,
    "GEN3-T01": 90,
}


def topic_candidate_type(entry_id: str, text: str) -> str:
    if entry_id == "GEN3-T06":
        return "monitor" if any(
            INITIAL_TOPIC_PATTERNS[candidate].search(text)
            for candidate in ("GEN3-T02", "GEN3-T03", "GEN3-T04")
            if candidate in INITIAL_TOPIC_PATTERNS
        ) else "primary_topic"
    if entry_id == "GEN3-T13" and not GEN3_T13_PRIMARY_PATTERN.search(text):
        return "monitor"
    return "primary_topic"


def initial_topic_candidates(
    entries: dict[str, dict[str, Any]],
    latest_user_query: str,
) -> list[PBSGTopicCandidate]:
    normalized = re.sub(r"\s+", " ", latest_user_query).strip()
    candidates: list[PBSGTopicCandidate] = []
    for entry_id, pattern in INITIAL_TOPIC_PATTERNS.items():
        if entry_id not in entries:
            continue
        match = pattern.search(normalized)
        score = metadata_overlap_score(normalized, entries[entry_id])
        evidence = "metadata match"
        matched_facts: list[str] = []
        if match:
            score += 0.65
            evidence = match.group(0)
            matched_facts.append(match.group(0))
        candidate_type = topic_candidate_type(entry_id, normalized)
        if entry_id == "GEN3-T13" and match and candidate_type == "monitor":
            score -= 0.45
        if entry_id == "GEN3-T06" and match and candidate_type == "monitor":
            score -= 0.35
        if score > 0:
            candidates.append(
                PBSGTopicCandidate(
                    entry_id=entry_id,
                    confidence=min(score, 1.0),
                    evidence=evidence,
                    matched_facts=matched_facts,
                    candidate_type=candidate_type,
                )
            )
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.candidate_type != "primary_topic",
            PRIMARY_TOPIC_PRIORITY.get(candidate.entry_id, 80),
            -candidate.confidence,
        ),
    )


def queued_topics_from_candidates(
    candidates: list[PBSGTopicCandidate],
    primary_entry_id: str,
    entries: dict[str, dict[str, Any]],
) -> list[PBSGQueuedTopic]:
    queued: list[PBSGQueuedTopic] = []
    for candidate in candidates:
        if candidate.entry_id == primary_entry_id or candidate.entry_id not in entries:
            continue
        if candidate.candidate_type != "primary_topic" or candidate.confidence < 0.6:
            continue
        queued.append(
            PBSGQueuedTopic(
                entry_id=candidate.entry_id,
                evidence=candidate.evidence,
                confidence=candidate.confidence,
            )
        )
    return queued


def resolve_initial_topics(
    entries: dict[str, dict[str, Any]],
    latest_user_query: str,
) -> PBSGTopicResolution | None:
    if not entries:
        return None
    fallback_entry_id = "GEN3-T01" if "GEN3-T01" in entries else sorted(entries)[0]
    normalized = re.sub(r"\s+", " ", latest_user_query).strip()
    if not normalized or BARE_START_PATTERN.match(normalized) or not SUBSTANTIVE_FACT_PATTERN.search(normalized):
        return PBSGTopicResolution(
            entry_id=fallback_entry_id,
            confidence=0.55,
            reason="defaulted to first-contact triage because no clear specialty facts were present",
            overlays=initial_topic_overlays(normalized, fallback_entry_id, entries),
        )
    if CAPITAL_OFFENCE_PATTERN.search(normalized) and "GEN3-T02" in entries:
        candidates = initial_topic_candidates(entries, latest_user_query)
        queued_topics = queued_topics_from_candidates(candidates, "GEN3-T02", entries)
        return PBSGTopicResolution(
            entry_id="GEN3-T02",
            confidence=1.0,
            reason="capital offence signal",
            overlays=initial_topic_overlays(normalized, "GEN3-T02", entries),
            queued_topics=queued_topics,
            candidates=candidates,
        )

    candidates = initial_topic_candidates(entries, latest_user_query)
    primary_candidates = [
        candidate
        for candidate in candidates
        if candidate.candidate_type == "primary_topic" and candidate.confidence >= 0.6
    ]
    if not primary_candidates:
        return PBSGTopicResolution(
            entry_id=fallback_entry_id,
            confidence=0.55,
            reason="defaulted to first-contact triage because no Golden Set topic matched confidently",
            overlays=initial_topic_overlays(normalized, fallback_entry_id, entries),
            candidates=candidates,
        )

    best_candidate = primary_candidates[0]
    queued_topics = queued_topics_from_candidates(primary_candidates, best_candidate.entry_id, entries)
    reason = best_candidate.evidence
    if queued_topics:
        reason = f"multi-topic match: {best_candidate.evidence}"
    return PBSGTopicResolution(
        entry_id=best_candidate.entry_id,
        confidence=best_candidate.confidence,
        reason=reason,
        overlays=initial_topic_overlays(normalized, best_candidate.entry_id, entries),
        queued_topics=queued_topics,
        candidates=candidates,
    )


def resolve_initial_topic(
    entries: dict[str, dict[str, Any]],
    latest_user_query: str,
) -> PBSGTopicResolution | None:
    return resolve_initial_topics(entries, latest_user_query)


def detect_candidate_topics(
    text: str,
    entries: dict[str, dict[str, Any]],
    active_workflow: str | None,
) -> list[PBSGQueuedTopic]:
    topics: list[PBSGQueuedTopic] = []
    for entry_id, pattern in TOPIC_SIGNAL_RULES:
        if entry_id == active_workflow or entry_id not in entries:
            continue
        match = pattern.search(text)
        if match:
            topics.append(PBSGQueuedTopic(entry_id=entry_id, evidence=match.group(0), confidence=0.8))
    return topics


def classify_turn_interrupt(
    entries: dict[str, dict[str, Any]],
    state: PBSGTriageState,
    latest_user_query: str,
) -> PBSGTurnClassification:
    if state.mode != "FAST_ROUTING" or not state.pending_entry_id or not state.current_question_id:
        return PBSGTurnClassification(turn_type="not_locked", should_call_llm=False)

    entry = entries.get(state.pending_entry_id)
    branching_logic = entry.get("branching_logic") if entry else None
    question_node = branching_logic.get(state.current_question_id) if isinstance(branching_logic, dict) else None
    if not isinstance(question_node, dict):
        return PBSGTurnClassification(turn_type="not_locked", should_call_llm=False)

    branch_key = branch_key_for_answer(question_node, latest_user_query)
    candidates = detect_candidate_topics(latest_user_query, entries, state.active_workflow)
    has_additive = bool(ADDITIVE_PATTERN.search(latest_user_query))
    has_correction = bool(CORRECTION_PATTERN.search(latest_user_query)) or bool(state.contradiction_signals)
    has_clarification = bool(CLARIFICATION_QUESTION_PATTERN.search(latest_user_query))
    has_safety = bool(SAFETY_INTERRUPT_PATTERN.search(latest_user_query)) and branch_key != "if_no"

    if has_correction:
        return PBSGTurnClassification(
            turn_type="correction",
            should_call_llm=True,
            reason="correction signal",
            pending_branch_key=branch_key,
            new_topics=candidates,
            affects_prior_answer=True,
        )
    if has_clarification:
        return PBSGTurnClassification(
            turn_type="clarification",
            should_call_llm=True,
            reason="clarification question",
            pending_branch_key=branch_key,
            new_topics=candidates,
        )
    if has_safety:
        return PBSGTurnClassification(
            turn_type="safety_interrupt",
            should_call_llm=True,
            reason="safety or urgency signal",
            pending_branch_key=branch_key,
            new_topics=candidates,
        )
    if candidates and has_additive:
        return PBSGTurnClassification(
            turn_type="answer_plus_new_topic" if branch_key else "new_topic_only",
            should_call_llm=True,
            reason="possible new topic in locked flow",
            pending_branch_key=branch_key,
            new_topics=candidates,
        )
    if branch_key:
        return PBSGTurnClassification(
            turn_type="answer_only",
            should_call_llm=False,
            pending_branch_key=branch_key,
            pending_answer_confidence=1.0,
        )
    if candidates:
        return PBSGTurnClassification(
            turn_type="new_topic_only",
            should_call_llm=True,
            reason="possible new topic in locked flow",
            pending_branch_key=branch_key,
            new_topics=candidates,
        )
    return PBSGTurnClassification(turn_type="ambiguous", should_call_llm=True, reason="answer did not map locally")


def build_triage_state(
    messages: list[ChatCompletionMessageParam],
    entries: dict[str, dict[str, Any]],
    latest_user_query: str,
) -> PBSGTriageState:
    assistant_content = latest_assistant_content(messages)
    selected_entry_id = extract_selected_entry_id(assistant_content)
    targets = extract_question_targets(assistant_content)
    pending_target = targets[-1] if targets else None
    pending_entry_id = pending_target.entry_id if pending_target and pending_target.entry_id else selected_entry_id
    current_question_id = pending_target.question_id if pending_target else None
    conversation_text = "\n".join(message_content_to_text(message.get("content")) for message in messages)
    conversation_text = f"{conversation_text}\n{latest_user_query}"
    flags = monitor_flags(conversation_text)
    topic_workflows = extract_topic_workflows_from_messages(messages)
    completed_workflows = extract_completed_workflows_from_messages(messages)
    for workflow_id in extract_topic_workflows(assistant_content):
        if workflow_id not in topic_workflows:
            topic_workflows.append(workflow_id)
    for workflow_id in extract_completed_workflows(assistant_content):
        if workflow_id not in completed_workflows:
            completed_workflows.append(workflow_id)
    active_workflow = pending_entry_id or selected_entry_id
    answered_lines = extract_answered_lines(assistant_content)
    fact_ledger = extract_user_fact_ledger(entries, messages, latest_user_query)
    branch_keys = allowed_branch_keys(entries, pending_entry_id, current_question_id)
    contradiction_signals = detect_contradiction_signals(answered_lines, latest_user_query)
    workflow_locked = bool(selected_entry_id and selected_entry_id in entries)
    if contradiction_signals:
        mode = "REPAIR"
    elif workflow_locked:
        mode = "FAST_ROUTING"
    else:
        mode = "ORCHESTRATION"
    queued_workflows = [
        workflow_id
        for workflow_id in topic_workflows
        if workflow_id != active_workflow and workflow_id not in completed_workflows
    ]
    parent_workflow = selected_entry_id if pending_entry_id == "GEN3-T06" and selected_entry_id != "GEN3-T06" else None
    if not parent_workflow and pending_entry_id == "GEN3-T06":
        resume_matches = re.findall(
            r"After this urgent path:\s*resume\s+(GEN3-[A-Z0-9-]+)\s+(Q\d+[A-Z]?)",
            conversation_text,
            flags=re.IGNORECASE,
        )
        if resume_matches:
            parent_workflow = resume_matches[-1][0].upper()
    resume_question_id = {"GEN3-T02": "Q3", "GEN3-T03": "Q2"}.get(parent_workflow or "")
    is_terminal_route = bool(assistant_content and "**Routing Recommendation:**" in assistant_content and selected_entry_id)
    routing_completion_status = "not_started"
    if contradiction_signals:
        routing_completion_status = "repair_required"
    elif is_terminal_route and queued_workflows:
        routing_completion_status = "awaiting_topic_resolution"
    elif is_terminal_route:
        routing_completion_status = "completed"
    elif workflow_locked:
        routing_completion_status = "in_progress"

    return PBSGTriageState(
        mode=mode,
        workflow_id=parent_workflow or selected_entry_id,
        workflow_locked=workflow_locked,
        active_workflow=active_workflow,
        queued_workflows=queued_workflows,
        completed_workflows=completed_workflows,
        concurrent_monitors=concurrent_monitors_from_flags(flags),
        current_question_id=current_question_id,
        pending_entry_id=pending_entry_id,
        previous_states=answered_lines,
        answered_lines=answered_lines,
        allowed_branch_keys=branch_keys,
        allowed_transitions=branch_keys,
        latest_answer_classification=normalize_simple_answer(latest_user_query),
        contradiction_signals=contradiction_signals,
        active_monitors=flags,
        repair_required=bool(contradiction_signals),
        parent_workflow=parent_workflow,
        resume_question_id=resume_question_id,
        triggered_overlays=triggered_overlays_from_flags(flags),
        fact_ledger=fact_ledger,
        unanswered_required_fields=unanswered_required_fields_for_state(
            entries, pending_entry_id, current_question_id, fact_ledger
        ),
        suspended_workflows=queued_workflows.copy(),
        routing_completion_status=routing_completion_status,
    )


def format_state_prompt(state: PBSGTriageState) -> str:
    lines = [
        "",
        "DETERMINISTIC ROUTING STATE (internal; do not expose as JSON)",
        f"- Mode: {state.mode}.",
    ]
    if not state.workflow_locked or not state.workflow_id:
        lines.append("- Workflow locked: false. Use ORCHESTRATION MODE to identify and lock the safest workflow before execution.")
        return "\n".join(lines)

    lines.append(
        f"- Workflow locked: {state.workflow_id}. Do not re-run workflow identification unless a branch explicitly hands off, a monitor requires escalation, material new facts appear, or repair is required."
    )
    if state.active_workflow:
        lines.append(f"- Active workflow: {state.active_workflow}. Only this workflow may ask the next primary question.")
    if state.queued_workflows:
        lines.append(f"- Queued workflows: {', '.join(state.queued_workflows)}. Do not ask from these workflows until the active workflow is routed, suspended by rule, or explicitly hands off.")
    if state.completed_workflows:
        lines.append(f"- Completed workflows: {', '.join(state.completed_workflows)}.")
    if state.concurrent_monitors:
        lines.append(f"- Concurrent monitors: {', '.join(state.concurrent_monitors)}. They may interrupt only for urgency threshold, safety issue, or required escalation.")
    if state.pending_entry_id and state.current_question_id:
        lines.append(f"- Pending question: {state.pending_entry_id} {state.current_question_id}. Treat the user's latest message as the answer to this question.")
    if state.routing_completion_status:
        lines.append(f"- Routing completion status: {state.routing_completion_status}.")
    if state.unanswered_required_fields:
        lines.append(f"- Unanswered required fields: {', '.join(state.unanswered_required_fields)}.")
    if state.fact_ledger:
        lines.append("- Known facts in the case ledger:")
        lines.extend(
            f"  - {fact.fact_key} = {fact.value} (source: {fact.source}, confidence: {fact.confidence:.2f})"
            for fact in state.fact_ledger[-8:]
        )
    if state.active_side_enquiry:
        lines.append(
            f"- Active side enquiry: {state.active_side_enquiry.question}. Preserve the pending routing question and resume it after answering."
        )
    if state.latest_answer_classification:
        lines.append(f"- Latest answer classification: {state.latest_answer_classification}. In FAST_ROUTING mode, execute the matching branch without filler.")
    if state.allowed_transitions:
        lines.append(f"- Allowed transitions for the pending question: {', '.join(state.allowed_transitions)}.")
    if state.repair_required:
        lines.append("- Repair required: true. Enter REPAIR MODE before continuing.")
    if state.contradiction_signals:
        lines.append(f"- Material contradiction signals: {'; '.join(state.contradiction_signals)}.")
        lines.append("- Invalidate downstream decisions that depended on the contradicted state, relock the workflow, then return to FAST_ROUTING mode.")
    active = [name for name, enabled in state.active_monitors.items() if enabled]
    if active:
        lines.append(f"- Active monitors triggered: {', '.join(active)}. Apply the Golden Set escalation/concurrent-routing rule if it is triggered by the current workflow.")
    if state.answered_lines:
        lines.append("- Prior visible triage state to preserve:")
        lines.extend(f"  - {line}" for line in state.answered_lines)
    lines.append("- If the latest answer is ambiguous, unsupported, contradictory, or cannot map to one allowed branch, ask the approved single clarification or escalate to PBSG Staff. Never guess.")
    return "\n".join(lines)


def find_route_text(entry: dict[str, Any] | None, route_label: str | None) -> str | None:
    if not entry or not route_label:
        return None
    routing = entry.get("routing")
    if not isinstance(routing, list):
        return None
    route_prefix = route_label.lower()
    for route in routing:
        if isinstance(route, str) and route.lower().startswith(route_prefix):
            return route
    return None


def structured_route_from_entry(entry: dict[str, Any] | None, route_label: str | None) -> StructuredRoute | None:
    if not entry or not route_label:
        return None
    routing_structured = entry.get("routing_structured")
    route_card = routing_structured.get(route_label) if isinstance(routing_structured, dict) else None
    if not isinstance(route_card, dict):
        return None

    def route_list(field: str) -> list[str]:
        value = route_card.get(field)
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    name = route_card.get("name")
    script = route_card.get("script")
    if not isinstance(script, str) or not script.strip():
        return None
    return StructuredRoute(
        route_label=route_label,
        route_name=name if isinstance(name, str) else "",
        script=script.strip(),
        needs_to_know=route_list("needs_to_know"),
        access=route_list("access"),
        prepare=route_list("prepare"),
        intern_steps=route_list("intern_steps"),
        caveats=route_list("caveats"),
    )


def unique_preserve_order(items: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip(" .;")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def normalize_route_key(text: str) -> str:
    text = URL_PATTERN.sub("", text.lower())
    text = EMAIL_PATTERN.sub("", text)
    text = PHONE_PATTERN.sub("", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_route_header(route_text: str) -> tuple[str, str, str]:
    match = re.match(r"(Route\s+[A-Z])\s*(?:\(([^)]*)\))?:\s*(.*)", route_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return "Route", "", route_text
    return match.group(1).title(), match.group(2) or "", match.group(3).strip()


def split_route_sentences(text: str) -> list[str]:
    protected = re.sub(r"(https?://\S+)", lambda match: match.group(1).replace(".", "<DOT>"), text)
    sentences = re.split(r"(?<=[.!?])\s+", protected)
    return [sentence.replace("<DOT>", ".").strip() for sentence in sentences if sentence.strip()]


def clean_route_script(text: str) -> str:
    text = re.sub(r"https?://\S+", "the relevant application page.", text)
    text = re.sub(
        r"Share about CLAS.*?and inform applicant that they may be eligible",
        "You may be eligible for CLAS",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bIf applicant is unable to self-apply\b", "If you cannot self-apply", text, flags=re.IGNORECASE)
    text = re.sub(r"\bIf applicant has difficulties going to PBSG\b", "If you have difficulties going to PBSG", text, flags=re.IGNORECASE)
    text = re.sub(r"\binform applicant to apply for CLAS:\s*", "Please apply for CLAS through ", text, flags=re.IGNORECASE)
    text = re.sub(r"\binform applicant to go to\b", "you may go to", text, flags=re.IGNORECASE)
    text = re.sub(r"\bInform the applicant that\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bInform applicant that\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bInform applicant to\s+", "Please ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAdvise the applicant to\s+", "Please ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bAsk the applicant to\s+", "Please ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe applicant's\b", "your", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthe applicant\b", "you", text, flags=re.IGNORECASE)
    text = re.sub(r"\bapplicant's\b", "your", text, flags=re.IGNORECASE)
    text = re.sub(r"\bapplicant\b", "you", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPBSG can provide general information on schemes to you\b", "PBSG can provide general information on schemes", text)
    text = re.sub(r"\bIf you is\b", "If you are", text, flags=re.IGNORECASE)
    text = re.sub(r"\byou is\b", "you are", text, flags=re.IGNORECASE)
    text = re.sub(r"\bif you cannot self-apply,\s*you may go\b", "If you cannot self-apply, you may go", text, flags=re.IGNORECASE)
    text = re.sub(
        r"If you cannot self-apply, you may go to PBSG Counter at State Courts Help Centre\s*\([^)]*\)\s*with documents needed\.?",
        "If you cannot self-apply, you may go to the PBSG Counter at the State Courts Help Centre with the required documents.",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\.\s*\.", ".", text)
    return re.sub(r"\s+", " ", text).strip(" ;")


def extract_route_script(body: str) -> str:
    sentences = split_route_sentences(body)
    script_candidates = [
        sentence
        for sentence in sentences
        if re.search(
            r"\b(Inform|Advise|Describe|Share about|Say to|Direct|Please|PBSG provides|You may be eligible|should contact)\b",
            sentence,
            flags=re.IGNORECASE,
        )
    ]
    if not script_candidates:
        script_candidates = sentences[:2]
    script = " ".join(clean_route_script(sentence) for sentence in script_candidates[:3])
    if len(script) > 420:
        script = script[:420].rsplit(" ", 1)[0].rstrip(",;") + "."
    return script or "I need to check this with PBSG Staff before going further."


def extract_access_items(body: str) -> list[str]:
    items: list[str] = []
    items.extend(f"Website / application link: {url}" for url in URL_PATTERN.findall(body))
    items.extend(f"Email: {email}" for email in EMAIL_PATTERN.findall(body))
    items.extend(f"Phone: {phone}" for phone in PHONE_PATTERN.findall(body))
    for address in ADDRESS_PATTERN.findall(body):
        cleaned_address = re.sub(r"\s+with documents needed.*", "", address, flags=re.IGNORECASE).rstrip(")")
        items.append(f"Address: {cleaned_address}")
    items.extend(f"Opening hours / appointment instructions: {hours.strip()}" for hours in HOURS_PATTERN.findall(body))
    return unique_preserve_order(items)


def extract_route_items(body: str, patterns: list[str]) -> list[str]:
    sentences = split_route_sentences(body)
    return unique_preserve_order(
        [
            sentence
            for sentence in sentences
            if any(re.search(pattern, sentence, flags=re.IGNORECASE) for pattern in patterns)
        ]
    )


def summarize_prepare_item(sentence: str) -> list[str]:
    items: list[str] = []
    if re.search(r"\bmeans test|financial documents?\b", sentence, flags=re.IGNORECASE):
        items.append("Financial documents for the means test")
    if re.search(r"\bcharge details|CLAS|criminal legal aid\b", sentence, flags=re.IGNORECASE):
        items.append("Charge details and other case details if asked on application")
    if re.search(r"\brejection letter|rejection|reasons?\b", sentence, flags=re.IGNORECASE):
        items.append("Relevant rejection letters or reasons, if applicable")
    if not items and re.search(r"\bdocuments?\b", sentence, flags=re.IGNORECASE):
        items.append("Documents needed for the application")
    return items


def summarize_intern_step(sentence: str) -> list[str]:
    lowered = sentence.lower()
    if "apply for clas" in lowered or "criminal legal aid application" in lowered:
        return ["Share the CLAS application link with the applicant"]
    if "unable to self-apply" in lowered or "cannot self-apply" in lowered:
        return ["If the applicant cannot self-apply, direct them to the PBSG Counter"]
    if "difficulties going to pbsg" in lowered:
        return ["If there are difficulties going to PBSG, escalate to PBSG Staff"]
    if "take down" in lowered:
        return [sentence]
    if "email" in lowered and "same day" in lowered:
        return ["Email the information to PBSG Staff on the same day"]
    if "do not attempt to advise" in lowered:
        return ["Do not attempt to advise further"]
    if "share" in lowered:
        return [re.sub(r"\s+", " ", sentence).strip(" .")]
    if "escalate" in lowered:
        return [re.sub(r"\s+", " ", sentence).strip(" .")]
    return []


def route_sentences_without_script(body: str, script: str) -> list[str]:
    script_key = normalize_route_key(script)
    sentences: list[str] = []
    for sentence in split_route_sentences(body):
        cleaned_script = clean_route_script(sentence)
        sentence_key = normalize_route_key(cleaned_script)
        if sentence_key and (sentence_key in script_key or script_key in sentence_key):
            continue
        sentences.append(sentence)
    return sentences


def structure_route(route_text: str, entry: dict[str, Any] | None = None, route_label: str | None = None) -> StructuredRoute:
    structured_route = structured_route_from_entry(entry, route_label)
    if structured_route:
        return structured_route

    route_label, route_name, body = strip_route_header(route_text)
    script = extract_route_script(body)
    access = extract_access_items(body)
    remaining_sentences = route_sentences_without_script(body, script)
    intern_steps = unique_preserve_order(
        [
            item
            for sentence in remaining_sentences
            for item in summarize_intern_step(sentence)
        ]
    )
    prepare = unique_preserve_order(
        [
            item
            for sentence in remaining_sentences
            for item in summarize_prepare_item(sentence)
        ]
    )
    caveats = unique_preserve_order(
        [
            sentence
            for sentence in remaining_sentences
            if re.search(r"third-party websites|not affiliated|Do NOT attempt to advise|not able to give legal advice", sentence, flags=re.IGNORECASE)
        ]
    )
    needs_to_know = unique_preserve_order(
        [
            sentence
            for sentence in remaining_sentences
            if re.search(
                r"\beligib|\bnot managed\b|\bnot needed\b|\bnot likely\b|\bunable to assist\b|\bfirst\b|\bfree\b|\blow-income\b|\brepresentation\b|\bguidance\b",
                sentence,
                flags=re.IGNORECASE,
            )
            and not re.search(r"\bapply\b|\bgo to\b|\btake down\b|\bemail\b|\bshare\b|\bdocuments? needed\b", sentence, flags=re.IGNORECASE)
        ]
    )
    if route_label == "Route E" and "CLAS" in route_name:
        prepare = unique_preserve_order(prepare + ["Financial documents for the means test", "Charge details and other case details if asked on application"])
        intern_steps = unique_preserve_order(
            intern_steps
            + [
                "Share the CLAS application link with the applicant",
                "If the applicant cannot self-apply, direct them to the PBSG Counter",
                "If there are difficulties going to PBSG, escalate to PBSG Staff",
            ]
        )
    return StructuredRoute(
        route_label=route_label,
        route_name=route_name,
        script=script,
        needs_to_know=needs_to_know[:5],
        access=access[:8],
        prepare=prepare[:5],
        intern_steps=intern_steps[:6],
        caveats=caveats[:4],
    )


def extract_route_label(outcome: str) -> str | None:
    match = ROUTE_PATTERN.search(outcome)
    return f"Route {match.group(1).upper()}" if match else None


def parse_transition_outcome(
    entries: dict[str, dict[str, Any]],
    entry_id: str,
    question_id: str,
    branch_key: str,
    outcome: str,
) -> PBSGTransition:
    local_question_match = re.search(r"\bProceed to\s+(Q\d+[A-Z]?)\b", outcome, flags=re.IGNORECASE)
    route_label = extract_route_label(outcome)
    if local_question_match and route_label:
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="concurrent_route_question",
            target_entry_id=entry_id,
            target_question_id=local_question_match.group(1).upper(),
            route_label=route_label,
        )
    if local_question_match:
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="proceed_question",
            target_entry_id=entry_id,
            target_question_id=local_question_match.group(1).upper(),
        )

    handoff_match = re.search(r"\bProceed to\s+(GEN3-[A-Z0-9-]+)\b", outcome, flags=re.IGNORECASE)
    if handoff_match:
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="handoff_entry",
            target_entry_id=handoff_match.group(1).upper(),
            target_question_id="Q1",
        )

    if outcome.lower().startswith("clarify"):
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="clarification",
            target_entry_id=entry_id,
            target_question_id=question_id,
            route_label=extract_route_label(outcome),
            clarification_text=first_quoted_text(outcome) or outcome,
        )

    entry = entries.get(entry_id)
    route_text = find_route_text(entry, route_label)
    route_context = " ".join(part for part in [outcome, route_text] if part)
    nested_match = re.search(r"\bProceed to\s+(GEN3-[A-Z0-9-]+)\b", route_context, flags=re.IGNORECASE)
    if route_label and nested_match:
        resume_match = re.search(
            r"following which.*?\bproceed to\s+(Q\d+[A-Z]?)\s+of\s+(GEN3-[A-Z0-9-]+)",
            route_context,
            flags=re.IGNORECASE,
        )
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="nested_stream",
            target_entry_id=nested_match.group(1).upper(),
            target_question_id="Q1",
            route_label=route_label,
            nested_entry_id=nested_match.group(1).upper(),
            resume_entry_id=resume_match.group(2).upper() if resume_match else entry_id,
            resume_question_id=resume_match.group(1).upper() if resume_match else None,
        )

    if entry_id == "GEN3-T06" and route_label == "Route D" and re.search(r"\bReturn to\s+GEN3-T01\b", route_context, flags=re.IGNORECASE):
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="handoff_entry",
            target_entry_id="GEN3-T01",
            target_question_id="Q1",
            route_label=route_label,
        )

    if route_label:
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="terminal_route",
            route_label=route_label,
        )

    cross_reference_match = re.search(r"\bCross-reference\s+(GEN3-[A-Z0-9-]+)\b", outcome, flags=re.IGNORECASE)
    if cross_reference_match:
        return PBSGTransition(
            entry_id=entry_id,
            question_id=question_id,
            branch_key=branch_key,
            outcome=outcome,
            transition_type="cross_reference",
            target_entry_id=cross_reference_match.group(1).upper(),
            target_question_id="Q1",
        )

    return PBSGTransition(
        entry_id=entry_id,
        question_id=question_id,
        branch_key=branch_key,
        outcome=outcome,
        transition_type="instruction",
    )


class PBSGWorkflowGraph:
    def __init__(self, entries: dict[str, dict[str, Any]]):
        self.entries = entries
        self.edges = self.build_edges(entries)

    def build_edges(self, entries: dict[str, dict[str, Any]]) -> dict[tuple[str, str, str], PBSGWorkflowEdge]:
        edges: dict[tuple[str, str, str], PBSGWorkflowEdge] = {}
        for entry_id, entry in entries.items():
            branching_logic = entry.get("branching_logic")
            if not isinstance(branching_logic, dict):
                continue
            for question_id, question_node in branching_logic.items():
                if not isinstance(question_node, dict):
                    continue
                for branch_key, outcome in question_node.items():
                    if not branch_key.startswith("if_") or not isinstance(outcome, str):
                        continue
                    transition = parse_transition_outcome(entries, entry_id, question_id, branch_key, outcome)
                    edges[(entry_id, question_id, branch_key)] = PBSGWorkflowEdge(
                        entry_id=entry_id,
                        question_id=question_id,
                        branch_key=branch_key,
                        transition=transition,
                    )
        return edges

    def edge_for(self, entry_id: str | None, question_id: str | None, branch_key: str | None) -> PBSGWorkflowEdge | None:
        if not entry_id or not question_id or not branch_key:
            return None
        return self.edges.get((entry_id, question_id, branch_key))

    def transition_for(self, entry_id: str | None, question_id: str | None, branch_key: str | None) -> PBSGTransition | None:
        edge = self.edge_for(entry_id, question_id, branch_key)
        return edge.transition if edge else None

    def transitions(self) -> list[PBSGTransition]:
        return [edge.transition for edge in self.edges.values()]


def gen3_t13_cues(text: str) -> set[str]:
    lowered = text.lower()
    cues: set[str] = set()
    if re.search(r"\b(minor|under 18|child is calling|17|16|15)\b", lowered):
        cues.add("minor")
    if re.search(r"\b(violence|abuse|unsafe|not safe|danger|self[- ]?harm)\b", lowered):
        cues.add("active_safety")
    if re.search(r"\b(no shelter|safe place|homeless|no food|no money|basic needs)\b", lowered):
        cues.add("basic_needs")
    if re.search(r"\b(social worker|fsc|case officer|counsellor|counselor)\b", lowered):
        cues.add("fsc_referred")
    if re.search(r"\b(elderly|confused|dementia|isolated)\b", lowered):
        cues.add("elderly")
    if re.search(r"\b(disabled|disability|mobility|cognitive)\b", lowered):
        cues.add("disability")
    if re.search(r"\b(language barrier|interpreter|limited english|translation)\b", lowered):
        cues.add("language_barrier")
    if re.search(r"\b(cannot use|can't use|no email|no internet|technology|tech[- ]?illiterate)\b", lowered):
        cues.add("technology_barrier")
    if re.search(r"\b(not sure|unclear|unknown|maybe)\b", lowered):
        cues.add("unclear")
    return cues


def resolve_gen3_t13_cue_transition(latest_user_query: str, question_id: str | None = None) -> PBSGTransition | None:
    cues = gen3_t13_cues(latest_user_query)
    question = question_id or "Q1"
    if {"active_safety", "basic_needs"} & cues:
        return PBSGTransition(
            entry_id="GEN3-T13",
            question_id=question,
            branch_key="cue_urgent",
            outcome="Cross-reference GEN3-T06 for urgent safety or basic needs assessment.",
            transition_type="cross_reference",
            target_entry_id="GEN3-T06",
            target_question_id="Q1",
        )
    if "minor" in cues:
        route_label = "Route B"
        outcome = "Route B (Minor — Special Handling)"
    elif {"active_safety", "fsc_referred"} & cues or len(cues - {"unclear"}) >= 2:
        route_label = "Route A"
        outcome = "Route A (High-Vulnerability — Escalate + Community Law Centre)"
    elif len(cues - {"unclear"}) == 1:
        route_label = "Route C"
        outcome = "Route C (Low-Vulnerability — Standard Triage with Adaptations)"
    elif "unclear" in cues:
        route_label = "Route A"
        outcome = "Route A (Escalate to PBSG Staff for professional assessment)"
    else:
        return None
    return PBSGTransition(
        entry_id="GEN3-T13",
        question_id=question,
        branch_key=f"cue_{route_label.lower().replace(' ', '_')}",
        outcome=outcome,
        transition_type="terminal_route",
        route_label=route_label,
    )


def resolve_expected_transition(
    entries: dict[str, dict[str, Any]],
    state: PBSGTriageState,
    latest_user_query: str,
) -> PBSGTransition | None:
    if state.mode != "FAST_ROUTING" or not state.pending_entry_id or not state.current_question_id:
        return None
    if state.pending_entry_id == "GEN3-T13":
        return resolve_gen3_t13_cue_transition(latest_user_query, state.current_question_id)
    entry = entries.get(state.pending_entry_id)
    branching_logic = entry.get("branching_logic") if entry else None
    question_node = branching_logic.get(state.current_question_id) if isinstance(branching_logic, dict) else None
    if not isinstance(question_node, dict):
        return None

    branch_key = branch_key_for_answer(question_node, latest_user_query)
    if not branch_key:
        return None
    outcome = question_node.get(branch_key)
    if not isinstance(outcome, str):
        return None
    graph = PBSGWorkflowGraph(entries)
    transition = graph.transition_for(state.pending_entry_id, state.current_question_id, branch_key)
    return transition or parse_transition_outcome(entries, state.pending_entry_id, state.current_question_id, branch_key, outcome)


def validate_response_questions(content: str | None, entries: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
    targets = extract_question_targets(content)
    if not targets:
        return True, None
    if len(targets) > 1:
        return False, "response contains more than one primary triage question"

    selected_entry_id = extract_selected_entry_id(content)
    target = targets[0]
    entry_id = target.entry_id or selected_entry_id
    if not entry_id:
        return False, "response asks a triage question without a selected entry"
    entry = entries.get(entry_id)
    if not entry:
        return False, f"response asks a question for unknown entry {entry_id}"
    branching_logic = entry.get("branching_logic")
    if not isinstance(branching_logic, dict) or target.question_id not in branching_logic:
        return False, f"response asks {entry_id} {target.question_id}, which is not in the active branching logic"
    return True, None


def extract_response_route_label(content: str | None) -> str | None:
    if not content:
        return None
    routing_match = re.search(r"\*\*Routing Recommendation:\*\*\s*(Route\s+[A-Z])\b", content, flags=re.IGNORECASE)
    if routing_match:
        return routing_match.group(1).title()
    return None


def collapse_duplicate_route_cards(content: str | None) -> str | None:
    if not content:
        return content
    selected_matches = list(re.finditer(r"\*\*Selected Entry:\*\*\s*([A-Z0-9-]+)", content, flags=re.IGNORECASE))
    if len(selected_matches) < 2:
        return content
    blocks: list[tuple[int, int, str, str, str | None]] = []
    for index, match in enumerate(selected_matches):
        start = match.start()
        end = selected_matches[index + 1].start() if index + 1 < len(selected_matches) else len(content)
        block = content[start:end]
        blocks.append((start, end, block, match.group(1).upper(), extract_response_route_label(block)))

    prefix = content[: blocks[0][0]]
    kept_blocks: list[str] = []
    seen_route_cards: set[tuple[str, str]] = set()
    changed = False

    for _, _, block, entry_id, route_label in blocks:
        if route_label:
            key = (entry_id, route_label)
            if key in seen_route_cards:
                changed = True
                continue
            seen_route_cards.add(key)
        kept_blocks.append(block)

    if not changed:
        return content
    return f"{prefix}{''.join(kept_blocks)}".rstrip()


def validate_response_transition(
    content: str | None,
    entries: dict[str, dict[str, Any]],
    expected_transition: PBSGTransition | None,
) -> tuple[bool, str | None]:
    del entries
    if not content or not expected_transition:
        return True, None

    targets = extract_question_targets(content)
    route_label = extract_response_route_label(content)
    selected_entry_id = extract_selected_entry_id(content)

    if expected_transition.transition_type in {"proceed_question", "nested_stream", "concurrent_route_question", "cross_reference"}:
        expected_entry_id = expected_transition.target_entry_id
        expected_question_id = expected_transition.target_question_id
        if not targets:
            return False, f"response did not ask expected {expected_entry_id} {expected_question_id}"
        if len(targets) > 1:
            return False, "response contains more than one primary triage question"
        target = targets[0]
        actual_entry_id = target.entry_id or selected_entry_id
        if actual_entry_id != expected_entry_id or target.question_id != expected_question_id:
            return (
                False,
                f"response asks {actual_entry_id} {target.question_id}; expected {expected_entry_id} {expected_question_id}",
            )
        if route_label and expected_transition.transition_type == "proceed_question":
            return False, f"response routed {route_label}; expected a local question"
        return True, None

    if expected_transition.transition_type == "terminal_route":
        if route_label != expected_transition.route_label:
            return False, f"response route {route_label}; expected {expected_transition.route_label}"
        if targets:
            return False, "terminal route response must not ask another primary question"
        return True, None

    if expected_transition.transition_type == "handoff_entry":
        if selected_entry_id != expected_transition.target_entry_id:
            return False, f"response selected {selected_entry_id}; expected handoff to {expected_transition.target_entry_id}"
        if targets:
            target = targets[0]
            actual_entry_id = target.entry_id or selected_entry_id
            expected_question_id = expected_transition.target_question_id or "Q1"
            if actual_entry_id != expected_transition.target_entry_id or target.question_id != expected_question_id:
                return (
                    False,
                    f"response asks {actual_entry_id} {target.question_id}; expected "
                    f"{expected_transition.target_entry_id} {expected_question_id}",
                )
        return True, None

    return True, None


def find_escalation_route(entry: dict[str, Any] | None) -> str:
    routing = entry.get("routing") if entry else None
    if isinstance(routing, list):
        for route in routing:
            if isinstance(route, str) and "Escalate to PBSG Staff" in route:
                match = re.match(r"(Route [A-Z](?: \([^)]*\))?)", route)
                if match:
                    return match.group(1)
    return "Escalate to PBSG Staff"


def safe_escalation_response(content: str | None, entries: dict[str, dict[str, Any]], reason: str) -> str:
    # Keep the validator reason out of intern-facing output; it may contain raw internal question ids.
    del reason
    selected_entry_id = extract_selected_entry_id(content)
    entry = entries.get(selected_entry_id) if selected_entry_id else None
    route = find_escalation_route(entry)
    source = f" [{selected_entry_id}.json]" if selected_entry_id else ""

    return "\n".join(
        [
            f"**Selected Entry:** {selected_entry_id or 'Unclear'}",
            "",
            "**Routing Recommendation:** "
            f"{route} (safe escalation because the generated transition could not be verified)",
            "",
            "**Tell the applicant:**",
            "",
            '> **"I need to check this with PBSG Staff before going further. Let me take down your details and have PBSG Staff follow up."**',
            "",
            "**Next steps for you (the intern):**",
            f"- Take down the applicant's full name, contact number, email if any, brief matter description, and any urgency or vulnerability factors noted.{source}",
            f"- Email the information to PBSG Staff on the same day. Do not attempt to advise further.{source}",
        ]
    )


class PBSGRoutingEngine:
    def __init__(self, entries: dict[str, dict[str, Any]] | None = None):
        self.entries = entries or load_golden_set_entries()
        self.graph = PBSGWorkflowGraph(self.entries)

    @classmethod
    def from_default_golden_set(cls) -> "PBSGRoutingEngine":
        return cls(load_golden_set_entries())

    def execute_initial_turn(self, latest_user_query: str) -> PBSGDeterministicResult | None:
        if not self.entries:
            return None
        resolution = resolve_initial_topic(self.entries, latest_user_query)
        if not resolution:
            return None
        return self.execute_initial_resolution(latest_user_query, resolution)

    def execute_initial_resolution(
        self,
        latest_user_query: str,
        resolution: PBSGTopicResolution,
        extracted_facts: list[PBSGTriageFact] | None = None,
    ) -> PBSGDeterministicResult | None:
        initial_facts = extract_user_fact_ledger(self.entries, [], latest_user_query)
        if extracted_facts:
            initial_facts = synthesize_means_status_facts(self.entries, merge_fact_ledger([*initial_facts, *extracted_facts]))
        seed_state = PBSGTriageState(
            mode="FAST_ROUTING",
            workflow_id=resolution.entry_id,
            workflow_locked=True,
            active_workflow=resolution.entry_id,
            queued_workflows=[topic.entry_id for topic in resolution.queued_topics],
            current_question_id="Q1",
            pending_entry_id=resolution.entry_id,
            concurrent_monitors=concurrent_monitors_from_flags(monitor_flags(latest_user_query)),
            triggered_overlays=resolution.overlays,
            fact_ledger=initial_facts,
            routing_completion_status="in_progress",
        )
        if resolution.entry_id == "GEN3-T02" and CAPITAL_OFFENCE_PATTERN.search(latest_user_query):
            transition = self.graph.transition_for("GEN3-T02", "Q1", "if_yes")
            if not transition:
                return None
            content = self.render_transition(transition)
            if not content:
                return None
            state = PBSGTriageState(
                mode="FAST_ROUTING",
                workflow_id="GEN3-T02",
                workflow_locked=True,
                active_workflow="GEN3-T02",
                current_question_id="Q1",
                pending_entry_id="GEN3-T02",
                latest_answer_classification="YES",
                concurrent_monitors=concurrent_monitors_from_flags(monitor_flags(latest_user_query)),
                triggered_overlays=resolution.overlays,
                queued_workflows=[topic.entry_id for topic in resolution.queued_topics],
            )
            self.apply_transition_to_state(state, transition)
            return PBSGDeterministicResult(content=content, state=state, transition=transition, entries=self.entries)

        transition = PBSGTransition(
            entry_id=resolution.entry_id,
            question_id="START",
            branch_key="initial_topic",
            outcome=resolution.reason,
            transition_type="proceed_question",
            target_entry_id=resolution.entry_id,
            target_question_id="Q1",
        )
        transition = self.advance_transition_through_known_facts(seed_state, transition)
        content = self.render_initial_question(resolution)
        if transition.question_id != "START" or transition.target_question_id != "Q1" or transition.transition_type != "proceed_question":
            content = self.render_queued_workflow_start(transition, resolution.entry_id)
        if not content:
            return None
        self.apply_transition_to_state(seed_state, transition)
        return PBSGDeterministicResult(content=content, state=seed_state, transition=transition, entries=self.entries)

    def execute_locked_turn(
        self,
        messages: list[ChatCompletionMessageParam],
        latest_user_query: str,
        branch_key_override: str | None = None,
    ) -> PBSGDeterministicResult | None:
        if not self.entries:
            return None
        state = build_triage_state(messages, self.entries, latest_user_query)
        if state.mode != "FAST_ROUTING":
            return None
        if state.pending_entry_id == "GEN3-T13":
            transition = resolve_gen3_t13_cue_transition(latest_user_query, state.current_question_id)
            if transition:
                content = self.render_transition(transition)
                if not content:
                    return None
                return PBSGDeterministicResult(content=content, state=state, transition=transition, entries=self.entries)
        transition = self.resolve_transition_from_branch(state, branch_key_override)
        if not transition:
            transition = resolve_expected_transition(self.entries, state, latest_user_query)
        if not transition:
            transition = self.resolve_fact_transition(state, state.pending_entry_id, state.current_question_id)
        if not transition:
            return None
        transition = self.resume_parent_after_nested_urgent(state, transition)
        transition = self.resolve_handoff_carryover(state, transition)
        transition = self.advance_transition_through_known_facts(state, transition)
        content = self.render_transition(transition)
        if not content:
            return None
        self.apply_transition_to_state(state, transition)
        return PBSGDeterministicResult(content=content, state=state, transition=transition, entries=self.entries)

    def resolve_fact_transition(
        self,
        state: PBSGTriageState,
        entry_id: str | None,
        question_id: str | None,
    ) -> PBSGTransition | None:
        if not entry_id or not question_id:
            return None
        entry = self.entries.get(entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict):
            return None
        means_fact = evaluate_means_test_structured(question_node, state.fact_ledger)
        fact = user_fact_for_question(self.entries, state.fact_ledger, entry_id, question_id)
        fact = means_fact or fact
        if not fact or fact.confidence < 0.7:
            return None
        branch_key = branch_value_for_fact(question_node, fact)
        if not branch_key or branch_key not in question_node:
            return None
        transition = self.resolve_transition_from_branch(
            PBSGTriageState(
                mode="FAST_ROUTING",
                workflow_id=entry_id,
                workflow_locked=True,
                active_workflow=entry_id,
                pending_entry_id=entry_id,
                current_question_id=question_id,
                fact_ledger=state.fact_ledger,
            ),
            branch_key,
        )
        if not transition:
            return None
        return PBSGTransition(
            entry_id=transition.entry_id,
            question_id=transition.question_id,
            branch_key=transition.branch_key,
            outcome=f"Carried over from {fact.source}: {fact.value}. {transition.outcome}",
            transition_type=transition.transition_type,
            target_entry_id=transition.target_entry_id,
            target_question_id=transition.target_question_id,
            route_label=transition.route_label,
            nested_entry_id=transition.nested_entry_id,
            resume_entry_id=transition.resume_entry_id,
            resume_question_id=transition.resume_question_id,
            clarification_text=transition.clarification_text,
        )

    def advance_transition_through_known_facts(
        self,
        state: PBSGTriageState,
        transition: PBSGTransition,
    ) -> PBSGTransition:
        current = transition
        visited: set[tuple[str | None, str | None]] = set()
        concurrent_origin: PBSGTransition | None = None
        while current.transition_type in {"proceed_question", "concurrent_route_question", "handoff_entry", "cross_reference"}:
            if current.transition_type == "concurrent_route_question" and current.route_label and not concurrent_origin:
                concurrent_origin = current
            target_entry_id = current.target_entry_id
            target_question_id = current.target_question_id or ("Q1" if current.transition_type in {"handoff_entry", "cross_reference"} else None)
            visit_key = (target_entry_id, target_question_id)
            if visit_key in visited:
                break
            visited.add(visit_key)
            fact_transition = self.resolve_fact_transition(state, target_entry_id, target_question_id)
            if not fact_transition:
                break
            self.record_transition_fact(state, fact_transition)
            current = self.resume_parent_after_nested_urgent(state, fact_transition)
            if current.transition_type in {"terminal_route", "nested_stream", "clarification"}:
                break
        if (
            concurrent_origin
            and current.transition_type == "proceed_question"
            and current.target_entry_id
            and current.target_question_id
        ):
            return PBSGTransition(
                entry_id=concurrent_origin.entry_id,
                question_id=concurrent_origin.question_id,
                branch_key=concurrent_origin.branch_key,
                outcome=concurrent_origin.outcome,
                transition_type="concurrent_route_question",
                target_entry_id=current.target_entry_id,
                target_question_id=current.target_question_id,
                route_label=concurrent_origin.route_label,
                resume_entry_id=concurrent_origin.resume_entry_id,
                resume_question_id=concurrent_origin.resume_question_id,
            )
        return current

    def start_queued_workflow(
        self,
        messages: list[ChatCompletionMessageParam],
        workflow_id: str,
    ) -> PBSGDeterministicResult | None:
        if workflow_id not in self.entries:
            return None
        state = build_triage_state(messages, self.entries, "")
        if workflow_id not in state.queued_workflows:
            return None
        state.active_workflow = workflow_id
        state.workflow_id = workflow_id
        state.workflow_locked = True
        state.pending_entry_id = workflow_id
        state.current_question_id = "Q1"
        state.queued_workflows = [entry_id for entry_id in state.queued_workflows if entry_id != workflow_id]
        state.routing_completion_status = "in_progress"

        transition = PBSGTransition(
            entry_id=workflow_id,
            question_id="START",
            branch_key="continue_queued_workflow",
            outcome="Continue queued workflow",
            transition_type="proceed_question",
            target_entry_id=workflow_id,
            target_question_id="Q1",
        )
        transition = self.advance_transition_through_known_facts(state, transition)

        content = self.render_queued_workflow_start(transition, workflow_id)
        if not content:
            return None
        self.apply_transition_to_state(state, transition)
        return PBSGDeterministicResult(content=content, state=state, transition=transition, entries=self.entries)

    def render_queued_workflow_start(self, transition: PBSGTransition, workflow_id: str) -> str | None:
        if transition.transition_type == "proceed_question" and transition.target_entry_id and transition.target_question_id:
            question = self.question_text(transition.target_entry_id, transition.target_question_id)
            if not question:
                return None
            is_queued_continuation = transition.branch_key == "continue_queued_workflow"
            lines = [
                f"**Selected Entry:** {workflow_id}",
                "",
                "Triage progress:",
                "",
                *([f"- Activated queued workflow: {workflow_id}"] if is_queued_continuation else []),
                *(
                    [f"- Carried over: {transition.question_id} = {label_from_branch_key(transition.branch_key)} → {transition.outcome} [{transition.entry_id}.json]"]
                    if transition.question_id != "START"
                    else []
                ),
                f"- Next question: {transition.target_question_id} from {transition.target_entry_id}",
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **{transition.target_question_id}: "{convert_question_to_second_person(question)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{transition.target_entry_id}.json]",
            ]
            return "\n".join(lines)
        return self.render_transition(transition)

    def resolve_handoff_carryover(
        self,
        state: PBSGTriageState,
        transition: PBSGTransition,
    ) -> PBSGTransition:
        if transition.transition_type not in {"handoff_entry", "cross_reference"} or not transition.target_entry_id:
            return transition
        target_question_id = "Q1"
        carried_fact = user_fact_for_question(self.entries, state.fact_ledger, transition.target_entry_id, target_question_id)
        if not carried_fact:
            return transition
        target_entry = self.entries.get(transition.target_entry_id)
        branching_logic = target_entry.get("branching_logic") if target_entry else None
        question_node = branching_logic.get(target_question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict):
            return transition
        carried_branch_key = branch_key_for_answer(question_node, carried_fact.value)
        if not carried_branch_key or carried_branch_key not in question_node:
            return transition
        carried_transition = self.graph.transition_for(transition.target_entry_id, target_question_id, carried_branch_key)
        if not carried_transition:
            outcome = question_node.get(carried_branch_key)
            if not isinstance(outcome, str):
                return transition
            carried_transition = parse_transition_outcome(
                self.entries,
                transition.target_entry_id,
                target_question_id,
                carried_branch_key,
                outcome,
            )
        return PBSGTransition(
            entry_id=carried_transition.entry_id,
            question_id=carried_transition.question_id,
            branch_key=carried_transition.branch_key,
            outcome=f"Carried over from {carried_fact.source}: {carried_fact.value}. {carried_transition.outcome}",
            transition_type=carried_transition.transition_type,
            target_entry_id=carried_transition.target_entry_id,
            target_question_id=carried_transition.target_question_id,
            route_label=carried_transition.route_label,
            nested_entry_id=carried_transition.nested_entry_id,
            resume_entry_id=carried_transition.resume_entry_id,
            resume_question_id=carried_transition.resume_question_id,
            clarification_text=carried_transition.clarification_text,
        )

    def record_transition_fact(self, state: PBSGTriageState, transition: PBSGTransition) -> None:
        question = question_text_from_entry(self.entries, transition.entry_id, transition.question_id)
        fact_key = canonical_fact_key_for_question(question)
        if not fact_key:
            return
        value = label_from_branch_key(transition.branch_key)
        fact = PBSGTriageFact(
            fact_key=fact_key,
            value=value,
            normalized_value=normalize_branch_label(value),
            source=f"{transition.entry_id}.{transition.question_id}",
            scope="workflow" if fact_key.startswith("workflow.question.") else "global",
            confidence=1.0,
            provenance=f"{transition.entry_id} {transition.question_id} = {value}",
            source_type="deterministic_transition",
            branch_value=transition.branch_key,
            source_text=f"{transition.entry_id} {transition.question_id} = {value}",
            workflow_scope="workflow" if fact_key.startswith("workflow.question.") else "global",
        )
        state.fact_ledger = [
            existing
            for existing in state.fact_ledger
            if not (existing.fact_key == fact.fact_key and existing.source == fact.source)
        ]
        state.fact_ledger.append(fact)

    def apply_transition_to_state(self, state: PBSGTriageState, transition: PBSGTransition) -> None:
        self.record_transition_fact(state, transition)
        state.latest_answer_classification = label_from_branch_key(transition.branch_key).upper()
        state.repair_required = False
        state.active_side_enquiry = None
        state.interruption_stack = []
        if transition.transition_type in {"proceed_question", "concurrent_route_question"}:
            state.active_workflow = transition.target_entry_id
            state.pending_entry_id = transition.target_entry_id
            state.current_question_id = transition.target_question_id
            state.routing_completion_status = "in_progress"
        elif transition.transition_type in {"handoff_entry", "cross_reference"}:
            state.active_workflow = transition.target_entry_id
            state.pending_entry_id = transition.target_entry_id
            state.current_question_id = "Q1"
            state.workflow_id = transition.target_entry_id
            state.routing_completion_status = "in_progress"
        elif transition.transition_type == "nested_stream":
            state.active_workflow = transition.target_entry_id
            state.pending_entry_id = transition.target_entry_id
            state.current_question_id = transition.target_question_id
            state.parent_workflow = transition.resume_entry_id or transition.entry_id
            state.resume_question_id = transition.resume_question_id
            state.routing_completion_status = "suspended"
            if transition.entry_id not in state.suspended_workflows:
                state.suspended_workflows.append(transition.entry_id)
        elif transition.transition_type == "terminal_route":
            if transition.entry_id not in state.completed_workflows:
                state.completed_workflows.append(transition.entry_id)
            state.current_question_id = None
            state.pending_entry_id = None
            state.routing_completion_status = (
                "awaiting_topic_resolution" if state.queued_workflows else "completed"
            )
        state.unanswered_required_fields = unanswered_required_fields_for_state(
            self.entries, state.pending_entry_id, state.current_question_id, state.fact_ledger
        )

    def resolve_transition_from_branch(
        self,
        state: PBSGTriageState,
        branch_key: str | None,
    ) -> PBSGTransition | None:
        if not branch_key or not state.pending_entry_id or not state.current_question_id:
            return None
        entry = self.entries.get(state.pending_entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        question_node = branching_logic.get(state.current_question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict) or branch_key not in question_node:
            return None
        outcome = question_node.get(branch_key)
        if not isinstance(outcome, str):
            return None
        transition = self.graph.transition_for(state.pending_entry_id, state.current_question_id, branch_key)
        if not transition:
            transition = parse_transition_outcome(
                self.entries,
                state.pending_entry_id,
                state.current_question_id,
                branch_key,
                outcome,
            )
        return self.resume_parent_after_nested_urgent(state, transition)

    def resume_parent_after_nested_urgent(
        self,
        state: PBSGTriageState,
        transition: PBSGTransition,
    ) -> PBSGTransition:
        parent_resume = {"GEN3-T02": "Q3", "GEN3-T03": "Q2"}
        if state.pending_entry_id != "GEN3-T06" or state.workflow_id not in parent_resume:
            return transition
        if transition.transition_type not in {"terminal_route", "handoff_entry"} or not transition.route_label:
            return transition
        return PBSGTransition(
            entry_id=transition.entry_id,
            question_id=transition.question_id,
            branch_key=transition.branch_key,
            outcome=transition.outcome,
            transition_type="concurrent_route_question",
            target_entry_id=state.workflow_id,
            target_question_id=parent_resume[state.workflow_id],
            route_label=transition.route_label,
            resume_entry_id=state.workflow_id,
            resume_question_id=parent_resume[state.workflow_id],
        )

    def render_transition(self, transition: PBSGTransition) -> str | None:
        if transition.transition_type == "nested_stream":
            return self.render_nested_stream_transition(transition)
        if transition.transition_type in {"proceed_question", "concurrent_route_question"}:
            return self.render_question_transition(transition)
        if transition.transition_type in {"handoff_entry", "cross_reference"}:
            return self.render_handoff_transition(transition)
        if transition.transition_type == "terminal_route":
            return self.render_terminal_route(transition)
        if transition.transition_type == "clarification":
            return self.render_clarification(transition)
        return None

    def render_initial_question(self, resolution: PBSGTopicResolution) -> str | None:
        question = self.question_text(resolution.entry_id, "Q1")
        if not question:
            return None
        active_entry = self.entries.get(resolution.entry_id, {})
        active_topic = active_entry.get("topic") if isinstance(active_entry, dict) else None
        lines = [
            f"**Selected Entry:** {resolution.entry_id}",
            "",
            "Triage progress:",
            "",
            f"- Topic resolved: {resolution.reason} [{resolution.entry_id}.json]",
            f"- Next question: Q1 from {resolution.entry_id}",
        ]
        if resolution.queued_topics:
            lines.extend(
                [
                    "",
                    "Topics identified:",
                    f"1. {resolution.entry_id}"
                    + (f" ({active_topic})" if isinstance(active_topic, str) and active_topic else "")
                    + " — active workflow",
                ]
            )
            for index, topic in enumerate(resolution.queued_topics, start=2):
                entry = self.entries.get(topic.entry_id, {})
                entry_topic = entry.get("topic") if isinstance(entry, dict) else None
                topic_label = f"{topic.entry_id}"
                if isinstance(entry_topic, str) and entry_topic:
                    topic_label += f" ({entry_topic})"
                lines.append(f"{index}. {topic_label} — queued workflow (noted from: {topic.evidence})")
        if resolution.overlays:
            lines.extend(
                [
                    "",
                    "**Concurrent monitors:**",
                    "",
                    *[f"- {overlay} noted as a monitor, not the active workflow [{overlay}.json]" for overlay in resolution.overlays],
                ]
            )
        lines.extend(
            [
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **Q1: "{convert_question_to_second_person(question)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{resolution.entry_id}.json]",
            ]
        )
        return "\n".join(lines)

    def render_nested_stream_transition(self, transition: PBSGTransition) -> str | None:
        if not transition.target_entry_id or not transition.target_question_id:
            return None
        question = self.question_text(transition.target_entry_id, transition.target_question_id)
        if not question:
            return None

        entry = self.entries.get(transition.entry_id)
        structured_route = structured_route_from_entry(entry, transition.route_label)
        applicant_script = structured_route.script if structured_route else self.nested_stream_bridge_script(transition)
        if re.search(r"\bProceed to\s+GEN3-|following which|triage outcome", applicant_script, flags=re.IGNORECASE):
            applicant_script = self.nested_stream_bridge_script(transition)
        resume_label = (
            f"{transition.resume_entry_id} {transition.resume_question_id}"
            if transition.resume_entry_id and transition.resume_question_id
            else "the parent stream"
        )
        active_stream_label = (
            f"{transition.target_entry_id} urgent concurrent path"
            if transition.target_entry_id == "GEN3-T06"
            else f"{transition.target_entry_id} nested path"
        )

        lines = self.render_header_and_answered_state(transition.entry_id, transition)
        lines.extend(
            [
                "",
                f"**Active stream:** {active_stream_label}",
                "",
                "**Why this stream is triggered:**",
                "",
                f"- {transition.question_id}: {label_from_branch_key(transition.branch_key)} → {transition.outcome} [{transition.entry_id}.json]",
            ]
        )
        lines.extend(
            [
                "",
                "**Tell the applicant:**",
                "",
                f'"{applicant_script}"',
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **{transition.target_entry_id} {transition.target_question_id}: "{convert_question_to_second_person(question)}"**',
            ]
        )
        lines.extend(
            [
                "",
                "Triage progress:",
                "",
                f"- Last answered: {transition.entry_id} {transition.question_id} = {label_from_branch_key(transition.branch_key)} [{transition.entry_id}.json]",
                f"- Now checking: {transition.target_entry_id} {transition.target_question_id}",
                f"- After this urgent path: resume {resume_label}",
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{transition.target_entry_id}.json]",
            ]
        )
        return "\n".join(lines)

    def nested_stream_bridge_script(self, transition: PBSGTransition) -> str:
        if transition.target_entry_id == "GEN3-T06":
            if transition.entry_id == "GEN3-T03":
                return "Your family matter may also involve urgency, such as violence or a deadline. I need to check the urgent issue first, then I will continue the family legal aid questions after that."
            if transition.entry_id == "GEN3-T02":
                return "Your criminal matter may also have an urgent deadline or safety concern. I need to check the urgent issue first, then I will continue the criminal legal aid questions after that."
            return "I need to check the urgent issue first, then I will continue the main triage questions after that."
        return "I need to check this related pathway first, then I will continue the main triage questions after that."

    def render_question_transition(self, transition: PBSGTransition) -> str | None:
        if not transition.target_entry_id or not transition.target_question_id:
            return None
        question = self.question_text(transition.target_entry_id, transition.target_question_id)
        if not question:
            return None

        selected_entry_id = transition.entry_id
        next_label = f"{transition.target_question_id} from {transition.target_entry_id}"
        question_prefix = transition.target_question_id
        if transition.transition_type == "nested_stream":
            next_label = f"{transition.target_entry_id} {transition.target_question_id} (Urgent concurrent path)"
            question_prefix = f"{transition.target_entry_id} {transition.target_question_id}"
        elif transition.transition_type == "concurrent_route_question" and transition.target_entry_id != transition.entry_id:
            question_prefix = f"{transition.target_entry_id} {transition.target_question_id}"

        lines = self.render_header_and_answered_state(selected_entry_id, transition)
        if transition.transition_type == "concurrent_route_question" and transition.route_label:
            if transition.resume_entry_id:
                route_note = (
                    f"{transition.route_label}: {transition.outcome}. "
                    f"Because this urgent stream is nested under {transition.resume_entry_id}, resume the parent stream at "
                    f"{transition.target_entry_id} {transition.target_question_id}."
                )
            else:
                route_text = find_route_text(self.entries.get(transition.entry_id), transition.route_label)
                route_note = route_text or transition.outcome
            lines.extend(["", "**Concurrent routing note:**", "", f"- {route_note} [{transition.entry_id}.json]"])
        lines.extend(
            [
                "",
                "Triage progress:",
                "",
                f"- Last answered: {transition.question_id} = {label_from_branch_key(transition.branch_key)} → {transition.outcome} [{transition.entry_id}.json]",
                f"- Next question: {next_label}",
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **{question_prefix}: "{convert_question_to_second_person(question)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{transition.target_entry_id}.json]",
            ]
        )
        return "\n".join(lines)

    def render_handoff_transition(self, transition: PBSGTransition) -> str | None:
        if not transition.target_entry_id:
            return None
        question = self.question_text(transition.target_entry_id, "Q1")
        if not question:
            return None
        lines = self.render_header_and_answered_state(transition.target_entry_id, transition)
        lines.extend(
            [
                "",
                "Triage progress:",
                "",
                f"- Last answered: {transition.question_id} = {label_from_branch_key(transition.branch_key)} → {transition.outcome} [{transition.entry_id}.json]",
                f"- Handoff: {transition.entry_id} → {transition.target_entry_id}",
                f"- Next question: Q1 from {transition.target_entry_id}",
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **Q1: "{convert_question_to_second_person(question)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{transition.target_entry_id}.json]",
            ]
        )
        return "\n".join(lines)

    def render_terminal_route(self, transition: PBSGTransition) -> str | None:
        entry = self.entries.get(transition.entry_id)
        route_text = find_route_text(entry, transition.route_label)
        if not transition.route_label or not route_text:
            return None
        structured_route = structure_route(route_text, entry, transition.route_label)
        lines = self.render_header_and_answered_state(transition.entry_id, transition)
        lines.extend(
            [
                "",
                "Triage progress:",
                "",
                f"- Last answered: {transition.question_id} = {label_from_branch_key(transition.branch_key)} → {transition.outcome} [{transition.entry_id}.json]",
                "- Next step: final routing recommendation",
                "",
                f"**Routing Recommendation:** {structured_route.route_label}"
                + (f" ({structured_route.route_name})" if structured_route.route_name else ""),
                "",
                "**Why this route applies:**",
                "",
                f"- {transition.question_id}: {label_from_branch_key(transition.branch_key)} [{transition.entry_id}.json]",
                "",
                "**Tell the applicant:**",
                "",
                f'> **"{structured_route.script}"**',
            ]
        )
        self.extend_route_section(lines, "What the applicant needs to know:", structured_route.needs_to_know, transition.entry_id)
        self.extend_route_section(lines, "How to access this route:", structured_route.access, transition.entry_id)
        self.extend_route_section(lines, "What the applicant should prepare:", structured_route.prepare, transition.entry_id)
        self.extend_route_section(lines, "Next steps for you (the intern):", structured_route.intern_steps, transition.entry_id)
        self.extend_route_section(lines, "Important caveat:", structured_route.caveats, transition.entry_id)
        return "\n".join(lines)

    def extend_route_section(self, lines: list[str], title: str, items: list[str], entry_id: str) -> None:
        if not items:
            return
        lines.extend(["", f"**{title}**", ""])
        lines.extend(f"- {item} [{entry_id}.json]" for item in items)

    def render_clarification(self, transition: PBSGTransition) -> str | None:
        clarification = transition.clarification_text or first_quoted_text(transition.outcome)
        if not clarification:
            return None
        lines = self.render_header_and_answered_state(transition.entry_id, transition)
        lines.extend(
            [
                "",
                "Triage progress:",
                "",
                f"- Last answered: {transition.question_id} = {label_from_branch_key(transition.branch_key)} → clarification needed [{transition.entry_id}.json]",
                f"- Current question remains: {transition.question_id} from {transition.entry_id}",
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **{transition.question_id}: "{convert_question_to_second_person(clarification)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{transition.entry_id}.json]",
            ]
        )
        return "\n".join(lines)

    def render_header_and_answered_state(self, selected_entry_id: str, transition: PBSGTransition) -> list[str]:
        return [
            f"**Selected Entry:** {selected_entry_id}",
            "",
            "What I gathered from your description:",
            "",
            f"- {transition.question_id}: {label_from_branch_key(transition.branch_key)} [{transition.entry_id}.json]",
        ]

    def question_text(self, entry_id: str, question_id: str) -> str | None:
        entry = self.entries.get(entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
        question = question_node.get("question") if isinstance(question_node, dict) else None
        return question if isinstance(question, str) else None
