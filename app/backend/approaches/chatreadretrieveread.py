import hashlib
import json
import re
import time
from collections.abc import AsyncGenerator, Awaitable
from dataclasses import asdict
from typing import Any, Optional, cast

from azure.search.documents.aio import SearchClient
from azure.search.documents.knowledgebases.aio import KnowledgeBaseRetrievalClient
from azure.search.documents.models import VectorQuery
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
)
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage

from approaches.approach import (
    Approach,
    DataPoints,
    ExtraInfo,
    QuickReply,
    QuickReplyOption,
    ThoughtStep,
)
from approaches.promptmanager import PromptManager
from pbsg_triage_state import (
    PBSGDeterministicResult,
    PBSGInterruption,
    PBSGQueuedTopic,
    PBSGRoutingEngine,
    PBSGTriageFact,
    PBSGTopicResolution,
    PBSGTransition,
    PBSGTurnClassification,
    build_triage_state,
    canonical_fact_key_for_node,
    classify_turn_interrupt,
    collapse_duplicate_route_cards,
    convert_question_to_second_person,
    format_state_prompt,
    is_general_enquiry_interrupt,
    label_from_branch_key,
    load_golden_set_entries,
    next_question_lines,
    normalize_branch_label,
    parse_transition_outcome,
    parse_pbsg_state_marker,
    pbsg_state_marker,
    question_text_from_entry,
    selected_stream_lines,
    short_question_label,
    stream_display_name,
    user_fact_for_question,
    resolve_expected_transition,
    resolve_initial_topic,
    safe_escalation_response,
    validate_response_transition,
    validate_response_questions,
)


def build_memory_pack(triage_state: Any) -> dict[str, Any]:
    topic_threads = getattr(triage_state, "topic_threads", []) or []
    return {
        "active_thread_id": getattr(triage_state, "active_thread_id", None),
        "referenced_thread_id": getattr(triage_state, "referenced_thread_id", None),
        "routing_completion_status": getattr(triage_state, "routing_completion_status", None),
        "session_summary": getattr(triage_state, "session_summary", ""),
        "already_resolved": getattr(triage_state, "already_resolved", False),
        "should_recap": getattr(triage_state, "should_recap", False),
        "resume_hint": getattr(triage_state, "resume_hint", None),
        "memory_hash": getattr(triage_state, "memory_hash", None),
        "thread_summaries": [
            {
                "thread_id": thread.thread_id,
                "entry_id": thread.entry_id,
                "status": thread.status,
                "summary": thread.summary,
                "last_pending_question": thread.last_pending_question,
                "terminal_route": thread.terminal_route,
                "answered_question_ids": list(thread.answered_question_ids),
                "aliases": list(thread.aliases),
            }
            for thread in topic_threads
        ],
    }


def extract_session_memory(session_state: Any) -> dict[str, Any] | None:
    if isinstance(session_state, dict):
        memory = session_state.get("pbsg_session_memory")
        if isinstance(memory, dict):
            return memory
    return None


def extend_session_state_with_memory(session_state: Any, triage_state: Any) -> Any:
    memory_pack = build_memory_pack(triage_state)
    if isinstance(session_state, dict):
        updated = dict(session_state)
        updated["pbsg_session_memory"] = memory_pack
        return updated
    return session_state


def hydrate_triage_state_from_session_memory(triage_state: Any, session_state: Any) -> Any:
    previous_memory = extract_session_memory(session_state)
    if previous_memory:
        triage_state.memory_origin = "messages+session"
        triage_state.memory_pack = {**previous_memory, **(getattr(triage_state, "memory_pack", {}) or {})}
    else:
        triage_state.memory_pack = build_memory_pack(triage_state)
    return triage_state


def context_with_triage_memory(context: dict[str, Any], triage_state: Any) -> dict[str, Any]:
    context["pbsg_memory_pack"] = build_memory_pack(triage_state)
    if getattr(triage_state, "session_summary", ""):
        context["pbsg_session_summary"] = triage_state.session_summary
    referenced_thread_id = getattr(triage_state, "referenced_thread_id", None)
    if referenced_thread_id:
        context["pbsg_referenced_thread_id"] = referenced_thread_id
    return context


def prompt_vars_with_memory(system_template_variables: dict[str, Any], triage_state: Any) -> dict[str, Any]:
    memory_pack = build_memory_pack(triage_state)
    return system_template_variables | {
        "pbsg_memory_pack": memory_pack,
        "pbsg_session_summary": memory_pack.get("session_summary", ""),
        "pbsg_active_thread_id": memory_pack.get("active_thread_id"),
        "pbsg_referenced_thread_id": memory_pack.get("referenced_thread_id"),
    }


def rewrite_vars_with_memory(user_query: str, past_messages: list[ChatCompletionMessageParam], triage_state: Any) -> dict[str, Any]:
    memory_pack = build_memory_pack(triage_state)
    return {
        "user_query": user_query,
        "past_messages": past_messages,
        "pbsg_memory_pack": memory_pack,
        "pbsg_session_summary": memory_pack.get("session_summary", ""),
        "pbsg_active_thread_id": memory_pack.get("active_thread_id"),
        "pbsg_referenced_thread_id": memory_pack.get("referenced_thread_id"),
    }


def should_use_resolved_recap(triage_state: Any) -> bool:
    return bool(getattr(triage_state, "already_resolved", False) and getattr(triage_state, "should_recap", False))


def recap_from_triage_state(entries: dict[str, dict[str, Any]], triage_state: Any) -> str | None:
    referenced_thread_id = getattr(triage_state, "referenced_thread_id", None) or getattr(triage_state, "active_thread_id", None)
    if not referenced_thread_id:
        return None
    thread = next(
        (candidate for candidate in getattr(triage_state, "topic_threads", []) if candidate.thread_id == referenced_thread_id),
        None,
    )
    if not thread or thread.status != "completed":
        return None
    stream_name = stream_display_name(entries, thread.entry_id)
    route = thread.terminal_route or getattr(triage_state, "last_resolved_route_by_thread", {}).get(thread.thread_id)
    summary_line = thread.summary or stream_name
    lines = [
        f"**Selected Stream:** {stream_name}",
        "",
        "**Recap:** We already completed this topic earlier.",
        "",
        f"- {summary_line}",
    ]
    if route:
        lines.append(f"- Last resolved route: {route}.")
    if thread.answered_question_ids:
        lines.append(f"- Questions already resolved: {', '.join(thread.answered_question_ids)}.")
    lines.append(
        "- If any material facts changed, tell me what changed and I will reassess. Otherwise, we do not need to restart this stream."
    )
    return "\n".join(lines)


def resolved_topic_recap_response(entries: dict[str, dict[str, Any]], triage_state: Any, session_state: Any = None) -> dict[str, Any] | None:
    content = recap_from_triage_state(entries, triage_state)
    if not content:
        return None
    context = context_with_triage_memory(
        {
            "thoughts": [],
            "data_points": {},
            "followup_questions": None,
            "quick_reply": None,
            "pbsg_triage_state": asdict(triage_state),
        },
        triage_state,
    )
    return {
        "message": {"content": content, "role": "assistant"},
        "context": context,
        "session_state": extend_session_state_with_memory(session_state, triage_state),
    }


def apply_memory_notes(content: str, triage_state: Any) -> str:
    if should_use_resolved_recap(triage_state):
        return f"**Note:** This topic was already resolved earlier, so I am giving a short recap instead of restarting it.\n\n{content}"
    resume_hint = getattr(triage_state, "resume_hint", None)
    referenced_thread_id = getattr(triage_state, "referenced_thread_id", None)
    active_thread_id = getattr(triage_state, "active_thread_id", None)
    if resume_hint and referenced_thread_id and referenced_thread_id != active_thread_id:
        return f"**Note:** {resume_hint}\n\n{content}"
    return content


def turn_requires_pending_question_preservation(turn_classification: PBSGTurnClassification | None) -> bool:
    if not turn_classification:
        return False
    return turn_classification.turn_type in {
        "current_topic_side_question",
        "true_new_topic",
        "return_to_completed_topic",
        "return_to_queued_topic",
        "correction",
    }


def turn_memory_reason(turn_classification: PBSGTurnClassification | None, triage_state: Any) -> str | None:
    if turn_classification and turn_classification.reason:
        return turn_classification.reason
    if getattr(triage_state, "already_resolved", False):
        return "Referenced a previously resolved topic and avoided restarting it."
    resume_hint = getattr(triage_state, "resume_hint", None)
    if resume_hint:
        return resume_hint
    return None


def maybe_resolved_recap(entries: dict[str, dict[str, Any]], triage_state: Any, session_state: Any) -> dict[str, Any] | None:
    if not should_use_resolved_recap(triage_state):
        return None
    return resolved_topic_recap_response(entries, triage_state, session_state)

from prepdocslib.blobmanager import AdlsBlobManager, BlobManager
from prepdocslib.embeddings import ImageEmbeddings

COMMON_QUERY_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
    "you",
}

PBSG_GLOSSARY_ANSWERS = {
    "lasco": "LASCO is the Legal Assistance Scheme for Capital Offences. It provides representation for people charged with capital offences.",
    "clas": "CLAS is the Criminal Legal Aid Scheme. It may provide free or low-cost criminal defence representation for eligible low-income applicants facing non-capital criminal charges.",
    "pdo": "PDO is the Public Defender’s Office, the government criminal legal aid office for eligible low-income Singapore Citizens or PRs facing non-capital criminal charges.",
    "lab": "LAB is the Legal Aid Bureau, the government legal aid office for eligible Singapore Citizens or PRs facing civil or family proceedings.",
    "fjss": "FJSS refers to Family Justice Support Scheme pathways for eligible matrimonial and family matters.",
    "urgent_issue": "An urgent issue usually means immediate safety risk, basic-needs or child-welfare crisis, or a concrete legal deadline within 14 days.",
    "vulnerable_applicant": "A vulnerable applicant is someone who may need adapted handling because of factors like age, disability, language barriers, distress, confusion, or inability to self-help safely.",
    "pchi": "PCHI means per capita household income. PCHI means total monthly household income divided by household members, including the applicant and all dependants.",
}
PBSG_GENERAL_ENQUIRY_FAQS = {
    "lasco": {
        "patterns": [r"\blasco\b"],
        "answer": PBSG_GLOSSARY_ANSWERS["lasco"],
        "source_ids": ["GEN3-T02"],
    },
    "clas": {
        "patterns": [r"\bclas\b", r"\bcriminal legal aid scheme\b"],
        "answer": PBSG_GLOSSARY_ANSWERS["clas"],
        "source_ids": ["GEN3-T02"],
    },
    "pdo": {
        "patterns": [r"\bpdo\b", r"\bpublic defender(?:['’]s)? office\b"],
        "answer": PBSG_GLOSSARY_ANSWERS["pdo"],
        "source_ids": ["GEN3-T02"],
    },
    "lab": {
        "patterns": [r"\blab\b", r"\blegal aid bureau\b"],
        "answer": PBSG_GLOSSARY_ANSWERS["lab"],
        "source_ids": ["GEN3-T03", "GEN3-T04"],
    },
    "fjss": {
        "patterns": [r"\bfjss\b", r"\bfamily justice support scheme\b"],
        "answer": PBSG_GLOSSARY_ANSWERS["fjss"],
        "source_ids": ["GEN3-T03"],
    },
    "pchi": {
        "patterns": [r"\bpchi\b", r"\bper capita household income\b"],
        "answer": PBSG_GLOSSARY_ANSWERS["pchi"],
        "source_ids": ["GEN3-T02", "GEN3-T03", "GEN3-T04"],
    },
    "services": {
        "patterns": [
            r"\bwhat services\b",
            r"\bservices (?:does|do|are)\b",
            r"\bwhat (?:does|can) (?:pbsg|pro bono sg|probono sg) (?:provide|do|offer)\b",
            r"\bwhat help (?:does|can) (?:pbsg|pro bono sg|probono sg) (?:provide|offer)\b",
            r"\btypes? of help\b",
            r"\bget legal help\b",
            r"\blegal guidance\b",
            r"\blegal clinic(?:s)?\b",
            r"\blegal representation\b",
        ],
        "answer": (
            "The approved triage content points applicants to different PBSG-related pathways depending on the matter: "
            "legal guidance or legal clinics, criminal legal aid triage, family/matrimonial triage, civil guidance, "
            "urgent handling, and escalation to PBSG Staff when the facts are too complex or sensitive for standard routing."
        ),
        "source_ids": ["GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04", "GEN3-T06", "GEN3-T13"],
    },
    "triage_route": {
        "patterns": [
            r"\bwhat(?:'s| is)? (?:a )?route\b",
            r"\bwhat (?:does|do) route [a-z]\b",
            r"\bwhat happens (?:after|when) .*route\b",
            r"\bwhy (?:am i|is the applicant|was this) routed\b",
            r"\bhow (?:do you|does this|does the chatbot) (?:choose|decide|determine) (?:a )?route\b",
            r"\brouting recommendation\b",
            r"\broute outcome\b",
            r"\bfinal route\b",
        ],
        "answer": (
            "A route is the recommended next pathway after the required triage questions are answered. The chatbot should "
            "not invent route letters or outcomes: route decisions come from the structured Golden Set branching logic and "
            "the deterministic routing engine. If a route applies, the response should explain what to say to the applicant, "
            "what they need to know, access steps, documents to prepare, intern next steps, and caveats where those details "
            "exist in the source route."
        ),
        "source_ids": ["GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04", "GEN3-T06", "GEN3-T13"],
    },
    "triage_stream": {
        "patterns": [
            r"\bwhat(?:'s| is)? (?:a )?(?:stream|workstream|workflow)\b",
            r"\bwhat (?:stream|workstream|workflow) am i in\b",
            r"\bselected stream\b",
            r"\blegal stream\b",
            r"\btriage stream\b",
            r"\bcriminal stream\b",
            r"\bfamily stream\b",
            r"\bmatrimonial stream\b",
            r"\bcivil stream\b",
            r"\burgent stream\b",
            r"\bvulnerable applicant stream\b",
            r"\bstreaming\b",
        ],
        "answer": (
            "In this chatbot, a stream or workstream means the active triage workflow, not video or audio streaming. "
            "Common streams in the approved Golden Set include first contact, criminal legal aid, family/matrimonial, "
            "civil and guidance, urgent matters, and vulnerable-applicant handling. Once a legal stream is selected, the "
            "deterministic routing engine follows that stream's questions and branching logic."
        ),
        "source_ids": ["GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04", "GEN3-T06", "GEN3-T13"],
    },
    "triage_process": {
        "patterns": [
            r"\bhow (?:does|do) (?:triage|the triage|this chatbot) work\b",
            r"\bwhat (?:questions|information|info) (?:do you|does pbsg|will you) need\b",
            r"\bwhy (?:are you|do you keep) asking\b",
            r"\bwhy (?:do you|does the chatbot) ask (?:these )?questions\b",
            r"\bwhat should i tell (?:you|the chatbot|the intern)\b",
            r"\bwhat facts (?:are|do you) need\b",
            r"\bstart triage\b",
        ],
        "answer": (
            "The triage flow asks only the facts needed to identify the correct legal help pathway. Depending on the topic, "
            "it may ask about the legal issue, whether there is urgency or safety risk, whether the applicant is already "
            "represented, citizenship or residency, prior applications to agencies such as LAB or PDO, means information, "
            "and whether staff escalation is needed. Route outcomes should come from the deterministic workflow, not an LLM guess."
        ),
        "source_ids": ["GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04", "GEN3-T06", "GEN3-T13"],
    },
    "staff_escalation": {
        "patterns": [
            r"\bpbsg staff\b",
            r"\bstaff escalation\b",
            r"\bescalate\b",
            r"\bhuman review\b",
            r"\bstaff assessment\b",
            r"\bcall back\b",
            r"\bfollow up\b",
        ],
        "answer": (
            "Staff escalation is used when the facts are too complex, urgent, sensitive, unclear, or outside the standard "
            "branching path for the intern to classify safely. The intern should record the applicant's particulars and the "
            "exact facts creating concern, then escalate to PBSG Staff according to the applicable route instructions."
        ),
        "source_ids": ["GEN3-T04", "GEN3-T06", "GEN3-T13"],
    },
    "who_can_get_help": {
        "patterns": [
            r"\bwho can get help\b",
            r"\bwho is eligible\b",
            r"\bwho qualifies\b",
            r"\bdo i qualify\b",
            r"\bcan i get help\b",
            r"\bcan (?:pbsg|pro bono sg|probono sg) help\b",
            r"\bcan (?:you|they) help me\b",
            r"\bam i eligible\b",
            r"\beligibility\b",
        ],
        "answer": (
            "Eligibility depends on the applicant's legal issue and the pathway. The triage flow may need facts such as "
            "the legal topic, citizenship or residency, whether another agency like LAB or PDO should be tried first, "
            "means information, urgency, and whether staff escalation is needed."
        ),
        "source_ids": ["GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04"],
    },
    "fees_cost": {
        "patterns": [
            r"\bis (?:it|pbsg|pro bono sg|probono sg) free\b",
            r"\bfree legal\b",
            r"\bhow much (?:does|will|is)\b",
            r"\bfees?\b",
            r"\bcosts?\b",
            r"\bpay\b",
            r"\blow-cost\b",
            r"\bmeans test\b",
        ],
        "answer": (
            "The approved triage content describes some pathways as free or low-cost, but cost and eligibility depend on "
            "the scheme and the applicant's facts. Some routes involve means tests, merits tests, or first applying to "
            "another agency such as LAB or PDO. The chatbot should triage the matter before confirming the relevant pathway."
        ),
        "source_ids": ["GEN3-T02", "GEN3-T03", "GEN3-T04"],
    },
    "apply_appointment_documents": {
        "patterns": [
            r"\bhow (?:do|can) i apply\b",
            r"\bapplication\b",
            r"\bappointment\b",
            r"\bbook\b",
            r"\bwalk[- ]?in\b",
            r"\bin person\b",
            r"\bdocuments?\b",
            r"\bwhat (?:should|do) i (?:bring|prepare)\b",
            r"\bprepare\b",
            r"\bform\b",
            r"\blink\b",
            r"\bwebsite\b",
        ],
        "answer": (
            "Application steps, appointment instructions, links, and documents depend on the route. The route card should "
            "share only the source-backed access details and preparation items for the applicable pathway. If the applicant "
            "cannot self-apply or needs in-person help, the Golden Set includes PBSG Counter handling for some legal clinic situations."
        ),
        "source_ids": ["GEN3-T02", "GEN3-T03", "GEN3-T04"],
    },
    "legal_advice_boundary": {
        "patterns": [
            r"\bcan (?:you|the intern|pbsg) give legal advice\b",
            r"\blegal advice\b",
            r"\badvise me\b",
            r"\bwhat should i do\b",
            r"\bwill i win\b",
            r"\bchances? of (?:winning|success)\b",
            r"\binterpret\b",
            r"\bcontract clause\b",
            r"\bplead guilty\b",
            r"\bshould i sue\b",
        ],
        "answer": (
            "The triage content says interns and volunteers must not give legal advice, interpret documents, predict case "
            "outcomes, or tell the applicant what they should do legally. They may help identify the right legal help pathway, "
            "read scheme information, take down particulars, and escalate to PBSG Staff when needed."
        ),
        "source_ids": ["GEN3-T13"],
    },
    "urgency": {
        "patterns": [
            r"\bwhat counts as urgent\b",
            r"\bwhat is an urgent (?:issue|matter)\b",
            r"\burgent\b",
            r"\bemergency\b",
            r"\bimmediate\b",
            r"\bdeadline\b",
            r"\bcourt date\b",
            r"\bhearing\b",
            r"\bwithin 14 days\b",
            r"\bunsafe\b",
            r"\bno shelter\b",
            r"\bno food\b",
            r"\bself[- ]?harm\b",
        ],
        "answer": (
            "Urgent handling is triggered by facts such as immediate safety risk, basic-needs or child-welfare crisis, "
            "or a concrete legal/procedural deadline within 14 days. If those facts are present, the urgent stream should "
            "be handled before or alongside the ordinary legal triage path."
        ),
        "source_ids": ["GEN3-T06"],
    },
    "vulnerable_applicant": {
        "patterns": [
            r"\bwhat counts as vulnerable\b",
            r"\bwhat is a vulnerable applicant\b",
            r"\bvulnerable applicant\b",
        ],
        "answer": PBSG_GLOSSARY_ANSWERS["vulnerable_applicant"],
        "source_ids": ["GEN3-T13"],
    },
    "counter_location": {
        "patterns": [
            r"\bstate courts help centre\b",
            r"\bpbsg counter\b",
            r"\bcounter\b",
            r"\bwhere (?:are you|is pbsg|is pro bono sg)\b",
            r"\blocated\b",
            r"\blocation\b",
            r"\baddress\b",
        ],
        "answer": (
            "The approved triage content mentions a PBSG Counter at State Courts Help Centre, "
            "1 Havelock Square, #B1-18 State Courts, Singapore 059724, for applicants who need in-person help with "
            "legal clinic applications. For current office, counter, and appointment details, check Pro Bono SG's "
            "official channels before sharing them as final operational information."
        ),
        "source_ids": ["GEN3-T04"],
    },
    "pbsg": {
        "patterns": [
            r"\bwhat(?:'s| is) (?:pbsg|pro bono sg|probono sg|pro bono singapore)\b",
            r"\btell me more about (?:pbsg|pro bono sg|probono sg|pro bono singapore)\b",
            r"\bwho (?:is|are) (?:pbsg|pro bono sg|probono sg|pro bono singapore)\b",
            r"\bpbsg\b",
            r"\bpro bono sg\b",
            r"\bprobono sg\b",
            r"\bpro bono singapore\b",
        ],
        "answer": (
            "Pro Bono SG is the organisation referenced in this triage content. The chatbot uses PBSG's structured "
            "Golden Set to help identify the right legal help pathway, such as legal clinics/guidance, criminal legal "
            "aid pathways, family-related pathways, civil guidance, urgent handling, or staff escalation where needed."
        ),
        "source_ids": ["GEN3-T01", "GEN3-T02", "GEN3-T03", "GEN3-T04", "GEN3-T06"],
    },
    "location": {
        "patterns": [
            r"\bwhere (?:are you|is pbsg|is pro bono sg)\b",
            r"\blocated\b",
            r"\blocation\b",
            r"\baddress\b",
            r"\bcounter\b",
        ],
        "answer": (
            "The approved triage content mentions a PBSG Counter at State Courts Help Centre, "
            "1 Havelock Square, #B1-18 State Courts, Singapore 059724, for applicants who need in-person help with "
            "legal clinic applications. For current office, counter, and appointment details, check Pro Bono SG's "
            "official channels before sharing them as final operational information."
        ),
        "source_ids": ["GEN3-T04"],
    },
}
PBSG_GENERAL_ENQUIRY_INTENT_PATTERN = re.compile(
    r"\b(what is|what's|what does|what happens|why|how|tell me more|explain|meaning of|where|who can|who is|who qualifies|can .* help|get help|services|located|location|address|counter|eligible|eligibility|qualify|route|routing|stream|workstream|workflow|triage|staff|escalate|urgent|deadline|free|fees?|costs?|appointment|apply|application|documents?|legal advice|streaming)\b",
    flags=re.IGNORECASE,
)
PBSG_TRIAGE_REQUEST_PATTERN = re.compile(
    r"\b(applicant|caller|client|my|me|i|we|he|she|they|someone)\b.{0,80}"
    r"\b(divorce|custody|maintenance|charged|charge|criminal|police|arrest|court|employment|salary|landlord|tenant|debt|probate|estate|urgent|deadline|violence|ppo)\b"
    r"|"
    r"\b(divorce|custody|maintenance|charged|charge|criminal|police|arrest|court|employment|salary|landlord|tenant|debt|probate|estate|urgent|deadline|violence|ppo)\b.{0,80}"
    r"\b(help|assist|representation|lawyer|legal|case|matter|issue|problem)\b",
    flags=re.IGNORECASE,
)

