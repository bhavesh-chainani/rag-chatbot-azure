import re
from dataclasses import dataclass, field
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
