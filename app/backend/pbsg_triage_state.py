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
    workflow_id: str | None = None
    workflow_locked: bool = False
    current_question_id: str | None = None
    pending_entry_id: str | None = None
    previous_states: list[str] = field(default_factory=list)
    answered_lines: list[str] = field(default_factory=list)
    allowed_branch_keys: list[str] = field(default_factory=list)
    active_monitors: dict[str, bool] = field(
        default_factory=lambda: {"urgent": False, "vulnerable": False, "represented": False}
    )
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


def allowed_branch_keys(entries: dict[str, dict[str, Any]], entry_id: str | None, question_id: str | None) -> list[str]:
    if not entry_id or not question_id:
        return []
    entry = entries.get(entry_id)
    branching_logic = entry.get("branching_logic") if entry else None
    question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
    if not isinstance(question_node, dict):
        return []
    return [key for key in question_node if key.startswith("if_")]


def monitor_flags(text: str) -> dict[str, bool]:
    return {
        "urgent": bool(URGENT_PATTERN.search(text)),
        "vulnerable": bool(VULNERABLE_PATTERN.search(text)),
        "represented": bool(REPRESENTED_PATTERN.search(text)),
    }


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

    return PBSGTriageState(
        workflow_id=selected_entry_id,
        workflow_locked=bool(selected_entry_id and selected_entry_id in entries),
        current_question_id=current_question_id,
        pending_entry_id=pending_entry_id,
        previous_states=extract_answered_lines(assistant_content),
        answered_lines=extract_answered_lines(assistant_content),
        allowed_branch_keys=allowed_branch_keys(entries, pending_entry_id, current_question_id),
        active_monitors=monitor_flags(conversation_text),
    )


def format_state_prompt(state: PBSGTriageState) -> str:
    if not state.workflow_locked or not state.workflow_id:
        return ""

    lines = [
        "",
        "DETERMINISTIC ROUTING STATE (internal; do not expose as JSON)",
        f"- Workflow locked: {state.workflow_id}. Do not re-run workflow identification unless a branch explicitly hands off, a monitor requires escalation, or the intern introduces materially new facts.",
    ]
    if state.pending_entry_id and state.current_question_id:
        lines.append(f"- Pending question: {state.pending_entry_id} {state.current_question_id}. Treat the user's latest message as the answer to this question.")
    if state.allowed_branch_keys:
        lines.append(f"- Allowed branch keys for the pending question: {', '.join(state.allowed_branch_keys)}.")
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
            "**Part C — Routing recommendation:** "
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
