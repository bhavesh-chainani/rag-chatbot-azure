import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessageParam


@dataclass
class PBSGTriageQuestionTarget:
    entry_id: str | None
    question_id: str


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
class PBSGTopicResolution:
    entry_id: str
    confidence: float
    reason: str
    overlays: list[str] = field(default_factory=list)


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


def extract_completed_workflows(content: str | None) -> list[str]:
    if not content:
        return []
    completed: list[str] = []
    for match in re.findall(r"\*\*✓\s*(GEN3-[A-Z0-9-]+)\s+routed\.\*\*", content, flags=re.IGNORECASE):
        workflow_id = match.upper()
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

    keyword_rules = [
        (r"\b(foreigner|not singapore citizen|not a citizen|not pr|not a pr)\b", "if_no_foreigner"),
        (r"\b(representation|represent|lawyer to act|lawyer)\b", "if_representation"),
        (r"\b(guidance|initial advice|advice|consultation)\b", "if_guidance"),
        (r"\b(nonprofit|non profit|charity|social enterprise)\b", "if_yes_and_nonprofit"),
        (r"\b(for profit|company|business|commercial)\b", "if_yes_and_for_profit"),
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
    return {
        "urgent": bool(URGENT_PATTERN.search(text)),
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
    if flags.get("urgent") and primary_entry_id != "GEN3-T06" and "GEN3-T06" in entries:
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


def resolve_initial_topic(
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
        return PBSGTopicResolution(
            entry_id="GEN3-T02",
            confidence=1.0,
            reason="capital offence signal",
            overlays=initial_topic_overlays(normalized, "GEN3-T02", entries),
        )

    scores: dict[str, float] = {}
    evidence: dict[str, str] = {}
    for entry_id, pattern in INITIAL_TOPIC_PATTERNS.items():
        if entry_id not in entries:
            continue
        match = pattern.search(normalized)
        score = metadata_overlap_score(normalized, entries[entry_id])
        if match:
            score += 0.65
            evidence[entry_id] = match.group(0)
        if entry_id == "GEN3-T13" and match and not GEN3_T13_PRIMARY_PATTERN.search(normalized):
            score -= 0.45
        if entry_id == "GEN3-T06" and match and any(
            candidate in scores or INITIAL_TOPIC_PATTERNS[candidate].search(normalized)
            for candidate in ("GEN3-T02", "GEN3-T03", "GEN3-T04")
            if candidate in INITIAL_TOPIC_PATTERNS
        ):
            score -= 0.35
        if score > 0:
            scores[entry_id] = score

    if not scores:
        return PBSGTopicResolution(
            entry_id=fallback_entry_id,
            confidence=0.55,
            reason="defaulted to first-contact triage because no Golden Set topic matched confidently",
            overlays=initial_topic_overlays(normalized, fallback_entry_id, entries),
        )

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_entry_id, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < 0.6 or (second_score >= 0.6 and best_score - second_score < 0.2):
        return PBSGTopicResolution(
            entry_id=fallback_entry_id,
            confidence=0.55,
            reason="defaulted to first-contact triage because topic signals were weak or ambiguous",
            overlays=initial_topic_overlays(normalized, fallback_entry_id, entries),
        )

    return PBSGTopicResolution(
        entry_id=best_entry_id,
        confidence=min(best_score, 1.0),
        reason=evidence.get(best_entry_id, "metadata match"),
        overlays=initial_topic_overlays(normalized, best_entry_id, entries),
    )


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
    has_safety = bool(SAFETY_INTERRUPT_PATTERN.search(latest_user_query))

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
    if candidates and (has_additive or branch_key):
        return PBSGTurnClassification(
            turn_type="answer_plus_new_topic" if branch_key else "new_topic_only",
            should_call_llm=True,
            reason="possible new topic in locked flow",
            pending_branch_key=branch_key,
            new_topics=candidates,
        )
    if candidates:
        return PBSGTurnClassification(
            turn_type="new_topic_only",
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
    topic_workflows = extract_topic_workflows(assistant_content)
    completed_workflows = extract_completed_workflows(assistant_content)
    active_workflow = pending_entry_id or selected_entry_id
    answered_lines = extract_answered_lines(assistant_content)
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
    resume_question_id = {"GEN3-T02": "Q3", "GEN3-T03": "Q2"}.get(parent_workflow or "")

    return PBSGTriageState(
        mode=mode,
        workflow_id=selected_entry_id,
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
        if resolution.entry_id == "GEN3-T02" and CAPITAL_OFFENCE_PATTERN.search(latest_user_query):
            transition = self.graph.transition_for("GEN3-T02", "Q1", "if_yes")
            if not transition:
                return None
            content = self.render_transition(transition)
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
            )
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
        content = self.render_initial_question(resolution)
        if not content:
            return None
        state = PBSGTriageState(
            mode="FAST_ROUTING",
            workflow_id=resolution.entry_id,
            workflow_locked=True,
            active_workflow=resolution.entry_id,
            current_question_id="Q1",
            pending_entry_id=resolution.entry_id,
            concurrent_monitors=concurrent_monitors_from_flags(monitor_flags(latest_user_query)),
            triggered_overlays=resolution.overlays,
        )
        return PBSGDeterministicResult(content=content, state=state, transition=transition, entries=self.entries)

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
            return None
        transition = self.resume_parent_after_nested_urgent(state, transition)
        content = self.render_transition(transition)
        if not content:
            return None
        return PBSGDeterministicResult(content=content, state=state, transition=transition, entries=self.entries)

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
        lines = [
            f"**Selected Entry:** {resolution.entry_id}",
            "",
            "Triage progress:",
            "",
            f"- Topic resolved: {resolution.reason} [{resolution.entry_id}.json]",
            f"- Next question: Q1 from {resolution.entry_id}",
        ]
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

        lines = self.render_header_and_answered_state(selected_entry_id, transition)
        if transition.transition_type == "concurrent_route_question" and transition.route_label:
            route_text = find_route_text(self.entries.get(transition.entry_id), transition.route_label)
            lines.extend(["", "**Concurrent routing note:**", "", f"- {route_text or transition.outcome} [{transition.entry_id}.json]"])
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