PBSG_CASE_SUMMARY_PENDING_MESSAGE = "Applicant summary is updating. You can continue with the next triage step now."
PBSG_CASE_SUMMARY_SYSTEM_PROMPT = """You write concise applicant case summaries for Pro Bono SG hotline interns.

Rules:
- Summarize only applicant facts listed in the provided JSON evidence.
- Do not decide eligibility, routing, route letters, or the next question.
- Do not mention internal workflow ids, GEN3 codes, JSON filenames, hidden state, or Q numbers.
- Do not infer residency, nationality, urgency, safety, income, representation, or eligibility. Mention those only when the evidence explicitly contains them.
- If a fact is not in the evidence, treat it as unknown. Do not fill gaps from the current workflow, the next question, or common assumptions.
- For high-risk facts, prefer the `summary_value` exactly.
- For residency facts, `normalized_value: "foreigner"` means the applicant is not a Singapore Citizen or PR.
- Keep it readable in under 15 seconds.
- Use 2-4 short bullets, each starting with "- ".
- If a fact is unknown, say it has not been provided yet instead of guessing.
"""
INTERNAL_ID_PATTERN = re.compile(r"\bGEN3-[A-Z0-9-]+\b|\bSelected Entry\b|\bQ\d+[A-Z]?\b", flags=re.IGNORECASE)

PBSG_QUICK_REPLY_LABELS = {
    ("GEN3-T01", "Q2"): {
        "if_calling_on_behalf_and_able_to_self_help": "Calling for someone else; they can contact PBSG directly",
        "if_self_or_calling_on_behalf_and_unable_to_self_help": (
            "Applicant is calling, or cannot contact PBSG themselves"
        ),
    },
    ("GEN3-T01", "Q3"): {
        "if_yes_and_nonprofit": "Yes, for a non-profit or charity",
        "if_yes_and_for_profit": "Yes, for a for-profit business",
        "if_no": "No, personal legal matter",
    },
    ("GEN3-T01", "Q4A"): {
        "if_guidance": "Legal guidance or clinic consultation",
        "if_representation": "Legal representation by a lawyer",
        "if_not_sure_or_both": "Not sure or both",
    },
    ("GEN3-T01", "Q5"): {
        "if_criminal": "Criminal matter",
        "if_matrimonial": "Family or matrimonial matter",
        "if_civil_or_others": "Civil or other matter",
    },
    ("GEN3-T02", "Q4"): {
        "if_no_foreigner": "No, not a Singapore Citizen or PR",
    },
    ("GEN3-T02", "Q5"): {
        "if_no_or_has_not_applied": "No, has not applied to PDO",
        "if_yes_passed_or_processing": "Yes, PDO approved or still processing",
        "if_yes_pdo_unable_to_assist": "Yes, PDO unable to assist",
    },
    ("GEN3-T03", "Q2"): {
        "if_no_foreigner": "No, not a Singapore Citizen or PR",
    },
    ("GEN3-T03", "Q3"): {
        "if_no": "No, has not applied to LAB",
        "if_yes_passed_or_processing": "Yes, LAB approved or still processing",
        "if_yes_failed_means_test": "Yes, LAB means test failed",
    },
    ("GEN3-T04", "Q1"): {
        "if_no_foreigner": "No, not a Singapore Citizen or PR",
    },
    ("GEN3-T04", "Q3"): {
        "if_no": "No, has not applied to LAB",
        "if_yes_lab_unable_to_assist": "Yes, LAB unable to assist",
        "if_yes_lab_able_or_not_sure": "Yes, LAB can assist or applicant is not sure",
    },
    ("GEN3-T06", "Q1"): {
        "if_immediate_safety_or_crisis": "Immediate safety or crisis",
        "if_basic_needs_or_child_welfare": "Basic needs or child welfare",
        "if_legal_or_procedural_deadline": "Legal or procedural deadline",
        "if_no_urgent_or_only_legal_seriousness": "No urgent issue beyond legal seriousness",
        "if_unclear_or_too_complex": "Unclear or too complex",
    },
    ("GEN3-T06", "Q4"): {
        "if_yes": "Yes, deadline or court date within 14 days",
        "if_no": "No deadline or court date within 14 days",
    },
}


class ChatReadRetrieveReadApproach(Approach):
    """
    A multi-step approach that first uses OpenAI to turn the user's question into a search query,
    then uses Azure AI Search to retrieve relevant documents, and then sends the conversation history,
    original user question, and search results to OpenAI to generate a response.
    """

    NO_RESPONSE = Approach.QUERY_REWRITE_NO_RESPONSE

    def __init__(
        self,
        *,
        search_client: SearchClient,
        search_index_name: str,
        knowledgebase_model: Optional[str],
        knowledgebase_deployment: Optional[str],
        knowledgebase_client: Optional[KnowledgeBaseRetrievalClient],
        knowledgebase_client_with_web: Optional[KnowledgeBaseRetrievalClient] = None,
        knowledgebase_client_with_sharepoint: Optional[KnowledgeBaseRetrievalClient] = None,
        knowledgebase_client_with_web_and_sharepoint: Optional[KnowledgeBaseRetrievalClient] = None,
        openai_client: AsyncOpenAI,
        chatgpt_model: str,
        chatgpt_deployment: Optional[str],  # Not needed for non-Azure OpenAI
        embedding_deployment: Optional[str],  # Not needed for non-Azure OpenAI or for retrieval_mode="text"
        embedding_model: str,
        embedding_dimensions: int,
        embedding_field: str,
        sourcepage_field: str,
        content_field: str,
        query_language: str,
        query_speller: str,
        prompt_manager: PromptManager,
        reasoning_effort: Optional[str] = None,
        multimodal_enabled: bool = False,
        image_embeddings_client: Optional[ImageEmbeddings] = None,
        global_blob_manager: Optional[BlobManager] = None,
        user_blob_manager: Optional[AdlsBlobManager] = None,
        use_web_source: bool = False,
        use_sharepoint_source: bool = False,
        retrieval_reasoning_effort: Optional[str] = None,
        enforce_access_control: bool = False,
    ):
        self.search_client = search_client
        self.search_index_name = search_index_name
        self.knowledgebase_model = knowledgebase_model
        self.knowledgebase_deployment = knowledgebase_deployment
        self.knowledgebase_client = knowledgebase_client
        self.knowledgebase_client_with_web = knowledgebase_client_with_web
        self.knowledgebase_client_with_sharepoint = knowledgebase_client_with_sharepoint
        self.knowledgebase_client_with_web_and_sharepoint = knowledgebase_client_with_web_and_sharepoint
        self.openai_client = openai_client
        self.chatgpt_model = chatgpt_model
        self.chatgpt_deployment = chatgpt_deployment
        self.embedding_deployment = embedding_deployment
        self.embedding_model = embedding_model
        self.embedding_dimensions = embedding_dimensions
        self.embedding_field = embedding_field
        self.sourcepage_field = sourcepage_field
        self.content_field = content_field
        self.query_language = query_language
        self.query_speller = query_speller
        self.prompt_manager = prompt_manager
        self.query_rewrite_tools = self.prompt_manager.load_tools("chat_query_rewrite_tools.json")
        self.reasoning_effort = reasoning_effort
        self.include_token_usage = True
        self.multimodal_enabled = multimodal_enabled
        self.image_embeddings_client = image_embeddings_client
        self.global_blob_manager = global_blob_manager
        self.user_blob_manager = user_blob_manager
        # Track whether web source retrieval is enabled for this deployment; overrides may only disable it.
        self.web_source_enabled = use_web_source
        self.use_sharepoint_source = use_sharepoint_source
        self.retrieval_reasoning_effort = retrieval_reasoning_effort
        self.enforce_access_control = enforce_access_control
        self.pbsg_golden_set_entries = load_golden_set_entries()
        self.pbsg_routing_engine = PBSGRoutingEngine(self.pbsg_golden_set_entries)

    def extract_followup_questions(self, content: Optional[str]):
        if content is None:
            return content, []
        return content.split("<<")[0], re.findall(r"<<([^>>]+)>>", content)

    def extract_golden_set_entries(self, text_sources: Optional[list[str]]) -> dict[str, dict[str, Any]]:
        entries: dict[str, dict[str, Any]] = {}
        for source in text_sources or []:
            json_start = source.find("{")
            json_end = source.rfind("}")
            if json_start < 0 or json_end <= json_start:
                continue
            try:
                entry = json.loads(source[json_start : json_end + 1])
            except json.JSONDecodeError:
                continue
            entry_id = entry.get("id")
            if isinstance(entry_id, str) and isinstance(entry.get("branching_logic"), dict):
                entries[entry_id] = entry
        return entries

    def extract_quick_reply_target(self, content: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not content:
            return None, None

        marker = parse_pbsg_state_marker(content)
        if marker.get("pending_question"):
            return marker.get("pending_entry") or marker.get("selected_entry"), marker["pending_question"].upper()

        selected_entry_matches = re.findall(r"\*\*Selected Entry:\*\*\s*([A-Z0-9-]+)", content, flags=re.IGNORECASE)
        selected_entry_id = selected_entry_matches[-1] if selected_entry_matches else None

        ask_markers = [
            "Ask the applicant (read verbatim):",
            "Tell the applicant:",
            "Back to triage",
        ]
        question_region = content
        marker_positions = [content.rfind(marker) for marker in ask_markers]
        marker_position = max(marker_positions)
        if marker_position >= 0:
            question_region = content[marker_position:]
        elif "Route " in content:
            return selected_entry_id, None

        question_matches = re.findall(r"(?:(GEN3-[A-Z0-9-]+)\s+)?(Q\d+[A-Z]?)\s*:", question_region)
        if not question_matches:
            question_matches = re.findall(r"(?:(GEN3-[A-Z0-9-]+)\s+)?(Q\d+[A-Z]?)\b", question_region)
        if not question_matches:
            return selected_entry_id, None

        entry_id, question_id = question_matches[-1]
        return entry_id or selected_entry_id, question_id

    def normalize_asked_question_text(self, content: Optional[str], extra_info: ExtraInfo) -> Optional[str]:
        if not content:
            return content

        selected_entry_matches = re.findall(r"\*\*Selected Entry:\*\*\s*([A-Z0-9-]+)", content, flags=re.IGNORECASE)
        selected_entry_id = selected_entry_matches[-1] if selected_entry_matches else None
        marker = parse_pbsg_state_marker(content)
        selected_entry_id = marker.get("selected_entry", selected_entry_id)
        if not selected_entry_id:
            return content

        entries = self.extract_golden_set_entries(extra_info.data_points.text)
        selected_entry = entries.get(selected_entry_id)
        branching_logic = selected_entry.get("branching_logic") if selected_entry else None
        if not isinstance(branching_logic, dict):
            return content

        ask_markers = [
            "Ask the applicant (read verbatim):",
            "Tell the applicant:",
            "Back to triage",
        ]
        marker_positions = [content.rfind(marker) for marker in ask_markers]
        marker_position = max(marker_positions)
        if marker_position < 0:
            return content

        ask_region = content[marker_position:]
        question_pattern = re.compile(
            r"(?P<prefix>>?\s*\**(?:(?P<entry>GEN3-[A-Z0-9-]+)\s+)?(?P<question>Q\d+[A-Z]?)\s*:\s*)"
            r"(?P<quote>[\"“])(?P<text>[^\"“”\n]*)(?P<closing_quote>[\"”])(?P<suffix>\**)",
            flags=re.IGNORECASE,
        )
        matches = list(question_pattern.finditer(ask_region))
        if not matches:
            return content

        match = matches[-1]
        explicit_entry_id = match.group("entry")
        if explicit_entry_id and explicit_entry_id != selected_entry_id:
            return content

        question_id = match.group("question")
        question_node = branching_logic.get(question_id)
        if not isinstance(question_node, dict):
            return content

        canonical_question = question_node.get("question")
        if not isinstance(canonical_question, str) or not canonical_question:
            return content

        normalized_question = convert_question_to_second_person(canonical_question)
        replacement = (
            f"{match.group('prefix')}{match.group('quote')}{normalized_question}"
            f"{match.group('closing_quote')}{match.group('suffix')}"
        )
        start = marker_position + match.start()
        end = marker_position + match.end()
        return content[:start] + replacement + content[end:]

    def label_from_branch_key(self, branch_key: str) -> str:
        return label_from_branch_key(branch_key)

    def quick_reply_label(self, entry_id: str, question_id: str, branch_key: str) -> str:
        question_labels = PBSG_QUICK_REPLY_LABELS.get((entry_id, question_id), {})
        return question_labels.get(branch_key, self.label_from_branch_key(branch_key))

    def quick_reply_outcome_is_supported(self, outcome: Any) -> bool:
        if not isinstance(outcome, str):
            return False
        normalized = outcome.lower()
        unsupported_phrases = [
            "walk through",
            "describe both",
            "ask for the",
            "note who",
            "coordinate rather than duplicate",
        ]
        return not any(phrase in normalized for phrase in unsupported_phrases)

    def build_quick_reply(self, content: Optional[str], extra_info: ExtraInfo) -> Optional[QuickReply]:
        entry_id, question_id = self.extract_quick_reply_target(content)
        if not entry_id or not question_id:
            return None

        entries = self.extract_golden_set_entries(extra_info.data_points.text)
        entry = entries.get(entry_id)
        if not entry:
            return None

        branching_logic = entry.get("branching_logic")
        question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict):
            return None

        branch_keys = [key for key in question_node if key.startswith("if_")]
        if not 1 < len(branch_keys) <= 5:
            return None
        if not all(self.quick_reply_outcome_is_supported(question_node.get(key)) for key in branch_keys):
            return None

        options = [
            QuickReplyOption(
                id=key,
                label=self.quick_reply_label(entry_id, question_id, key),
                value=self.quick_reply_label(entry_id, question_id, key),
            )
            for key in branch_keys
        ]
        return QuickReply(mode="single", entryId=entry_id, questionId=question_id, options=options)

    def render_deterministic_question_response(
        self, transition: PBSGTransition, entries: dict[str, dict[str, Any]]
    ) -> Optional[str]:
        if transition.transition_type not in {"proceed_question", "nested_stream"}:
            return None
        if not transition.target_entry_id or not transition.target_question_id:
            return None
        entry = entries.get(transition.target_entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        question_node = branching_logic.get(transition.target_question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict):
            return None
        question = question_node.get("question")
        if not isinstance(question, str):
            return None

        lines = selected_stream_lines(
            entries,
            transition.entry_id,
            transition.target_entry_id,
            transition.target_question_id,
            transition.route_label,
        )
        lines.extend(
            [
                "",
                "Latest triage update:",
                "",
                f"- The applicant's response: **{self.label_from_branch_key(transition.branch_key)}**.",
                "- What this means: we should continue with the next required triage question.",
                "",
                "Triage progress:",
                "",
                f"- Continue in the {stream_display_name(entries, transition.target_entry_id)}.",
                "- Next step: ask the required follow-up question below.",
            ]
        )
        lines.extend(next_question_lines(entries, transition.target_entry_id, transition.target_question_id, question))
        return "\n".join(lines)

    def render_deterministic_transition_response(
        self, transition: PBSGTransition | None, entries: dict[str, dict[str, Any]]
    ) -> Optional[str]:
        if not transition or transition.transition_type != "terminal_route":
            return None
        return PBSGRoutingEngine(entries).render_transition(transition)

    def apply_triage_response_guard(self, content: Optional[str], extra_info: ExtraInfo) -> Optional[str]:
        entries = self.extract_golden_set_entries(extra_info.data_points.text)
        if not content or not entries:
            return content

        deterministic_response = self.render_deterministic_transition_response(
            extra_info.deterministic_transition,
            entries,
        )
        if deterministic_response:
            return deterministic_response

        is_valid, reason = validate_response_questions(content, entries)
        if not is_valid:
            return safe_escalation_response(content, entries, reason or "unknown transition error")

        expected_transition = extra_info.deterministic_transition
        is_valid, reason = validate_response_transition(content, entries, expected_transition)
        if is_valid:
            return content
        if isinstance(expected_transition, PBSGTransition):
            deterministic_response = self.render_deterministic_question_response(expected_transition, entries)
            if deterministic_response:
                return deterministic_response
        return safe_escalation_response(content, entries, reason or "unknown transition error")

    def chat_completion_from_content(self, content: str, completion_id: str = "no-final-call") -> ChatCompletion:
        return ChatCompletion(
            id=completion_id,
            object="chat.completion",
            created=0,
            model=self.chatgpt_model,
            choices=[
                Choice(
                    message=ChatCompletionMessage(
                        role="assistant",
                        content=content,
                    ),
                    finish_reason="stop",
                    index=0,
                )
            ],
        )

    def golden_set_data_points(self, entries: dict[str, dict[str, Any]]) -> DataPoints:
        return DataPoints(
            text=[f"{entry_id}.json: {json.dumps(entry, ensure_ascii=False)}" for entry_id, entry in sorted(entries.items())],
            citations=[f"{entry_id}.json" for entry_id in sorted(entries)],
        )

    def ensure_golden_set_source_entries(
        self,
        data_points: DataPoints,
        entry_ids: list[str],
    ) -> None:
        if not entry_ids:
            return
        data_points.text = data_points.text or []
        data_points.citations = data_points.citations or []
        existing_entries = self.extract_golden_set_entries(data_points.text)
        for entry_id in entry_ids:
            entry = self.pbsg_golden_set_entries.get(entry_id)
            if not entry or entry_id in existing_entries:
                continue
            data_points.text.append(f"{entry_id}.json: {json.dumps(entry, ensure_ascii=False)}")
            citation = f"{entry_id}.json"
            if citation not in data_points.citations:
                data_points.citations.append(citation)

    def ensure_initial_topic_sources(
        self,
        data_points: DataPoints,
        messages: list[ChatCompletionMessageParam],
        original_user_query: Any,
    ) -> None:
        if len(messages) != 1 or not isinstance(original_user_query, str):
            return
        resolution = resolve_initial_topic(self.pbsg_golden_set_entries, original_user_query)
        if not resolution:
            return
        entry_ids = [
            resolution.entry_id,
            *(topic.entry_id for topic in resolution.queued_topics),
            *resolution.overlays,
        ]
        self.ensure_golden_set_source_entries(data_points, entry_ids)

    def pbsg_case_summary_hash(self, triage_state: Any) -> str:
        payload = {
            "active_workflow": getattr(triage_state, "active_workflow", None),
            "pending_entry_id": getattr(triage_state, "pending_entry_id", None),
            "current_question_id": getattr(triage_state, "current_question_id", None),
            "completed_workflows": getattr(triage_state, "completed_workflows", []),
            "queued_workflows": getattr(triage_state, "queued_workflows", []),
            "fact_ledger": [
                {
                    "fact_key": getattr(fact, "fact_key", None),
                    "value": getattr(fact, "value", None),
                    "normalized_value": getattr(fact, "normalized_value", None),
                    "source": getattr(fact, "source", None),
                    "status": getattr(fact, "status", None),
                }
                for fact in getattr(triage_state, "fact_ledger", [])
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    def pending_pbsg_case_summary(self, triage_state: Any) -> dict[str, Any]:
        return {
            "text": "",
            "status": "pending",
            "source_turn_hash": self.pbsg_case_summary_hash(triage_state),
            "updated_at": None,
        }

    def response_context_with_case_summary(self, context: dict[str, Any], triage_state: Any) -> dict[str, Any]:
        context["pbsg_case_summary"] = self.pending_pbsg_case_summary(triage_state)
        return context

    def sanitize_pbsg_case_summary(self, summary: str) -> str:
        lines = [line.strip() for line in summary.splitlines() if line.strip()]
        cleaned_lines: list[str] = []
        for line in lines:
            line = re.sub(r"^\d+[.)]\s*", "- ", line)
            if not line.startswith("- "):
                line = f"- {line.lstrip('- ').strip()}"
            line = INTERNAL_ID_PATTERN.sub("", line)
            line = re.sub(r"\s+", " ", line).strip()
            if line and line != "-":
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines[:4])

    def reliable_case_summary_facts(self, triage_state: Any) -> list[PBSGTriageFact]:
        reliable_source_types = {"routing_answer", "deterministic_transition", "user_message", "structured_extraction"}
        high_risk_fact_keys = {
            "applicant.residency_status",
            "matter.urgency_or_safety",
            "applicant.representation_status",
            "applicant.means_status",
            "applicant.lab_application_status",
            "applicant.pdo_application_status",
        }
        pending_source = (
            f"{triage_state.pending_entry_id}.{triage_state.current_question_id}"
            if getattr(triage_state, "pending_entry_id", None) and getattr(triage_state, "current_question_id", None)
            else None
        )
        facts: list[PBSGTriageFact] = []
        for fact in getattr(triage_state, "fact_ledger", [])[-10:]:
            if getattr(fact, "status", None) != "active":
                continue
            if getattr(fact, "source_type", None) not in reliable_source_types:
                continue
            if pending_source and getattr(fact, "source", None) == pending_source:
                continue
            if fact.fact_key in high_risk_fact_keys and fact.source_type == "user_message":
                source_text = (fact.source_text or "").lower()
                if fact.fact_key == "applicant.residency_status" and not re.search(
                    r"\b(singaporean|singapore citizen|sg citizen|sgc|permanent resident|\bpr\b|foreigner|"
                    r"work permit|employment pass|s pass|dependent pass|not (?:a )?singapore citizen(?: or pr)?|"
                    r"not (?:a )?citizen|not (?:a )?pr)\b",
                    source_text,
                ):
                    continue
            facts.append(fact)
        return facts

    def case_summary_value_for_fact(self, fact: PBSGTriageFact) -> str:
        if fact.fact_key == "applicant.residency_status":
            if fact.normalized_value == "foreigner" or fact.branch_value == "if_no_foreigner":
                return "Not a Singapore Citizen or PR (foreigner)"
            if fact.normalized_value == "sgc_pr" or fact.branch_value == "if_yes":
                return "Singapore Citizen or PR"
        return fact.value

    def case_summary_fact_payload(self, triage_state: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for fact in self.reliable_case_summary_facts(triage_state):
            entry_id = None
            question_id = None
            if "." in fact.source and fact.source.split(".", 1)[0] in self.pbsg_golden_set_entries:
                entry_id, question_id = fact.source.split(".", 1)
            question = question_text_from_entry(self.pbsg_golden_set_entries, entry_id, question_id)
            payload.append(
                {
                    "fact_key": fact.fact_key,
                    "value": fact.value,
                    "summary_value": self.case_summary_value_for_fact(fact),
                    "normalized_value": fact.normalized_value,
                    "branch_value": fact.branch_value,
                    "source_type": fact.source_type,
                    "source": fact.source,
                    "question": question,
                    "provenance": fact.source_text or fact.provenance,
                }
            )
        return payload

    def compact_case_summary_facts(self, triage_state: Any) -> str:
        facts = self.case_summary_fact_payload(triage_state)
        if not facts:
            return "[]"
        return json.dumps(facts, ensure_ascii=False, indent=2)

    async def generate_pbsg_case_summary(
        self,
        messages: list[ChatCompletionMessageParam],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = context or {}
        triage_state = build_triage_state(messages, self.pbsg_golden_set_entries, "")
        source_turn_hash = self.pbsg_case_summary_hash(triage_state)
        fact_payload = self.case_summary_fact_payload(triage_state)
        if not self.openai_client:
            return {
                "text": "",
                "status": "unavailable",
                "source_turn_hash": source_turn_hash,
                "updated_at": None,
                "source_fact_count": len(fact_payload),
                "source_fact_keys": [fact["fact_key"] for fact in fact_payload],
            }

        prompt = "\n".join(
            [
                "Current stream: " + stream_display_name(self.pbsg_golden_set_entries, triage_state.active_workflow),
                "Queued streams: "
                + ", ".join(stream_display_name(self.pbsg_golden_set_entries, workflow) for workflow in triage_state.queued_workflows),
                "Completed streams: "
                + ", ".join(stream_display_name(self.pbsg_golden_set_entries, workflow) for workflow in triage_state.completed_workflows),
                "",
                "Evidence JSON (use only these facts):",
                json.dumps(fact_payload, ensure_ascii=False, indent=2) if fact_payload else "[]",
            ]
        )
        response = await cast(
            Awaitable[ChatCompletion],
            self.create_chat_completion(
                self.chatgpt_deployment,
                self.chatgpt_model,
                [
                    {"role": "system", "content": PBSG_CASE_SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                overrides={},
                response_token_limit=220,
                temperature=0.1,
            ),
        )
        text = self.sanitize_pbsg_case_summary(response.choices[0].message.content or "")
        return {
            "text": text,
            "status": "ready" if text else "unavailable",
            "source_turn_hash": source_turn_hash,
            "updated_at": int(time.time()),
            "source_fact_count": len(fact_payload),
            "source_fact_keys": [fact["fact_key"] for fact in fact_payload],
        }

    def build_deterministic_chat_response(
        self,
        deterministic_result: PBSGDeterministicResult,
        session_state: Any = None,
        turn_classification: PBSGTurnClassification | None = None,
    ) -> dict[str, Any]:
        queued_topics: list[PBSGQueuedTopic] = []
        if turn_classification and turn_classification.new_topics:
            for topic in turn_classification.new_topics:
                if topic.entry_id not in deterministic_result.state.queued_workflows:
                    deterministic_result.state.queued_workflows.append(topic.entry_id)
            queued_topics = turn_classification.new_topics

        extra_info = ExtraInfo(
            data_points=self.golden_set_data_points(deterministic_result.entries),
            deterministic_transition=deterministic_result.transition,
        )
        content = self.apply_triage_response_guard(deterministic_result.content, extra_info)
        if content and queued_topics:
            content = self.append_queued_topic_note(content, deterministic_result.state.active_workflow, queued_topics)
        if content:
            content = self.append_route_completion_note(content, deterministic_result.state)
            content = apply_memory_notes(content, deterministic_result.state)
        extra_info.quick_reply = self.build_continue_queued_topic_quick_reply(deterministic_result.state)
        if not extra_info.quick_reply:
            extra_info.quick_reply = self.build_quick_reply(content, extra_info)
        extra_info.thoughts.append(
            ThoughtStep(
                "Deterministic PBSG routing",
                "Answered from Golden Set branching logic without retrieval or LLM generation.",
                {
                    "mode": deterministic_result.state.mode,
                    "entry_id": deterministic_result.transition.entry_id,
                    "question_id": deterministic_result.transition.question_id,
                    "branch_key": deterministic_result.transition.branch_key,
                    "transition_type": deterministic_result.transition.transition_type,
                    "turn_type": turn_classification.turn_type if turn_classification else "answer_only",
                },
            )
        )
        response_context = self.response_context_with_case_summary(
            {
                "thoughts": extra_info.thoughts,
                "data_points": {
                    key: value for key, value in asdict(extra_info.data_points).items() if value is not None
                },
                "followup_questions": extra_info.followup_questions,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(deterministic_result.state),
            },
            deterministic_result.state,
        )
        response_context = context_with_triage_memory(response_context, deterministic_result.state)
        return {
            "message": {"content": content, "role": "assistant"},
            "context": response_context,
            "session_state": extend_session_state_with_memory(session_state, deterministic_result.state),
        }

    def build_continue_queued_topic_quick_reply(self, triage_state: Any) -> Optional[QuickReply]:
        if getattr(triage_state, "routing_completion_status", None) != "awaiting_topic_resolution":
            return None
        queued_workflows = getattr(triage_state, "queued_workflows", None) or []
        if not queued_workflows:
            return None
        workflow_id = queued_workflows[0]
        entry = self.pbsg_golden_set_entries.get(workflow_id, {})
        topic = entry.get("topic") if isinstance(entry, dict) else None
        label_suffix = f" - {topic}" if isinstance(topic, str) and topic else ""
        stream_name = stream_display_name(self.pbsg_golden_set_entries, workflow_id)
        return QuickReply(
            mode="single",
            entryId=workflow_id,
            questionId="CONTINUE",
            options=[
                QuickReplyOption(
                    id=f"continue_queued_workflow:{workflow_id}",
                    label=f"Topic resolved - continue to {stream_name}",
                    value=f"Continue queued workflow: {workflow_id}",
                )
            ],
        )

    def append_route_completion_note(self, content: str, triage_state: Any) -> str:
        quick_reply = self.build_continue_queued_topic_quick_reply(triage_state)
        if not quick_reply:
            return content
        workflow_id = quick_reply.entryId
        entry = self.pbsg_golden_set_entries.get(workflow_id, {})
        topic = entry.get("topic") if isinstance(entry, dict) else workflow_id
        stream_name = stream_display_name(self.pbsg_golden_set_entries, workflow_id)
        marker = parse_pbsg_state_marker(content)
        state_marker = pbsg_state_marker(
            marker.get("selected_entry") or getattr(triage_state, "active_workflow", None) or getattr(triage_state, "workflow_id", None),
            marker.get("pending_entry"),
            marker.get("pending_question"),
            marker.get("route_label"),
            [workflow_id],
        )
        note = "\n".join(
            [
                "",
                state_marker,
                "**Queued topic ready:**",
                "",
                f"- {stream_name}: {topic}",
                "- Click the button when this routed topic has been resolved and you are ready to continue.",
                "",
                "Topics identified:",
                f"1. {stream_display_name(self.pbsg_golden_set_entries, triage_state.active_workflow or triage_state.workflow_id)} - routed workflow",
                f"2. {stream_name} - queued workflow",
            ]
        )
        return f"{content}{note}"

    def append_queued_topic_note(
        self,
        content: str,
        active_workflow: str | None,
        topics: list[PBSGQueuedTopic],
    ) -> str:
        if not topics:
            return content
        unique_topics: list[PBSGQueuedTopic] = []
        for topic in topics:
            if topic.entry_id not in [existing.entry_id for existing in unique_topics]:
                unique_topics.append(topic)
        active_line = (
            f"1. {stream_display_name(self.pbsg_golden_set_entries, active_workflow)} - active workflow"
            if active_workflow
            else "1. Current stream - active workflow"
        )
        queued_lines = [
            f"{index}. {stream_display_name(self.pbsg_golden_set_entries, topic.entry_id)} - queued workflow (noted from: {topic.evidence})"
            for index, topic in enumerate(unique_topics, start=2)
        ]
        marker = parse_pbsg_state_marker(content)
        state_marker = pbsg_state_marker(
            marker.get("selected_entry") or active_workflow,
            marker.get("pending_entry"),
            marker.get("pending_question"),
            marker.get("route_label"),
            [topic.entry_id for topic in unique_topics],
        )
        note = "\n".join(
            [
                "",
                state_marker,
                "**Queued topic note:** I noted a separate possible topic and will handle it after this stream is routed.",
                "",
                "Topics identified:",
                active_line,
                *queued_lines,
            ]
        )
        return f"{content}{note}"

    def try_deterministic_locked_response(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if not messages:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
        turn_classification = classify_turn_interrupt(self.pbsg_golden_set_entries, triage_state, latest_content)
        deterministic_result = self.pbsg_routing_engine.execute_locked_turn(messages[:-1], latest_content)
        if turn_classification.should_call_llm and not deterministic_result:
            return None
        if not deterministic_result:
            return None
        return self.build_deterministic_chat_response(deterministic_result, session_state, turn_classification)

    def try_deterministic_initial_response(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if len(messages) != 1:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        deterministic_result = self.pbsg_routing_engine.execute_initial_turn(latest_content)
        if not deterministic_result:
            return None
        return self.build_deterministic_chat_response(deterministic_result, session_state)

    def is_bare_initial_topic_message(self, content: str) -> bool:
        normalized = re.sub(r"\s+", " ", content).strip().lower()
        if not normalized:
            return True
        normalized_words = re.findall(r"[a-z0-9]+", normalized)
        if len(normalized_words) <= 3 and normalized in {
            "hi",
            "hello",
            "hey",
            "thanks",
            "thank you",
            "ok",
            "okay",
            "start triage",
            "i need help",
            "need help",
            "help",
            "general enquiry",
            "general inquiry",
        }:
            return True
        return False

    def initial_topic_classifier_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry_id in sorted(
            self.pbsg_golden_set_entries,
            key=lambda candidate_id: (candidate_id != "GEN3-T01", candidate_id),
        ):
            entry = self.pbsg_golden_set_entries[entry_id]
            entries.append(
                {
                    "id": entry_id,
                    "topic": entry.get("topic"),
                    "category": entry.get("category"),
                    "user_query": entry.get("user_query"),
                    "variations": entry.get("variations", []),
                    "part_a_general_info": entry.get("part_a_general_info"),
                }
            )
        return entries

    def fact_extraction_entries_for_ids(self, entry_ids: list[str]) -> list[dict[str, Any]]:
        extraction_entries: list[dict[str, Any]] = []
        for entry_id in entry_ids:
            entry = self.pbsg_golden_set_entries.get(entry_id)
            branching_logic = entry.get("branching_logic") if isinstance(entry, dict) else None
            if not isinstance(branching_logic, dict):
                continue
            questions: list[dict[str, Any]] = []
            for question_id, question_node in branching_logic.items():
                if not isinstance(question_id, str) or not isinstance(question_node, dict):
                    continue
                question = question_node.get("question")
                if not isinstance(question, str):
                    continue
                questions.append(
                    {
                        "question_id": question_id,
                        "question": question,
                        "allowed_branch_keys": [key for key in question_node if key.startswith("if_")],
                    }
                )
            extraction_entries.append({"id": entry_id, "topic": entry.get("topic"), "questions": questions})
        return extraction_entries

    def initial_fact_extraction_entries(self, resolution: PBSGTopicResolution) -> list[dict[str, Any]]:
        entry_ids = [resolution.entry_id]
        entry_ids.extend(topic.entry_id for topic in resolution.queued_topics if topic.entry_id not in entry_ids)
        entry_ids.extend(entry_id for entry_id in resolution.overlays if entry_id not in entry_ids)
        return self.fact_extraction_entries_for_ids(entry_ids)

    def context_fact_extraction_entries(self, triage_state: Any) -> list[dict[str, Any]]:
        entry_ids: list[str] = []
        for entry_id in [
            triage_state.pending_entry_id,
            triage_state.active_workflow,
            triage_state.workflow_id,
            triage_state.parent_workflow,
            *triage_state.triggered_overlays,
            *triage_state.concurrent_monitors,
            *triage_state.queued_workflows,
        ]:
            if isinstance(entry_id, str) and entry_id in self.pbsg_golden_set_entries and entry_id not in entry_ids:
                entry_ids.append(entry_id)
        return self.fact_extraction_entries_for_ids(entry_ids)

    def conversation_turn_summaries(self, messages: list[ChatCompletionMessageParam]) -> list[dict[str, str]]:
        summaries: list[dict[str, str]] = []
        for message in messages[-12:]:
            role = message.get("role")
            content = message.get("content")
            if isinstance(role, str) and isinstance(content, str):
                summaries.append({"role": role, "content": content[:4000]})
        return summaries

    def fact_from_structured_initial_answer(
        self,
        answer: dict[str, Any],
        latest_content: str,
        *,
        source_prefix: str = "structured_initial_extraction",
        source_turn_index: int | None = 0,
    ) -> PBSGTriageFact | None:
        entry_id = answer.get("entry_id")
        question_id = answer.get("question_id")
        branch_key = answer.get("branch_key")
        confidence = answer.get("confidence")
        evidence = answer.get("evidence")
        if (
            not isinstance(entry_id, str)
            or not isinstance(question_id, str)
            or not isinstance(branch_key, str)
            or not isinstance(confidence, (int, float))
            or confidence < 0.75
        ):
            return None
        entry_id = entry_id.upper()
        question_id = question_id.upper()
        entry = self.pbsg_golden_set_entries.get(entry_id)
        branching_logic = entry.get("branching_logic") if isinstance(entry, dict) else None
        question_node = branching_logic.get(question_id) if isinstance(branching_logic, dict) else None
        if not isinstance(question_node, dict) or branch_key not in question_node:
            return None
        question = question_text_from_entry(self.pbsg_golden_set_entries, entry_id, question_id)
        fact_key = canonical_fact_key_for_node(entry_id, question_id, question)
        if not fact_key:
            return None
        value = label_from_branch_key(branch_key)
        workflow_scope = "workflow" if fact_key.startswith("workflow.question.") else "global"
        return PBSGTriageFact(
            fact_key=fact_key,
            value=value,
            normalized_value=normalize_branch_label(value),
            source=f"{source_prefix}:{entry_id}.{question_id}",
            scope=workflow_scope,
            confidence=float(confidence),
            provenance=evidence if isinstance(evidence, str) else latest_content,
            source_type="structured_extraction",
            branch_value=branch_key,
            source_text=latest_content,
            source_turn_index=source_turn_index,
            workflow_scope=workflow_scope,
        )

    def facts_from_structured_answers(
        self,
        raw_answers: Any,
        latest_content: str,
        *,
        source_prefix: str,
        source_turn_index: int | None,
    ) -> list[PBSGTriageFact]:
        if not isinstance(raw_answers, list):
            return []
        return [
            fact
            for raw_answer in raw_answers
            if isinstance(raw_answer, dict)
            for fact in [
                self.fact_from_structured_initial_answer(
                    raw_answer,
                    latest_content,
                    source_prefix=source_prefix,
                    source_turn_index=source_turn_index,
                )
            ]
            if fact
        ]

    async def extract_structured_initial_facts(
        self,
        latest_content: str,
        resolution: PBSGTopicResolution,
        overrides: dict[str, Any],
    ) -> tuple[list[PBSGTriageFact], ThoughtStep | None]:
        extraction_entries = self.initial_fact_extraction_entries(resolution)
        if not extraction_entries:
            return [], None
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "You are the PBSG initial answer extraction support agent. Read the intern's first message and "
                    "identify which triage questions are already answered. Return compact JSON only with key "
                    "answered_questions, an array of objects with entry_id, question_id, branch_key, confidence, "
                    "and evidence. Use only provided entry ids, question ids, and branch keys. Extract answers only "
                    "when the message clearly answers that exact triage question, either explicitly or by clear "
                    "semantic implication. Do not choose topic ids, route letters, next questions, handoffs, or final "
                    "recommendations."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "latest_user_message": latest_content,
                        "active_entry_id": resolution.entry_id,
                        "queued_entry_ids": [topic.entry_id for topic in resolution.queued_topics],
                        "monitor_entry_ids": resolution.overlays,
                        "entries": extraction_entries,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        topic_model = overrides.get("pbsg_initial_topic_model", self.chatgpt_model)
        topic_deployment = overrides.get("pbsg_initial_topic_deployment", self.chatgpt_deployment)
        try:
            completion = await cast(
                Awaitable[ChatCompletion],
                self.create_chat_completion(
                    topic_deployment,
                    topic_model,
                    messages,
                    overrides,
                    self.get_response_token_limit(topic_model, 300),
                    should_stream=False,
                    temperature=0.0,
                ),
            )
        except Exception:
            return [], None
        try:
            payload = json.loads(completion.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return [], None
        raw_answers = payload.get("answered_questions")
        facts = self.facts_from_structured_answers(
            raw_answers,
            latest_content,
            source_prefix="structured_initial_extraction",
            source_turn_index=0,
        )
        thought = self.format_thought_step_for_chatcompletion(
            title="Structured PBSG initial answer extractor",
            messages=messages,
            overrides=overrides,
            model=topic_model,
            deployment=topic_deployment,
            usage=completion.usage,
        )
        return facts, thought

    def relevance_fact_from_assessment(
        self,
        assessment: dict[str, Any],
        triage_state: Any,
        latest_content: str,
        source_turn_index: int | None,
    ) -> PBSGTriageFact | None:
        disposition = assessment.get("disposition")
        if disposition not in {"already_answered", "not_relevant"}:
            return None
        branch_key = assessment.get("branch_key")
        if not isinstance(branch_key, str):
            return None
        entry_id = assessment.get("entry_id")
        question_id = assessment.get("question_id")
        if not isinstance(entry_id, str):
            entry_id = triage_state.pending_entry_id
        if not isinstance(question_id, str):
            question_id = triage_state.current_question_id
        return self.fact_from_structured_initial_answer(
            {
                "entry_id": entry_id,
                "question_id": question_id,
                "branch_key": branch_key,
                "confidence": assessment.get("confidence", 0),
                "evidence": assessment.get("evidence"),
            },
            latest_content,
            source_prefix="structured_context_relevance",
            source_turn_index=source_turn_index,
        )

    async def extract_structured_context_support(
        self,
        messages: list[ChatCompletionMessageParam],
        triage_state: Any,
        latest_content: str,
        overrides: dict[str, Any],
    ) -> tuple[list[PBSGTriageFact], dict[str, Any] | None, ThoughtStep | None]:
        extraction_entries = self.context_fact_extraction_entries(triage_state)
        if not extraction_entries or not triage_state.pending_entry_id or not triage_state.current_question_id:
            return [], None, None
        question = question_text_from_entry(
            self.pbsg_golden_set_entries, triage_state.pending_entry_id, triage_state.current_question_id
        )
        entry = self.pbsg_golden_set_entries.get(triage_state.pending_entry_id)
        branching_logic = entry.get("branching_logic") if isinstance(entry, dict) else None
        question_node = (
            branching_logic.get(triage_state.current_question_id) if isinstance(branching_logic, dict) else None
        )
        branch_keys = [key for key in question_node if key.startswith("if_")] if isinstance(question_node, dict) else []
        if not question or not branch_keys:
            return [], None, None
        original_query = ""
        for message in messages:
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                original_query = cast(str, message.get("content"))
                break
        support_messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "You are the PBSG contextual triage support agent. Use the original topic query, prior turns, "
                    "current state, and Golden Set questions to identify facts already answered and whether the "
                    "current question is relevant. Return compact JSON only with keys answered_questions and "
                    "question_relevance. answered_questions is an array of {entry_id, question_id, branch_key, "
                    "confidence, evidence}. question_relevance is {entry_id, question_id, disposition, branch_key, "
                    "confidence, evidence, contextual_question_script}. disposition must be one of needed, "
                    "already_answered, not_relevant, needs_rephrasing, escalate_to_staff. Use only provided branch "
                    "keys. You may extract or assess relevance, but do not choose route letters, handoffs, resume "
                    "targets, next questions, or final recommendations. The deterministic backend owns routing."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "original_topic_query": original_query,
                        "latest_user_message": latest_content,
                        "conversation_turns": self.conversation_turn_summaries(messages),
                        "current_state": asdict(triage_state),
                        "pending_question": {
                            "entry_id": triage_state.pending_entry_id,
                            "question_id": triage_state.current_question_id,
                            "question": question,
                            "allowed_branch_keys": branch_keys,
                            "allowed_branch_labels": {key: label_from_branch_key(key) for key in branch_keys},
                        },
                        "entries": extraction_entries,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        topic_model = overrides.get("pbsg_initial_topic_model", self.chatgpt_model)
        topic_deployment = overrides.get("pbsg_initial_topic_deployment", self.chatgpt_deployment)
        try:
            completion = await cast(
                Awaitable[ChatCompletion],
                self.create_chat_completion(
                    topic_deployment,
                    topic_model,
                    support_messages,
                    overrides,
                    self.get_response_token_limit(topic_model, 350),
                    should_stream=False,
                    temperature=0.0,
                ),
            )
        except Exception:
            return [], None, None
        try:
            payload = json.loads(completion.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return [], None, None
        source_turn_index = len(messages) - 1 if messages else None
        facts = self.facts_from_structured_answers(
            payload.get("answered_questions"),
            latest_content,
            source_prefix="structured_context_extraction",
            source_turn_index=source_turn_index,
        )
        relevance = payload.get("question_relevance")
        if isinstance(relevance, dict):
            relevance_fact = self.relevance_fact_from_assessment(
                relevance, triage_state, latest_content, source_turn_index
            )
            if relevance_fact:
                facts.append(relevance_fact)
        else:
            relevance = None
        thought = self.format_thought_step_for_chatcompletion(
            title="Structured PBSG context support",
            messages=support_messages,
            overrides=overrides,
            model=topic_model,
            deployment=topic_deployment,
            usage=completion.usage,
        )
        return facts, relevance, thought

    def should_run_context_support(self, triage_state: Any, turn_classification: PBSGTurnClassification) -> bool:
        if triage_state.pending_entry_id in {"GEN3-T06", "GEN3-T13"}:
            return True
        if triage_state.parent_workflow or triage_state.triggered_overlays or triage_state.concurrent_monitors:
            return True
        return turn_classification.should_call_llm

    async def try_contextual_locked_response(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if not self.openai_client or not messages:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
        recap_response = maybe_resolved_recap(self.pbsg_golden_set_entries, triage_state, session_state)
        if recap_response:
            return recap_response
        if triage_state.mode != "FAST_ROUTING" or not triage_state.pending_entry_id or not triage_state.current_question_id:
            return None
        turn_classification = classify_turn_interrupt(self.pbsg_golden_set_entries, triage_state, latest_content)
        if not self.should_run_context_support(triage_state, turn_classification):
            return None
        extracted_facts, relevance, extraction_thought = await self.extract_structured_context_support(
            messages, triage_state, latest_content, overrides
        )
        if not extracted_facts and relevance:
            response = self.build_contextual_relevance_response(triage_state, relevance, session_state)
            if response:
                if extraction_thought:
                    response["context"]["thoughts"].append(extraction_thought)
                return response
        if not extracted_facts:
            return None
        deterministic_result = self.pbsg_routing_engine.execute_locked_turn(
            messages[:-1], latest_content, extracted_facts=extracted_facts
        )
        if not deterministic_result:
            return None
        response = self.build_deterministic_chat_response(deterministic_result, session_state, turn_classification)
        if extraction_thought:
            response["context"]["thoughts"].append(extraction_thought)
        if relevance:
            response["context"]["pbsg_question_relevance"] = relevance
        return response

    def build_contextual_relevance_response(
        self,
        triage_state: Any,
        relevance: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        disposition = relevance.get("disposition")
        if disposition == "needs_rephrasing":
            script = relevance.get("contextual_question_script")
            entry_id = relevance.get("entry_id") if isinstance(relevance.get("entry_id"), str) else triage_state.pending_entry_id
            question_id = (
                relevance.get("question_id") if isinstance(relevance.get("question_id"), str) else triage_state.current_question_id
            )
            if not isinstance(script, str) or not entry_id or not question_id:
                return None
            canonical_question = self.pbsg_routing_engine.question_text(entry_id, question_id)
            if not canonical_question:
                return None
            content_lines = selected_stream_lines(self.pbsg_golden_set_entries, entry_id, entry_id, question_id)
            content_lines.extend(
                [
                    "",
                    "**Note:** I am asking the same Golden Set question in context-specific wording. The route options are unchanged.",
                    "",
                    "Triage progress:",
                    "",
                    f"<!-- Current question remains: {question_id} from {entry_id} -->",
                    f"- Current question remains about {short_question_label(self.pbsg_golden_set_entries, entry_id, question_id)}.",
                    "",
                    "**Ask the applicant (read verbatim):**",
                    "",
                    f'> **"{script}"**',
                    "",
                    "Type the applicant's answer here and I will determine the next question or route.",
                ]
            )
            content = "\n".join(content_lines)
        elif disposition in {"not_relevant", "escalate_to_staff"}:
            content = safe_escalation_response(
                pbsg_state_marker(triage_state.pending_entry_id), self.pbsg_golden_set_entries, "context relevance"
            )
        else:
            return None

        data_points = self.golden_set_data_points(self.pbsg_golden_set_entries)
        extra_info = ExtraInfo(data_points=data_points)
        extra_info.quick_reply = self.build_quick_reply(content, extra_info)
        extra_info.thoughts.append(
            ThoughtStep(
                "Structured PBSG context relevance",
                "Handled the current question using validated contextual relevance without changing the Golden Set route graph.",
                {"question_relevance": relevance},
            )
        )
        response_context = self.response_context_with_case_summary(
            {
                "thoughts": extra_info.thoughts,
                "data_points": {key: value for key, value in asdict(data_points).items() if value is not None},
                "followup_questions": None,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(triage_state),
                "pbsg_question_relevance": relevance,
            },
            triage_state,
        )
        response_context = context_with_triage_memory(response_context, triage_state)
        return {
            "message": {"content": apply_memory_notes(content, triage_state), "role": "assistant"},
            "context": response_context,
            "session_state": extend_session_state_with_memory(session_state, triage_state),
        }

    async def try_structured_llm_initial_topic_response(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if (
            not self.openai_client
            or len(messages) != 1
            or overrides.get("pbsg_initial_topic_classifier") is False
        ):
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        if self.is_bare_initial_topic_message(latest_content):
            return None
        local_resolution = resolve_initial_topic(self.pbsg_golden_set_entries, latest_content)
        candidate_entries = self.initial_topic_classifier_entries()
        valid_entry_ids = {entry["id"] for entry in candidate_entries if isinstance(entry.get("id"), str)}
        if not local_resolution or not candidate_entries:
            return None

        classifier_messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "You are the initial PBSG triage topic router. Classify the intern's first message into "
                    "Golden Set topic ids using semantic understanding, not keyword matching alone. Return compact "
                    "JSON only with keys primary_entry_id, queued_entry_ids, monitor_entry_ids, confidence, evidence. "
                    "Use only provided entry ids. Choose queued_entry_ids for separate legal issues that should be "
                    "handled after the primary workflow. Choose monitor_entry_ids only for urgency, safety, or "
                    "vulnerability overlays. Prioritize GEN3-T02 criminal before GEN3-T03 matrimonial/family, then "
                    "GEN3-T04 civil, unless first-contact gating is the only clear fit. Treat criminal/process facts "
                    "as GEN3-T02 even if the intern uses lay wording instead of legal labels. Examples include theft, "
                    "stealing, shoplifting, assault, drugs, vaping or e-vaporiser offences, being caught by law, being "
                    "caught by police, police trouble, arrest, investigation, summons, charge, prosecution, court case, "
                    "trial, plea, bail, remand, sentence, jail, or having to go to court/trial. Interpret separation "
                    "from a spouse, divorce, custody, maintenance, family court, PPO, protection order, family violence, "
                    "domestic violence, or wanting to end a marriage/relationship as GEN3-T03 matrimonial/family signals. "
                    "If one narrative contains both a criminal/process issue and a matrimonial/family issue, choose "
                    "GEN3-T02 as primary and queue GEN3-T03 unless the only criminal content is clearly irrelevant. "
                    "Add GEN3-T06 as a monitor only when the facts indicate immediate safety risk, basic-needs risk, "
                    "or a concrete deadline/court date within 14 days. Add GEN3-T13 as a monitor only when vulnerability "
                    "or adapted handling is suggested. Do not choose route letters, questions, handoffs, eligibility "
                    "outcomes, or final recommendations."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "latest_user_message": latest_content,
                        "deterministic_resolution_hint": asdict(local_resolution),
                        "golden_set_entries": candidate_entries,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        topic_model = overrides.get("pbsg_initial_topic_model", self.chatgpt_model)
        topic_deployment = overrides.get("pbsg_initial_topic_deployment", self.chatgpt_deployment)
        try:
            completion = await cast(
                Awaitable[ChatCompletion],
                self.create_chat_completion(
                    topic_deployment,
                    topic_model,
                    classifier_messages,
                    overrides,
                    self.get_response_token_limit(topic_model, 200),
                    should_stream=False,
                    temperature=0.0,
                ),
            )
        except Exception:
            return None
        try:
            payload = json.loads(completion.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return None

        primary_entry_id = payload.get("primary_entry_id")
        confidence = payload.get("confidence")
        if (
            not isinstance(primary_entry_id, str)
            or primary_entry_id not in valid_entry_ids
            or not isinstance(confidence, (int, float))
            or confidence < 0.7
        ):
            return None

        queued_topics: list[PBSGQueuedTopic] = []
        raw_queued = payload.get("queued_entry_ids")
        if isinstance(raw_queued, list):
            for entry_id in raw_queued:
                if isinstance(entry_id, str) and entry_id in valid_entry_ids and entry_id != primary_entry_id:
                    queued_topics.append(
                        PBSGQueuedTopic(entry_id=entry_id, evidence="structured topic classifier", confidence=float(confidence))
                    )
        raw_monitors = payload.get("monitor_entry_ids")
        overlays = list(local_resolution.overlays)
        if isinstance(raw_monitors, list):
            for entry_id in raw_monitors:
                if (
                    isinstance(entry_id, str)
                    and entry_id in self.pbsg_golden_set_entries
                    and entry_id != primary_entry_id
                    and entry_id not in overlays
                ):
                    overlays.append(entry_id)

        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), str) else "structured topic classifier"
        evidence = evidence.strip()
        if evidence.lower() == "structured topic classifier":
            classifier_reason = evidence
        else:
            classifier_reason = f"structured topic classifier: {evidence}"

        substantive_entry_ids = {"GEN3-T02", "GEN3-T03", "GEN3-T04"}
        effective_entry_id = primary_entry_id
        effective_queued_topics = queued_topics
        predicted_substantive_topics: list[PBSGQueuedTopic] = []
        if primary_entry_id in substantive_entry_ids:
            predicted_substantive_topics.append(
                PBSGQueuedTopic(
                    entry_id=primary_entry_id,
                    evidence=evidence or "structured topic classifier",
                    confidence=float(confidence),
                )
            )
        for topic in queued_topics:
            if topic.entry_id in substantive_entry_ids and topic.entry_id not in {
                existing.entry_id for existing in predicted_substantive_topics
            }:
                predicted_substantive_topics.append(topic)

        if "GEN3-T06" in overlays:
            effective_entry_id = "GEN3-T06"
            effective_queued_topics = predicted_substantive_topics
        elif "GEN3-T13" in overlays and predicted_substantive_topics:
            effective_entry_id = "GEN3-T13"
            effective_queued_topics = predicted_substantive_topics
        elif predicted_substantive_topics and "GEN3-T01" in self.pbsg_golden_set_entries:
            effective_entry_id = "GEN3-T01"
            effective_queued_topics = predicted_substantive_topics

        resolution = PBSGTopicResolution(
            entry_id=effective_entry_id,
            confidence=float(confidence),
            reason=classifier_reason,
            overlays=overlays,
            queued_topics=effective_queued_topics,
            candidates=local_resolution.candidates,
        )
        extracted_facts, extraction_thought = await self.extract_structured_initial_facts(
            latest_content, resolution, overrides
        )
        deterministic_result = self.pbsg_routing_engine.execute_initial_resolution(
            latest_content, resolution, extracted_facts=extracted_facts
        )
        if not deterministic_result:
            return None
        response = self.build_deterministic_chat_response(deterministic_result, session_state)
        response["context"]["thoughts"].append(
            self.format_thought_step_for_chatcompletion(
                title="Structured PBSG initial topic classifier",
                messages=classifier_messages,
                overrides=overrides,
                model=topic_model,
                deployment=topic_deployment,
                usage=completion.usage,
            )
        )
        if extraction_thought:
            response["context"]["thoughts"].append(extraction_thought)
        return response

    def general_enquiry_match(self, latest_content: str) -> tuple[str, dict[str, Any]] | None:
        normalized = latest_content.lower()
        if not PBSG_GENERAL_ENQUIRY_INTENT_PATTERN.search(normalized):
            return None
        for faq_key, faq in PBSG_GENERAL_ENQUIRY_FAQS.items():
            patterns = faq.get("patterns", [])
            if any(re.search(pattern, normalized) for pattern in patterns if isinstance(pattern, str)):
                return faq_key, faq
        return None

    def is_legal_triage_request(self, latest_content: str) -> bool:
        return bool(PBSG_TRIAGE_REQUEST_PATTERN.search(latest_content))

    def general_enquiry_answer(self, latest_content: str) -> tuple[str, list[str], str] | None:
        match = self.general_enquiry_match(latest_content)
        if not match:
            return None
        faq_key, faq = match
        answer = faq.get("answer")
        if not isinstance(answer, str):
            return None
        source_ids = [entry_id for entry_id in faq.get("source_ids", []) if entry_id in self.pbsg_golden_set_entries]
        return answer, source_ids, faq_key

    def build_general_enquiry_response(
        self,
        latest_content: str,
        *,
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        answer_result = self.general_enquiry_answer(latest_content)
        if not answer_result:
            return None
        answer, source_ids, faq_key = answer_result

        source_entries = {
            entry_id: self.pbsg_golden_set_entries[entry_id]
            for entry_id in source_ids
            if entry_id in self.pbsg_golden_set_entries
        }
        data_points = self.golden_set_data_points(source_entries) if source_entries else DataPoints()
        content = "\n\n".join(
            [
                "**General enquiry:**",
                answer,
                (
                    "If the applicant wants to check which pathway applies to their situation, please briefly describe "
                    "the legal issue and I will start triage."
                ),
            ]
        )
        thoughts = [
            ThoughtStep(
                "Deterministic PBSG general enquiry",
                "Answered a pure organisational or scheme question from the curated local FAQ without retrieval or LLM generation.",
                {"faq_key": faq_key, "source_ids": source_ids},
            )
        ]
        return {
            "message": {"content": content, "role": "assistant"},
            "context": {
                "thoughts": thoughts,
                "data_points": {key: value for key, value in asdict(data_points).items() if value is not None},
                "followup_questions": None,
                "quick_reply": None,
            },
            "session_state": session_state,
        }

    def try_initial_general_enquiry_response(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if len(messages) != 1:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str) or self.is_legal_triage_request(latest_content):
            return None
        return self.build_general_enquiry_response(latest_content, session_state=session_state)

    def mixed_general_enquiry_prefix(self, messages: list[ChatCompletionMessageParam]) -> str | None:
        if len(messages) != 1:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str) or not self.is_legal_triage_request(latest_content):
            return None
        answer_result = self.general_enquiry_answer(latest_content)
        if not answer_result:
            return None
        answer, _, _ = answer_result
        return "\n\n".join(
            [
                "**General enquiry:**",
                answer,
                "**Now I will triage the legal issue:**",
            ]
        )

    def with_general_enquiry_prefix(self, response: dict[str, Any], prefix: str | None) -> dict[str, Any]:
        if not prefix:
            return response
        message = response.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message["content"] = f"{prefix}\n\n{message['content']}"
        context = response.get("context")
        if isinstance(context, dict):
            thoughts = context.get("thoughts")
            if isinstance(thoughts, list):
                thoughts.insert(
                    0,
                    ThoughtStep(
                        "Deterministic PBSG mixed general enquiry",
                        "Answered the general part from the curated local FAQ before continuing legal triage.",
                        None,
                    ),
                )
        return response

    def local_side_enquiry_answer(self, latest_content: str) -> str | None:
        answer_result = self.general_enquiry_answer(latest_content)
        if not answer_result:
            return None
        answer, _, _ = answer_result
        return answer

    def general_enquiry_interrupt_answer(self, latest_content: str, triage_state: Any = None) -> tuple[str, list[str], str] | None:
        answer_result = self.general_enquiry_answer(latest_content)
        if not answer_result:
            return None
        if not is_general_enquiry_interrupt(latest_content):
            return None
        normalized = re.sub(r"\s+", " ", latest_content).strip().lower()
        if triage_state and getattr(triage_state, "pending_entry_id", None) and getattr(triage_state, "current_question_id", None):
            if normalize_branch_label(label_from_branch_key("if_not_sure")) == normalize_branch_label(normalized):
                return None
        return answer_result

    def build_general_enquiry_interrupt_response(
        self,
        latest_content: str,
        triage_state: Any,
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        answer_result = self.general_enquiry_interrupt_answer(latest_content, triage_state)
        if not answer_result:
            return None
        answer, source_ids, faq_key = answer_result
        source_entries = {
            entry_id: self.pbsg_golden_set_entries[entry_id]
            for entry_id in source_ids
            if entry_id in self.pbsg_golden_set_entries
        }
        data_points = self.golden_set_data_points(source_entries) if source_entries else DataPoints()

        if getattr(triage_state, "pending_entry_id", None) and getattr(triage_state, "current_question_id", None):
            triage_state.active_side_enquiry = PBSGInterruption(
                question=latest_content,
                parent_workflow=triage_state.pending_entry_id,
                parent_question_id=triage_state.current_question_id,
            )
            triage_state.interruption_stack.append(triage_state.active_side_enquiry)
            turn_classification = PBSGTurnClassification(
                turn_type="current_topic_side_question",
                should_call_llm=False,
                reason=answer,
            )
            response = self.build_pending_question_response(
                triage_state,
                turn_classification,
                "I answered the general enquiry and preserved the current triage question.",
                session_state,
            )
            response["context"]["thoughts"].insert(
                0,
                ThoughtStep(
                    "Deterministic PBSG general enquiry interrupt",
                    "Answered a glossary or process question without advancing the active triage workflow.",
                    {"faq_key": faq_key, "source_ids": source_ids},
                ),
            )
            response["context"]["data_points"] = {
                key: value for key, value in asdict(data_points).items() if value is not None
            }
            return response

        if getattr(triage_state, "routing_completion_status", None) == "awaiting_topic_resolution" and getattr(
            triage_state, "queued_workflows", None
        ):
            hold_response = self.build_awaiting_topic_resolution_response(triage_state, session_state)
            hold_content = hold_response["message"]["content"]
            prefix = "\n\n".join(["**General enquiry:**", answer])
            hold_response["message"]["content"] = f"{prefix}\n\n{hold_content}"
            hold_response["context"]["thoughts"].insert(
                0,
                ThoughtStep(
                    "Deterministic PBSG general enquiry interrupt",
                    "Answered a glossary or process question and preserved the queued-topic hold state.",
                    {"faq_key": faq_key, "source_ids": source_ids},
                ),
            )
            hold_response["context"]["data_points"] = {
                key: value for key, value in asdict(data_points).items() if value is not None
            }
            return hold_response

        return None

    def try_general_enquiry_interrupt_response(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if not messages:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        if len(messages) == 1:
            return None
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
        return self.build_general_enquiry_interrupt_response(latest_content, triage_state, session_state)

    def try_local_side_enquiry_response(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if not messages:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
        if self.general_enquiry_interrupt_answer(latest_content, triage_state):
            return None
        if triage_state.mode != "FAST_ROUTING" or not triage_state.pending_entry_id or not triage_state.current_question_id:
            return None
        answer = self.local_side_enquiry_answer(latest_content)
        if not answer:
            return None
        triage_state.active_side_enquiry = PBSGInterruption(
            question=latest_content,
            parent_workflow=triage_state.pending_entry_id,
            parent_question_id=triage_state.current_question_id,
        )
        triage_state.interruption_stack.append(triage_state.active_side_enquiry)
        turn_classification = PBSGTurnClassification(
            turn_type="clarification",
            should_call_llm=False,
            reason=answer,
        )
        return self.build_pending_question_response(
            triage_state,
            turn_classification,
            "I answered the side question and preserved the current routing point.",
            session_state,
        )

    def parse_continue_queued_workflow(self, latest_content: str, triage_state: Any = None) -> str | None:
        match = re.search(r"\bContinue queued workflow:\s*(GEN3-[A-Z0-9-]+)\b", latest_content, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
        if "continue queued workflow" not in latest_content.lower():
            return None
        queued_workflows = getattr(triage_state, "queued_workflows", None) or []
        if not queued_workflows:
            return None
        normalized = re.sub(r"\s+", " ", latest_content).strip().lower()
        for workflow_id in queued_workflows:
            entry = self.pbsg_golden_set_entries.get(workflow_id, {})
            stream_name = stream_display_name(self.pbsg_golden_set_entries, workflow_id).lower()
            topic = entry.get("topic") if isinstance(entry, dict) else None
            topic_text = topic.lower() if isinstance(topic, str) else ""
            if workflow_id.lower() in normalized or stream_name in normalized or (topic_text and topic_text in normalized):
                return workflow_id
        return queued_workflows[0]

    def is_route_completion_acknowledgement(self, latest_content: str) -> bool:
        normalized = re.sub(r"[^a-z0-9\s]", " ", latest_content.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized in {"ok", "okay", "yes", "done", "resolved", "thanks", "thank you", "next"}

    def build_awaiting_topic_resolution_response(
        self,
        triage_state: Any,
        session_state: Any = None,
    ) -> dict[str, Any]:
        active_workflow = triage_state.workflow_id or triage_state.active_workflow or "Current topic"
        next_workflow = triage_state.queued_workflows[0] if triage_state.queued_workflows else None
        next_entry = self.pbsg_golden_set_entries.get(next_workflow or "", {})
        next_topic = next_entry.get("topic") if isinstance(next_entry, dict) else next_workflow
        active_stream = stream_display_name(self.pbsg_golden_set_entries, active_workflow)
        next_stream = stream_display_name(self.pbsg_golden_set_entries, next_workflow)
        content = "\n".join(
            [
                pbsg_state_marker(
                    active_workflow if active_workflow in self.pbsg_golden_set_entries else None,
                    queued_workflows=triage_state.queued_workflows,
                ),
                f"**Selected Stream:** {active_stream}",
                "",
                f"**Note:** The {active_stream} has been routed. I will not start the queued topic until you confirm this topic is resolved.",
                "",
                "**Queued topic ready:**",
                "",
                f"- {next_stream}: {next_topic}",
                "- Click the button when you are ready to continue.",
                "",
                "Topics identified:",
                f"1. {active_stream} - routed workflow",
                f"2. {next_stream} - queued workflow",
            ]
        )
        data_points = self.golden_set_data_points(self.pbsg_golden_set_entries)
        extra_info = ExtraInfo(data_points=data_points)
        extra_info.quick_reply = self.build_continue_queued_topic_quick_reply(triage_state)
        extra_info.thoughts.append(
            ThoughtStep(
                "Deterministic PBSG queued topic hold",
                "Preserved the queued workflow and waited for explicit intern confirmation before continuing.",
                {
                    "routing_completion_status": triage_state.routing_completion_status,
                    "queued_workflows": triage_state.queued_workflows,
                },
            )
        )
        response_context = self.response_context_with_case_summary(
            {
                "thoughts": extra_info.thoughts,
                "data_points": {key: value for key, value in asdict(data_points).items() if value is not None},
                "followup_questions": None,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(triage_state),
            },
            triage_state,
        )
        response_context = context_with_triage_memory(response_context, triage_state)
        return {
            "message": {"content": apply_memory_notes(content, triage_state), "role": "assistant"},
            "context": response_context,
            "session_state": extend_session_state_with_memory(session_state, triage_state),
        }

    def try_queued_topic_control_response(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if not messages:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
        recap_response = maybe_resolved_recap(self.pbsg_golden_set_entries, triage_state, session_state)
        if recap_response:
            return recap_response
        if triage_state.routing_completion_status != "awaiting_topic_resolution" or not triage_state.queued_workflows:
            return None
        workflow_to_continue = self.parse_continue_queued_workflow(latest_content, triage_state)
        if workflow_to_continue:
            deterministic_result = self.pbsg_routing_engine.start_queued_workflow(messages[:-1], workflow_to_continue)
            if deterministic_result:
                return self.build_deterministic_chat_response(deterministic_result, session_state)
            return None
        return self.build_awaiting_topic_resolution_response(triage_state, session_state)

    def first_missing_prerequisite_question(
        self,
        entry_id: str,
        target_question_id: str,
        triage_state: Any,
    ) -> str | None:
        match = re.fullmatch(r"Q(\d+)([A-Z]?)", target_question_id.upper())
        if not match:
            return None
        target_number = int(match.group(1))
        if target_number <= 1:
            return None
        entry = self.pbsg_golden_set_entries.get(entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        if not isinstance(branching_logic, dict):
            return None
        for question_number in range(1, target_number):
            question_id = f"Q{question_number}"
            if question_id not in branching_logic:
                continue
            if not user_fact_for_question(self.pbsg_golden_set_entries, triage_state.fact_ledger, entry_id, question_id):
                return question_id
        return None

    def repair_unvalidated_prerequisite_skip(
        self,
        content: Optional[str],
        messages: list[ChatCompletionMessageParam],
        latest_content: str,
    ) -> Optional[str]:
        if not content:
            return content
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        marker = parse_pbsg_state_marker(content)
        selected_entry_id = marker.get("selected_entry")
        if not selected_entry_id:
            selected_entry_matches = re.findall(r"\*\*Selected Entry:\*\*\s*(GEN3-[A-Z0-9-]+)", content, flags=re.IGNORECASE)
            selected_entry_id = selected_entry_matches[-1].upper() if selected_entry_matches else None
        if not selected_entry_id or selected_entry_id not in self.pbsg_golden_set_entries:
            return content
        target_question_id = marker.get("pending_question")
        if not target_question_id:
            targets = re.findall(r"Next question:\s*(Q\d+[A-Z]?)\b|>\s*\**(Q\d+[A-Z]?)\s*:", content, flags=re.IGNORECASE)
            for first_match, second_match in targets:
                target_question_id = (first_match or second_match).upper()
        if not target_question_id:
            return content
        missing_question_id = self.first_missing_prerequisite_question(selected_entry_id, target_question_id, triage_state)
        if not missing_question_id:
            return content
        question = self.pbsg_routing_engine.question_text(selected_entry_id, missing_question_id)
        if not question:
            return content
        lines = selected_stream_lines(self.pbsg_golden_set_entries, selected_entry_id, selected_entry_id, missing_question_id)
        lines.extend(
            [
                "",
                "**Note:** I cannot rely on unverified answers for this workflow. We need to ask the first unanswered required question.",
                "",
                "Triage progress:",
                "",
                f"- We are returning to the required question about {short_question_label(self.pbsg_golden_set_entries, selected_entry_id, missing_question_id)}.",
            ]
        )
        lines.extend(next_question_lines(self.pbsg_golden_set_entries, selected_entry_id, missing_question_id, question))
        return "\n".join(lines)

    def repair_hardship_rejection(
        self,
        content: Optional[str],
        messages: list[ChatCompletionMessageParam],
        latest_content: str,
    ) -> Optional[str]:
        marker = parse_pbsg_state_marker(content)
        selected_entry_id = marker.get("selected_entry")
        if not content or ((selected_entry_id or "") != "GEN3-T04" and "**Selected Entry:** GEN3-T04" not in content) or "Route D" not in content:
            return content
        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        has_hardship = any(
            fact.status == "active"
            and fact.fact_key in {"applicant.financial_hardship", "applicant.income_status", "applicant.means_status"}
            and fact.normalized_value in {"true", "no_income", "marginal_or_exceptional"}
            for fact in triage_state.fact_ledger
        )
        if not has_hardship:
            return content
        transition = self.pbsg_routing_engine.graph.transition_for(
            "GEN3-T04", "Q4", "if_no_marginal_or_exceptional"
        )
        if not transition:
            return content
        repaired = self.pbsg_routing_engine.render_transition(transition)
        if not repaired:
            return content
        return repaired + "\n\n**Repair note:** I treated the no-income hardship information as a possible exceptional circumstance, so this must not be rejected as well-over/no-exceptions."

    async def try_structured_llm_locked_response(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any] | None:
        if not self.openai_client or not messages:
            return None
        latest_content = messages[-1].get("content")
        if not isinstance(latest_content, str):
            return None

        triage_state = build_triage_state(messages[:-1], self.pbsg_golden_set_entries, latest_content)
        triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
        recap_response = maybe_resolved_recap(self.pbsg_golden_set_entries, triage_state, session_state)
        if recap_response:
            return recap_response
        if triage_state.mode != "FAST_ROUTING" or not triage_state.pending_entry_id or not triage_state.current_question_id:
            return None
        entry = self.pbsg_golden_set_entries.get(triage_state.pending_entry_id)
        branching_logic = entry.get("branching_logic") if entry else None
        question_node = (
            branching_logic.get(triage_state.current_question_id) if isinstance(branching_logic, dict) else None
        )
        if not isinstance(question_node, dict):
            return None
        branch_keys = [key for key in question_node if key.startswith("if_")]
        if not branch_keys:
            return None
        local_classification = classify_turn_interrupt(self.pbsg_golden_set_entries, triage_state, latest_content)

        classifier_messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "Classify a locked PBSG triage turn. Return compact JSON only. "
                    "Schema: turn_type is one of answer_only, answer_plus_new_topic, new_topic_only, "
                    "correction, clarification, safety_interrupt, ambiguous. pending_answer is either null "
                    "or {branch_key, confidence}. new_topics is a list of {entry_id, evidence, confidence}. "
                    "correction is {affects_prior_answer, reason}. clarification_answer may be a short plain-language "
                    "answer if the user asked a clarification question. Only choose branch_key from allowed branches "
                    "and entry_id from available entries. Do not choose the next question, route letter, resume target, "
                    "or final recommendation. The backend deterministic workflow graph owns all route execution. "
                    "Do not write user-facing routing advice."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "entry_id": triage_state.pending_entry_id,
                        "question_id": triage_state.current_question_id,
                        "question": question_node.get("question"),
                        "allowed_branch_keys": branch_keys,
                        "allowed_branch_labels": {key: label_from_branch_key(key) for key in branch_keys},
                        "available_entries": sorted(self.pbsg_golden_set_entries),
                        "local_gate_turn_type": local_classification.turn_type,
                        "local_gate_new_topics": [asdict(topic) for topic in local_classification.new_topics],
                        "latest_user_message": latest_content,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        completion = await cast(
            Awaitable[ChatCompletion],
            self.create_chat_completion(
                self.chatgpt_deployment,
                self.chatgpt_model,
                classifier_messages,
                overrides,
                self.get_response_token_limit(self.chatgpt_model, 200),
                should_stream=False,
                temperature=0.0,
            ),
        )
        classifier_content = completion.choices[0].message.content or "{}"
        turn_classification = self.parse_structured_turn_classification(
            classifier_content, branch_keys, local_classification
        )
        if not turn_classification:
            return None

        if turn_classification.turn_type == "correction":
            response = self.build_pending_question_response(
                triage_state,
                turn_classification,
                "I noted a correction. To avoid routing on the wrong facts, please confirm the corrected answer to the current question before we continue.",
                session_state,
            )
            response["context"]["thoughts"].append(
                self.format_thought_step_for_chatcompletion(
                    title="Structured PBSG turn switch",
                    messages=classifier_messages,
                    overrides=overrides,
                    model=self.chatgpt_model,
                    deployment=self.chatgpt_deployment,
                    usage=completion.usage,
                )
            )
            return response

        if turn_classification.turn_type in {"new_topic_only", "clarification", "ambiguous"}:
            if turn_classification.turn_type == "clarification":
                note = "I noted the clarification question. Please answer the current triage question so I can continue safely."
            elif turn_classification.turn_type == "new_topic_only":
                note = "I noted a separate possible topic and will handle it after this stream is routed."
            else:
                note = "I could not map that safely to one allowed branch. Please answer the current triage question directly."
            response = self.build_pending_question_response(triage_state, turn_classification, note, session_state)
            response["context"]["thoughts"].append(
                self.format_thought_step_for_chatcompletion(
                    title="Structured PBSG turn switch",
                    messages=classifier_messages,
                    overrides=overrides,
                    model=self.chatgpt_model,
                    deployment=self.chatgpt_deployment,
                    usage=completion.usage,
                )
            )
            return response

        branch_key = turn_classification.pending_branch_key
        confidence = turn_classification.pending_answer_confidence
        if branch_key not in branch_keys or not confidence or confidence < 0.75:
            return None
        deterministic_result = self.pbsg_routing_engine.execute_locked_turn(
            messages[:-1], latest_content, branch_key_override=branch_key
        )
        if not deterministic_result:
            return None
        response = self.build_deterministic_chat_response(deterministic_result, session_state, turn_classification)
        response["context"]["thoughts"].append(
            self.format_thought_step_for_chatcompletion(
                title="Structured PBSG turn switch",
                messages=classifier_messages,
                overrides=overrides,
                model=self.chatgpt_model,
                deployment=self.chatgpt_deployment,
                usage=completion.usage,
            )
        )
        return response

    def parse_structured_turn_classification(
        self,
        classifier_content: str,
        branch_keys: list[str],
        local_classification: PBSGTurnClassification,
    ) -> PBSGTurnClassification | None:
        try:
            payload = json.loads(classifier_content)
        except json.JSONDecodeError:
            return None

        # Backward-compatible shape for older tests/fallbacks.
        if payload.get("classification") == "branch":
            branch_key = payload.get("branch_key")
            confidence = payload.get("confidence")
            if branch_key in branch_keys and isinstance(confidence, (int, float)):
                return PBSGTurnClassification(
                    turn_type="answer_only",
                    should_call_llm=True,
                    pending_branch_key=branch_key,
                    pending_answer_confidence=float(confidence),
                    new_topics=local_classification.new_topics,
                )
            return None

        turn_type = payload.get("turn_type")
        if turn_type not in {
            "answer_only",
            "answer_plus_new_topic",
            "new_topic_only",
            "correction",
            "clarification",
            "safety_interrupt",
            "ambiguous",
        }:
            return None

        pending_answer = payload.get("pending_answer")
        branch_key = None
        confidence = None
        if isinstance(pending_answer, dict):
            candidate_branch = pending_answer.get("branch_key")
            candidate_confidence = pending_answer.get("confidence")
            if candidate_branch in branch_keys and isinstance(candidate_confidence, (int, float)):
                branch_key = candidate_branch
                confidence = float(candidate_confidence)

        topics = self.validated_queued_topics(payload.get("new_topics"), local_classification.new_topics)
        correction = payload.get("correction")
        affects_prior_answer = (
            bool(correction.get("affects_prior_answer")) if isinstance(correction, dict) else local_classification.affects_prior_answer
        )
        return PBSGTurnClassification(
            turn_type=turn_type,
            should_call_llm=True,
            reason=payload.get("clarification_answer") if isinstance(payload.get("clarification_answer"), str) else None,
            pending_branch_key=branch_key,
            pending_answer_confidence=confidence,
            new_topics=topics,
            affects_prior_answer=affects_prior_answer,
        )

    def validated_queued_topics(
        self,
        raw_topics: Any,
        local_topics: list[PBSGQueuedTopic],
    ) -> list[PBSGQueuedTopic]:
        topics: list[PBSGQueuedTopic] = []
        if isinstance(raw_topics, list):
            for raw_topic in raw_topics:
                if not isinstance(raw_topic, dict):
                    continue
                entry_id = raw_topic.get("entry_id")
                evidence = raw_topic.get("evidence")
                confidence = raw_topic.get("confidence")
                if (
                    isinstance(entry_id, str)
                    and entry_id in self.pbsg_golden_set_entries
                    and isinstance(evidence, str)
                    and isinstance(confidence, (int, float))
                    and confidence >= 0.7
                ):
                    topics.append(PBSGQueuedTopic(entry_id=entry_id, evidence=evidence, confidence=float(confidence)))
        for local_topic in local_topics:
            if local_topic.entry_id not in [topic.entry_id for topic in topics]:
                topics.append(local_topic)
        return topics

    def build_pending_question_response(
        self,
        triage_state: Any,
        turn_classification: PBSGTurnClassification,
        note: str,
        session_state: Any = None,
    ) -> dict[str, Any]:
        entry_id = triage_state.pending_entry_id or triage_state.workflow_id or "Unclear"
        question_id = triage_state.current_question_id
        question = self.pbsg_routing_engine.question_text(entry_id, question_id) if question_id else None
        for topic in turn_classification.new_topics:
            if topic.entry_id not in triage_state.queued_workflows:
                triage_state.queued_workflows.append(topic.entry_id)
        content_lines = selected_stream_lines(
            self.pbsg_golden_set_entries,
            entry_id,
            entry_id if question_id else None,
            question_id,
        )
        content_lines.extend(["", f"**Note:** {note}"])
        if turn_classification.reason and turn_classification.turn_type == "clarification":
            content_lines.extend(["", "**Clarification:**", "", turn_classification.reason])
        elif turn_classification.reason and turn_classification.turn_type == "current_topic_side_question":
            content_lines.extend(["", "**General enquiry:**", "", turn_classification.reason])
        if turn_classification.new_topics:
            content_lines.extend(
                [
                    "",
                    "**Queued topic note:** I noted a separate possible topic and will handle it after this stream is routed.",
                    "",
                    "Topics identified:",
                    f"1. {stream_display_name(self.pbsg_golden_set_entries, triage_state.active_workflow or entry_id)} - active workflow",
                ]
            )
            content_lines.extend(
                f"{index}. {stream_display_name(self.pbsg_golden_set_entries, topic.entry_id)} - queued workflow (noted from: {topic.evidence})"
                for index, topic in enumerate(turn_classification.new_topics, start=2)
            )
        if question_id and question:
            content_lines.extend(
                [
                    "",
                    "Triage progress:",
                    "",
                    f"<!-- Current question remains: {question_id} from {entry_id} -->",
                    f"- Current question remains about {short_question_label(self.pbsg_golden_set_entries, entry_id, question_id)}.",
                ]
            )
            content_lines.extend(next_question_lines(self.pbsg_golden_set_entries, entry_id, question_id, question))
        data_points = self.golden_set_data_points(self.pbsg_golden_set_entries)
        extra_info = ExtraInfo(data_points=data_points)
        content = "\n".join(content_lines)
        extra_info.quick_reply = self.build_quick_reply(content, extra_info)
        extra_info.thoughts.append(
            ThoughtStep(
                "Structured PBSG turn switch",
                "Preserved the active triage state without advancing an unsafe route.",
                {"turn_type": turn_classification.turn_type},
            )
        )
        response_context = self.response_context_with_case_summary(
            {
                "thoughts": extra_info.thoughts,
                "data_points": {key: value for key, value in asdict(data_points).items() if value is not None},
                "followup_questions": None,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(triage_state),
            },
            triage_state,
        )
        response_context = context_with_triage_memory(response_context, triage_state)
        return {
            "message": {"content": apply_memory_notes(content, triage_state), "role": "assistant"},
            "context": response_context,
            "session_state": extend_session_state_with_memory(session_state, triage_state),
        }

    def get_search_query(self, chat_completion: ChatCompletion, default_query: str) -> str:
        """Read the optimized search query from a chat completion tool call."""
        try:
            return self.extract_rewritten_query(chat_completion, default_query, no_response_token=self.NO_RESPONSE)
        except Exception:
            return default_query

    def tokenize_for_overlap(self, text: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return {token for token in tokens if token not in COMMON_QUERY_TERMS and len(token) > 1}

    def rewrite_looks_drifted(self, original_user_query: str, rewritten_query: str) -> bool:
        original_tokens = self.tokenize_for_overlap(original_user_query)
        rewritten_tokens = self.tokenize_for_overlap(rewritten_query)
        if not original_tokens or not rewritten_tokens:
            return False
        overlap_ratio = len(original_tokens & rewritten_tokens) / len(original_tokens)
        return overlap_ratio < 0.2

    async def run_without_streaming(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ) -> dict[str, Any]:
        general_enquiry_response = self.try_initial_general_enquiry_response(messages, session_state)
        if general_enquiry_response:
            return general_enquiry_response
        mixed_general_prefix = self.mixed_general_enquiry_prefix(messages)
        structured_initial_response = await self.try_structured_llm_initial_topic_response(messages, overrides, session_state)
        if structured_initial_response:
            return self.with_general_enquiry_prefix(structured_initial_response, mixed_general_prefix)
        initial_response = self.try_deterministic_initial_response(messages, session_state)
        if initial_response:
            return self.with_general_enquiry_prefix(initial_response, mixed_general_prefix)
        queued_topic_response = self.try_queued_topic_control_response(messages, session_state)
        if queued_topic_response:
            return queued_topic_response
        general_enquiry_interrupt_response = self.try_general_enquiry_interrupt_response(messages, session_state)
        if general_enquiry_interrupt_response:
            return general_enquiry_interrupt_response
        contextual_response = await self.try_contextual_locked_response(messages, overrides, session_state)
        if contextual_response:
            return contextual_response
        deterministic_response = self.try_deterministic_locked_response(messages, session_state)
        if deterministic_response:
            return deterministic_response
        side_enquiry_response = self.try_local_side_enquiry_response(messages, session_state)
        if side_enquiry_response:
            return side_enquiry_response
        structured_response = await self.try_structured_llm_locked_response(messages, overrides, session_state)
        if structured_response:
            return structured_response

        extra_info, chat_coroutine = await self.run_until_final_call(
            messages, overrides, auth_claims, session_state=session_state, should_stream=False
        )
        chat_completion_response: ChatCompletion = await cast(Awaitable[ChatCompletion], chat_coroutine)
        content = chat_completion_response.choices[0].message.content
        role = chat_completion_response.choices[0].message.role
        if overrides.get("suggest_followup_questions"):
            content, followup_questions = self.extract_followup_questions(content)
            extra_info.followup_questions = followup_questions
        content = self.normalize_asked_question_text(content, extra_info)
        content = collapse_duplicate_route_cards(content)
        content = self.apply_triage_response_guard(content, extra_info)
        latest_content = messages[-1].get("content") if messages else ""
        if isinstance(latest_content, str):
            content = self.repair_unvalidated_prerequisite_skip(content, messages, latest_content)
            content = self.repair_hardship_rejection(content, messages, latest_content)
        extra_info.quick_reply = self.build_quick_reply(content, extra_info)
        # Assume last thought is for generating answer
        # TODO: Update for agentic? This isn't still true?
        if self.include_token_usage and extra_info.thoughts and chat_completion_response.usage:
            extra_info.thoughts[-1].update_token_usage(chat_completion_response.usage)
        response_context = {
            "thoughts": extra_info.thoughts,
            "data_points": {
                key: value for key, value in asdict(extra_info.data_points).items() if value is not None
            },
            "followup_questions": extra_info.followup_questions,
            "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
        }
        if parse_pbsg_state_marker(content):
            triage_state = build_triage_state(
                [*messages, {"role": "assistant", "content": content}],
                self.pbsg_golden_set_entries,
                "",
            )
            triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
            response_context["pbsg_triage_state"] = asdict(triage_state)
            self.response_context_with_case_summary(response_context, triage_state)
            response_context = context_with_triage_memory(response_context, triage_state)
            session_state = extend_session_state_with_memory(session_state, triage_state)
            content = apply_memory_notes(content, triage_state)
        chat_app_response = {
            "message": {"content": content, "role": role},
            "context": response_context,
            "session_state": session_state,
        }
        return chat_app_response

    async def run_with_streaming(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ) -> AsyncGenerator[dict, None]:
        general_enquiry_response = self.try_initial_general_enquiry_response(messages, session_state)
        if general_enquiry_response:
            yield {"delta": {"role": "assistant"}, "context": general_enquiry_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": general_enquiry_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": general_enquiry_response["context"], "session_state": session_state}
            return
        mixed_general_prefix = self.mixed_general_enquiry_prefix(messages)
        structured_initial_response = await self.try_structured_llm_initial_topic_response(messages, overrides, session_state)
        if structured_initial_response:
            structured_initial_response = self.with_general_enquiry_prefix(structured_initial_response, mixed_general_prefix)
            yield {"delta": {"role": "assistant"}, "context": structured_initial_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": structured_initial_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": structured_initial_response["context"], "session_state": session_state}
            return
        initial_response = self.try_deterministic_initial_response(messages, session_state)
        if initial_response:
            initial_response = self.with_general_enquiry_prefix(initial_response, mixed_general_prefix)
            yield {"delta": {"role": "assistant"}, "context": initial_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": initial_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": initial_response["context"], "session_state": session_state}
            return
        queued_topic_response = self.try_queued_topic_control_response(messages, session_state)
        if queued_topic_response:
            yield {"delta": {"role": "assistant"}, "context": queued_topic_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": queued_topic_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": queued_topic_response["context"], "session_state": session_state}
            return
        general_enquiry_interrupt_response = self.try_general_enquiry_interrupt_response(messages, session_state)
        if general_enquiry_interrupt_response:
            yield {"delta": {"role": "assistant"}, "context": general_enquiry_interrupt_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": general_enquiry_interrupt_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": general_enquiry_interrupt_response["context"], "session_state": session_state}
            return
        contextual_response = await self.try_contextual_locked_response(messages, overrides, session_state)
        if contextual_response:
            yield {"delta": {"role": "assistant"}, "context": contextual_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": contextual_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": contextual_response["context"], "session_state": session_state}
            return
        deterministic_response = self.try_deterministic_locked_response(messages, session_state)
        if deterministic_response:
            yield {"delta": {"role": "assistant"}, "context": deterministic_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": deterministic_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": deterministic_response["context"], "session_state": session_state}
            return
        side_enquiry_response = self.try_local_side_enquiry_response(messages, session_state)
        if side_enquiry_response:
            yield {"delta": {"role": "assistant"}, "context": side_enquiry_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": side_enquiry_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": side_enquiry_response["context"], "session_state": session_state}
            return
        structured_response = await self.try_structured_llm_locked_response(messages, overrides, session_state)
        if structured_response:
            yield {"delta": {"role": "assistant"}, "context": structured_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": structured_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": structured_response["context"], "session_state": session_state}
            return

        extra_info, chat_coroutine = await self.run_until_final_call(
            messages, overrides, auth_claims, session_state=session_state, should_stream=True
        )
        buffer_pbsg_stream = bool(self.extract_golden_set_entries(extra_info.data_points.text))
        yield {"delta": {"role": "assistant"}, "context": extra_info, "session_state": session_state}

        followup_questions_started = False
        followup_content = ""
        chat_result = await chat_coroutine

        if isinstance(chat_result, ChatCompletion):
            message = chat_result.choices[0].message
            content = message.content or ""
            role = message.role or "assistant"

            followup_questions: list[str] = []
            if overrides.get("suggest_followup_questions"):
                content, followup_questions = self.extract_followup_questions(content)
                extra_info.followup_questions = followup_questions
            content = self.normalize_asked_question_text(content, extra_info)
            content = collapse_duplicate_route_cards(content)
            content = self.apply_triage_response_guard(content, extra_info)
            latest_content = messages[-1].get("content") if messages else ""
            if isinstance(latest_content, str):
                content = self.repair_unvalidated_prerequisite_skip(content, messages, latest_content)
                content = self.repair_hardship_rejection(content, messages, latest_content)
            extra_info.quick_reply = self.build_quick_reply(content, extra_info)

            if self.include_token_usage and extra_info.thoughts and chat_result.usage:
                extra_info.thoughts[-1].update_token_usage(chat_result.usage)

            delta_payload: dict[str, Any] = {"role": role}
            if content:
                delta_payload["content"] = content
            yield {"delta": delta_payload}

            yield {"delta": {"role": "assistant"}, "context": extra_info, "session_state": session_state}

            if followup_questions:
                yield {
                    "delta": {"role": "assistant"},
                    "context": {"context": extra_info, "followup_questions": followup_questions},
                }
            return

        chat_result = cast(AsyncStream[ChatCompletionChunk], chat_result)
        streamed_content = ""

        async for event_chunk in chat_result:
            # "2023-07-01-preview" API version has a bug where first response has empty choices
            event = event_chunk.model_dump()  # Convert pydantic model to dict
            if event["choices"]:
                # No usage during streaming
                completion = {
                    "delta": {
                        "content": event["choices"][0]["delta"].get("content"),
                        "role": event["choices"][0]["delta"]["role"],
                    }
                }
                # if event contains << and not >>, it is start of follow-up question, truncate
                delta_content_raw = completion["delta"].get("content")
                delta_content: str = (
                    delta_content_raw or ""
                )  # content may either not exist in delta, or explicitly be None
                if overrides.get("suggest_followup_questions") and "<<" in delta_content:
                    followup_questions_started = True
                    earlier_content = delta_content[: delta_content.index("<<")]
                    if earlier_content:
                        completion["delta"]["content"] = earlier_content
                        streamed_content += earlier_content
                        if not buffer_pbsg_stream:
                            yield completion
                    followup_content += delta_content[delta_content.index("<<") :]
                elif followup_questions_started:
                    followup_content += delta_content
                elif buffer_pbsg_stream:
                    streamed_content += delta_content
                else:
                    streamed_content += delta_content
                    yield completion
            else:
                # Final chunk at end of streaming should contain usage
                # https://cookbook.openai.com/examples/how_to_stream_completions#4-how-to-get-token-usage-data-for-streamed-chat-completion-response
                if event_chunk.usage and extra_info.thoughts and self.include_token_usage:
                    extra_info.thoughts[-1].update_token_usage(event_chunk.usage)
                    yield {"delta": {"role": "assistant"}, "context": extra_info, "session_state": session_state}

        if followup_content:
            _, followup_questions = self.extract_followup_questions(followup_content)
            extra_info.followup_questions = followup_questions
            yield {
                "delta": {"role": "assistant"},
                "context": {"context": extra_info, "followup_questions": followup_questions},
            }
        if streamed_content:
            streamed_content = self.normalize_asked_question_text(streamed_content, extra_info) or streamed_content
            streamed_content = collapse_duplicate_route_cards(streamed_content) or streamed_content
            streamed_content = self.apply_triage_response_guard(streamed_content, extra_info) or streamed_content
            latest_content = messages[-1].get("content") if messages else ""
            if isinstance(latest_content, str):
                streamed_content = self.repair_unvalidated_prerequisite_skip(streamed_content, messages, latest_content) or streamed_content
                streamed_content = self.repair_hardship_rejection(streamed_content, messages, latest_content) or streamed_content
            extra_info.quick_reply = self.build_quick_reply(streamed_content, extra_info)
            if buffer_pbsg_stream:
                yield {"delta": {"role": "assistant", "content": streamed_content}}
            yield {"delta": {"role": "assistant"}, "context": extra_info, "session_state": session_state}

    async def run(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
        context: dict[str, Any] = {},
    ) -> dict[str, Any]:
        overrides = context.get("overrides", {})
        auth_claims = context.get("auth_claims", {})
        return await self.run_without_streaming(messages, overrides, auth_claims, session_state)

    async def run_stream(
        self,
        messages: list[ChatCompletionMessageParam],
        session_state: Any = None,
        context: dict[str, Any] = {},
    ) -> AsyncGenerator[dict[str, Any], None]:
        overrides = context.get("overrides", {})
        auth_claims = context.get("auth_claims", {})
        return self.run_with_streaming(messages, overrides, auth_claims, session_state)

    async def run_until_final_call(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
        should_stream: bool = False,
    ) -> tuple[ExtraInfo, Awaitable[ChatCompletion] | Awaitable[AsyncStream[ChatCompletionChunk]]]:
        use_agentic_knowledgebase = True if overrides.get("use_agentic_knowledgebase") else False
        original_user_query = messages[-1]["content"]

        reasoning_model_support = self.GPT_REASONING_MODELS.get(self.chatgpt_model)
        if reasoning_model_support and (not reasoning_model_support.streaming and should_stream):
            raise Exception(
                f"{self.chatgpt_model} does not support streaming. Please use a different model or disable streaming."
            )
        if use_agentic_knowledgebase:
            if should_stream and overrides.get("use_web_source"):
                raise Exception(
                    "Streaming is not supported with agentic retrieval when web source is enabled. Please disable streaming or web source."
                )
            extra_info = await self.run_agentic_retrieval_approach(messages, overrides, auth_claims)
        else:
            extra_info = await self.run_search_approach(messages, overrides, auth_claims, session_state)

        if extra_info.answer:
            # If agentic retrieval already provided an answer, skip final call to LLM
            async def return_answer() -> ChatCompletion:
                return self.chat_completion_from_content(extra_info.answer or "")

            return (extra_info, return_answer())

        golden_set_entries = self.extract_golden_set_entries(extra_info.data_points.text)
        triage_state_prompt = ""
        if isinstance(original_user_query, str) and golden_set_entries:
            triage_state = build_triage_state(messages[:-1], golden_set_entries, original_user_query)
            triage_state = hydrate_triage_state_from_session_memory(triage_state, session_state)
            triage_state_prompt = format_state_prompt(triage_state)
            extra_info.deterministic_transition = resolve_expected_transition(
                golden_set_entries, triage_state, original_user_query
            )
            deterministic_response = self.render_deterministic_transition_response(
                extra_info.deterministic_transition,
                golden_set_entries,
            )
            if deterministic_response:
                extra_info.thoughts.append(
                    ThoughtStep(
                        "Deterministic PBSG routing",
                        "Rendered the final route card from Golden Set branching logic without LLM generation.",
                        {
                            "entry_id": extra_info.deterministic_transition.entry_id,
                            "question_id": extra_info.deterministic_transition.question_id,
                            "branch_key": extra_info.deterministic_transition.branch_key,
                            "transition_type": extra_info.deterministic_transition.transition_type,
                        },
                    )
                )

                async def return_deterministic_answer() -> ChatCompletion:
                    return self.chat_completion_from_content(deterministic_response, "deterministic-pbsg-route")

                return (extra_info, return_deterministic_answer())

        system_template_variables = self.get_system_prompt_variables(overrides.get("prompt_template"))
        system_template_variables.setdefault("injected_prompt", "")
        system_template_variables = system_template_variables | {
            "include_follow_up_questions": bool(overrides.get("suggest_followup_questions")),
            "image_sources": extra_info.data_points.images,
            "citations": extra_info.data_points.citations,
            "routing_state_prompt": triage_state_prompt,
        }
        if isinstance(original_user_query, str) and golden_set_entries:
            system_template_variables = prompt_vars_with_memory(system_template_variables, triage_state)

        messages = self.prompt_manager.build_conversation(
            system_template_path="chat_answer.system.jinja2",
            system_template_variables=system_template_variables,
            user_template_path="chat_answer.user.jinja2",
            user_template_variables={
                "user_query": original_user_query,
                "text_sources": extra_info.data_points.text,
            },
            user_image_sources=extra_info.data_points.images,
            past_messages=messages[:-1],
        )

        chat_coroutine = cast(
            Awaitable[ChatCompletion] | Awaitable[AsyncStream[ChatCompletionChunk]],
            self.create_chat_completion(
                self.chatgpt_deployment,
                self.chatgpt_model,
                messages,
                overrides,
                self.get_response_token_limit(self.chatgpt_model, 1024),
                should_stream,
            ),
        )
        extra_info.thoughts.append(
            self.format_thought_step_for_chatcompletion(
                title="Prompt to generate answer",
                messages=messages,
                overrides=overrides,
                model=self.chatgpt_model,
                deployment=self.chatgpt_deployment,
                usage=None,
            )
        )
        return (extra_info, chat_coroutine)

    async def run_search_approach(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
        session_state: Any = None,
    ):
        use_text_search = overrides.get("retrieval_mode") in ["text", "hybrid", None]
        use_vector_search = overrides.get("retrieval_mode") in ["vectors", "hybrid", None]
        use_semantic_ranker = True if overrides.get("semantic_ranker") else False
        use_semantic_captions = True if overrides.get("semantic_captions") else False
        use_query_rewriting = True if overrides.get("query_rewriting") else False
        top = overrides.get("top", 3)
        minimum_search_score = overrides.get("minimum_search_score", 0.0)
        minimum_reranker_score = overrides.get("minimum_reranker_score", 0.0)
        # Reranker scores are only available on semantic ranker path.
        # If semantic ranker is off, do not filter results by reranker threshold.
        if not use_semantic_ranker:
            minimum_reranker_score = 0.0
        search_index_filter = self.build_filter(overrides)
        access_token = auth_claims.get("access_token") if self.enforce_access_control else None
        send_text_sources = overrides.get("send_text_sources", True)
        send_image_sources = overrides.get("send_image_sources", self.multimodal_enabled) and self.multimodal_enabled
        search_text_embeddings = overrides.get("search_text_embeddings", True)
        search_image_embeddings = (
            overrides.get("search_image_embeddings", self.multimodal_enabled) and self.multimodal_enabled
        )

        original_user_query = messages[-1]["content"]
        if not isinstance(original_user_query, str):
            raise ValueError("The most recent message content must be a string.")

        # STEP 1: Generate an optimized keyword search query based on the chat history and the last question

        triage_state = hydrate_triage_state_from_session_memory(
            build_triage_state(messages[:-1], self.pbsg_golden_set_entries, original_user_query),
            session_state,
        )
        rewrite_result = await self.rewrite_query(
            prompt_template="query_rewrite.system.jinja2",
            prompt_variables=rewrite_vars_with_memory(original_user_query, messages[:-1], triage_state),
            overrides=overrides,
            chatgpt_model=self.chatgpt_model,
            chatgpt_deployment=self.chatgpt_deployment,
            user_query=original_user_query,
            response_token_limit=self.get_response_token_limit(
                self.chatgpt_model, 100
            ),  # Setting too low risks malformed JSON, setting too high may affect performance
            tools=self.query_rewrite_tools,
            temperature=0.0,  # Minimize creativity for search query generation
            no_response_token=self.NO_RESPONSE,
        )

        query_text = rewrite_result.query
        rewrite_fallback_to_user_query = False
        if self.rewrite_looks_drifted(original_user_query, query_text):
            query_text = original_user_query
            rewrite_fallback_to_user_query = True

        # STEP 2: Retrieve relevant documents from the search index with the GPT optimized query

        vectors: list[VectorQuery] = []
        if use_vector_search:
            if search_text_embeddings:
                vectors.append(await self.compute_text_embedding(query_text))
            if search_image_embeddings:
                vectors.append(await self.compute_multimodal_embedding(query_text))

        results = await self.search(
            top,
            query_text,
            search_index_filter,
            vectors,
            use_text_search,
            use_vector_search,
            use_semantic_ranker,
            use_semantic_captions,
            minimum_search_score,
            minimum_reranker_score,
            use_query_rewriting,
            access_token,
        )

        # STEP 3: Generate a contextual and content specific answer using the search results and chat history
        data_points = await self.get_sources_content(
            results,
            use_semantic_captions,
            include_text_sources=send_text_sources,
            download_image_sources=send_image_sources,
            user_oid=auth_claims.get("oid"),
        )
        self.ensure_initial_topic_sources(data_points, messages, original_user_query)
        extra_info = ExtraInfo(
            data_points,
            thoughts=[
                self.format_thought_step_for_chatcompletion(
                    title="Prompt to generate search query",
                    messages=rewrite_result.messages,
                    overrides=overrides,
                    model=self.chatgpt_model,
                    deployment=self.chatgpt_deployment,
                    usage=rewrite_result.completion.usage,
                    reasoning_effort=rewrite_result.reasoning_effort,
                ),
                ThoughtStep(
                    "Search using generated search query",
                    query_text,
                    {
                        "use_semantic_captions": use_semantic_captions,
                        "use_semantic_ranker": use_semantic_ranker,
                        # Keep legacy key for compatibility, but add explicit key name
                        # to avoid confusion with the app-level LLM query rewrite step.
                        "use_query_rewriting": use_query_rewriting,
                        "use_azure_search_query_rewriting": use_query_rewriting,
                        "top": top,
                        "filter": search_index_filter,
                        "use_vector_search": use_vector_search,
                        "use_text_search": use_text_search,
                        "search_text_embeddings": search_text_embeddings,
                        "search_image_embeddings": search_image_embeddings,
                        "rewrite_fallback_to_user_query": rewrite_fallback_to_user_query,
                        "minimum_search_score": minimum_search_score,
                        "minimum_reranker_score": minimum_reranker_score,
                    },
                ),
                ThoughtStep(
                    "Search results",
                    [result.serialize_for_results() for result in results],
                ),
            ],
        )
        return extra_info

    async def run_agentic_retrieval_approach(
        self,
        messages: list[ChatCompletionMessageParam],
        overrides: dict[str, Any],
        auth_claims: dict[str, Any],
    ):
        search_index_filter = self.build_filter(overrides)
        access_token = auth_claims.get("access_token") if self.enforce_access_control else None
        minimum_reranker_score = overrides.get("minimum_reranker_score", 0)
        send_text_sources = overrides.get("send_text_sources", True)
        send_image_sources = overrides.get("send_image_sources", self.multimodal_enabled) and self.multimodal_enabled
        retrieval_reasoning_effort = overrides.get("retrieval_reasoning_effort", self.retrieval_reasoning_effort)
        # Overrides can only disable web source support configured at construction time.
        use_web_source = self.web_source_enabled
        override_use_web_source = overrides.get("use_web_source")
        if isinstance(override_use_web_source, bool):
            use_web_source = use_web_source and override_use_web_source
        # Overrides can only disable sharepoint source support configured at construction time.
        use_sharepoint_source = self.use_sharepoint_source
        override_use_sharepoint_source = overrides.get("use_sharepoint_source")
        if isinstance(override_use_sharepoint_source, bool):
            use_sharepoint_source = use_sharepoint_source and override_use_sharepoint_source
        if use_web_source and retrieval_reasoning_effort == "minimal":
            raise Exception("Web source cannot be used with minimal retrieval reasoning effort.")

        selected_client, effective_web_source, effective_sharepoint_source = self._select_knowledgebase_client(
            use_web_source,
            use_sharepoint_source,
        )

        agentic_results = await self.run_agentic_retrieval(
            messages=messages,
            knowledgebase_client=selected_client,
            search_index_name=self.search_index_name,
            filter_add_on=search_index_filter,
            minimum_reranker_score=minimum_reranker_score,
            access_token=access_token,
            use_web_source=effective_web_source,
            use_sharepoint_source=effective_sharepoint_source,
            retrieval_reasoning_effort=retrieval_reasoning_effort,
        )

        data_points = await self.get_sources_content(
            agentic_results.documents,
            use_semantic_captions=False,
            include_text_sources=send_text_sources,
            download_image_sources=send_image_sources,
            user_oid=auth_claims.get("oid"),
            web_results=agentic_results.web_results,
            sharepoint_results=agentic_results.sharepoint_results,
        )
        original_user_query = messages[-1].get("content") if messages else None
        self.ensure_initial_topic_sources(data_points, messages, original_user_query)

        return ExtraInfo(
            data_points,
            thoughts=agentic_results.thoughts,
            answer=agentic_results.answer,
        )

    def _select_knowledgebase_client(
        self,
        use_web_source: bool,
        use_sharepoint_source: bool,
    ) -> tuple[KnowledgeBaseRetrievalClient, bool, bool]:
        if use_web_source and use_sharepoint_source:
            if self.knowledgebase_client_with_web_and_sharepoint:
                return self.knowledgebase_client_with_web_and_sharepoint, True, True
            if self.knowledgebase_client_with_web:
                return self.knowledgebase_client_with_web, True, False
            if self.knowledgebase_client_with_sharepoint:
                return self.knowledgebase_client_with_sharepoint, False, True

        if use_web_source and self.knowledgebase_client_with_web:
            return self.knowledgebase_client_with_web, True, False

        if use_sharepoint_source and self.knowledgebase_client_with_sharepoint:
            return self.knowledgebase_client_with_sharepoint, False, True

        if self.knowledgebase_client:
            return self.knowledgebase_client, False, False
        raise ValueError("Agentic retrieval requested but no knowledge base is configured")
