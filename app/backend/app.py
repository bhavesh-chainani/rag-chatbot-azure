import dataclasses
import io
import json
import logging
import mimetypes
import os
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from azure.cognitiveservices.speech import (
    ResultReason,
    SpeechConfig,
    SpeechSynthesisOutputFormat,
    SpeechSynthesisResult,
    SpeechSynthesizer,
)
from azure.identity.aio import (
    AzureDeveloperCliCredential,
    ManagedIdentityCredential,
    get_bearer_token_provider,
)
from azure.monitor.opentelemetry import configure_azure_monitor
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.knowledgebases.aio import KnowledgeBaseRetrievalClient
from opentelemetry.instrumentation.aiohttp_client import AioHttpClientInstrumentor
from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
from opentelemetry.instrumentation.httpx import (
    HTTPXClientInstrumentor,
)
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from quart import (
    Blueprint,
    Quart,
    abort,
    current_app,
    jsonify,
    make_response,
    request,
    send_file,
    send_from_directory,
)
from quart_cors import cors

from approaches.approach import Approach, DataPoints
from approaches.chatreadretrieveread import ChatReadRetrieveReadApproach
from approaches.promptmanager import PromptManager
from chat_history.cosmosdb import chat_history_cosmosdb_bp
from config import (
    CONFIG_AGENTIC_KNOWLEDGEBASE_ENABLED,
    CONFIG_AUTH_CLIENT,
    CONFIG_CHAT_APPROACH,
    CONFIG_CHAT_HISTORY_BROWSER_ENABLED,
    CONFIG_CHAT_HISTORY_COSMOS_ENABLED,
    CONFIG_CREDENTIAL,
    CONFIG_DEFAULT_REASONING_EFFORT,
    CONFIG_DEFAULT_RETRIEVAL_REASONING_EFFORT,
    CONFIG_GLOBAL_BLOB_MANAGER,
    CONFIG_INGESTER,
    CONFIG_KNOWLEDGEBASE_CLIENT,
    CONFIG_KNOWLEDGEBASE_CLIENT_WITH_SHAREPOINT,
    CONFIG_KNOWLEDGEBASE_CLIENT_WITH_WEB,
    CONFIG_KNOWLEDGEBASE_CLIENT_WITH_WEB_AND_SHAREPOINT,
    CONFIG_LANGUAGE_PICKER_ENABLED,
    CONFIG_MULTIMODAL_ENABLED,
    CONFIG_OPENAI_CLIENT,
    CONFIG_QUERY_REWRITING_ENABLED,
    CONFIG_QUERY_ROUTER_DEPLOYMENT,
    CONFIG_QUERY_ROUTER_ENABLED,
    CONFIG_QUERY_ROUTER_MODEL,
    CONFIG_QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE,
    CONFIG_QUERY_ROUTER_SCOPE_DESCRIPTION,
    CONFIG_RAG_SEARCH_IMAGE_EMBEDDINGS,
    CONFIG_RAG_SEARCH_TEXT_EMBEDDINGS,
    CONFIG_RAG_SEND_IMAGE_SOURCES,
    CONFIG_RAG_SEND_TEXT_SOURCES,
    CONFIG_REASONING_EFFORT_ENABLED,
    CONFIG_SEARCH_CLIENT,
    CONFIG_SEMANTIC_RANKER_DEPLOYED,
    CONFIG_SHAREPOINT_SOURCE_ENABLED,
    CONFIG_SPEECH_INPUT_ENABLED,
    CONFIG_SPEECH_OUTPUT_AZURE_ENABLED,
    CONFIG_SPEECH_OUTPUT_BROWSER_ENABLED,
    CONFIG_SPEECH_SERVICE_ID,
    CONFIG_SPEECH_SERVICE_LOCATION,
    CONFIG_SPEECH_SERVICE_TOKEN,
    CONFIG_SPEECH_SERVICE_VOICE,
    CONFIG_STREAMING_ENABLED,
    CONFIG_USER_BLOB_MANAGER,
    CONFIG_USER_UPLOAD_ENABLED,
    CONFIG_VECTOR_SEARCH_ENABLED,
    CONFIG_WEB_SOURCE_ENABLED,
)
from core.authentication import AuthenticationHelper
from core.sessionhelper import create_session_id
from decorators import authenticated, authenticated_path
from error import error_dict, error_response
from prepdocs import (
    OpenAIHost,
    setup_embeddings_service,
    setup_file_processors,
    setup_image_embeddings_service,
    setup_openai_client,
    setup_search_info,
)
from prepdocslib.blobmanager import AdlsBlobManager, BlobManager
from prepdocslib.embeddings import ImageEmbeddings
from prepdocslib.filestrategy import UploadUserFileStrategy
from prepdocslib.listfilestrategy import File
from query_router import (
    is_obvious_non_query,
    is_query_relevant,
    out_of_scope_response,
    out_of_scope_stream,
)

bp = Blueprint("routes", __name__, static_folder="static")
# Fix Windows registry issue with mimetypes
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


@bp.route("/")
async def index():
    return await bp.send_static_file("index.html")


# Empty page is recommended for login redirect to work.
# See https://github.com/AzureAD/microsoft-authentication-library-for-js/blob/dev/lib/msal-browser/docs/initialization.md#redirecturi-considerations for more information
@bp.route("/redirect")
async def redirect():
    return ""


@bp.route("/legalchat.ico")
async def favicon():
    return await bp.send_static_file("legalchat.ico")


@bp.route("/assets/<path:path>")
async def assets(path):
    return await send_from_directory(Path(__file__).resolve().parent / "static" / "assets", path)


@bp.route("/content/<path>")
@authenticated_path
async def content_file(path: str, auth_claims: dict[str, Any]):
    """
    Serve content files from blob storage from within the app to keep the example self-contained.
    *** NOTE *** if you are using app services authentication, this route will return unauthorized to all users that are not logged in
    if AZURE_ENFORCE_ACCESS_CONTROL is not set or false, logged in users can access all files regardless of access control
    if AZURE_ENFORCE_ACCESS_CONTROL is set to true, logged in users can only access files they have access to
    This is also slow and memory hungry.
    """
    # Remove page number from path, filename-1.txt -> filename.txt
    # This shouldn't typically be necessary as browsers don't send hash fragments to servers
    if path.find("#page=") > 0:
        path_parts = path.rsplit("#page=", 1)
        path = path_parts[0]
    current_app.logger.info("Opening file %s", path)
    blob_manager: BlobManager = current_app.config[CONFIG_GLOBAL_BLOB_MANAGER]

    # Get bytes and properties from the blob manager
    result = await blob_manager.download_blob(path)

    if result is None:
        current_app.logger.info("Path not found in general Blob container: %s", path)
        if current_app.config[CONFIG_USER_UPLOAD_ENABLED]:
            user_oid = auth_claims["oid"]
            user_blob_manager: AdlsBlobManager = current_app.config[CONFIG_USER_BLOB_MANAGER]
            result = await user_blob_manager.download_blob(path, user_oid=user_oid)
            if result is None:
                current_app.logger.exception("Path not found in DataLake: %s", path)

    if not result:
        abort(404)

    content, properties = result

    if not properties or "content_settings" not in properties:
        abort(404)

    mime_type = properties["content_settings"]["content_type"]
    if mime_type == "application/octet-stream":
        mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"

    # Create a BytesIO object from the bytes
    blob_file = io.BytesIO(content)
    return await send_file(blob_file, mimetype=mime_type, as_attachment=False, attachment_filename=path)


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            as_dict = dataclasses.asdict(o)
            if isinstance(o, DataPoints):
                # Drop optional data point collections that are not populated to keep API surface stable
                return {k: v for k, v in as_dict.items() if v is not None}
            data_points_payload = as_dict.get("data_points") if isinstance(as_dict, dict) else None
            if isinstance(data_points_payload, dict) and data_points_payload.get("citation_activity_details") is None:
                data_points_payload.pop("citation_activity_details")
            return as_dict
        return super().default(o)


async def format_as_ndjson(r: AsyncGenerator[dict, None]) -> AsyncGenerator[str, None]:
    try:
        async for event in r:
            yield json.dumps(event, ensure_ascii=False, cls=JSONEncoder) + "\n"
    except Exception as error:
        logging.exception("Exception while generating response stream: %s", error)
        yield json.dumps(error_dict(error))


BACKEND_GUARDRAIL_ENFORCEMENT = os.getenv("BACKEND_GUARDRAIL_ENFORCEMENT", "true").lower() == "true"

GUARDRAILS_SECTION_RE = re.compile(
    r"\n\*\*Guardrails(?:[^\n]*)\*\*\s*\n(?:.*\n)*?(?=\n\*\*[^\n]+\*\*|\Z)",
    re.IGNORECASE,
)
DISALLOWED_LEGAL_ADVICE_PATTERNS = (
    re.compile(r"\b(?:you|the caller)\s+should\s+(?:sue|file|plead|admit|accept|reject|sign)\b", re.IGNORECASE),
    re.compile(r"\b(?:you|the caller)\s+will\s+(?:win|lose)\b", re.IGNORECASE),
    re.compile(r"\b(?:high|strong)\s+chance\s+of\s+(?:winning|success)\b", re.IGNORECASE),
    re.compile(r"\bI\s+interpret\s+(?:this|your)\s+(?:document|contract|agreement)\b", re.IGNORECASE),
    re.compile(r"\bthis\s+clause\s+means\b", re.IGNORECASE),
)
HIGH_RISK_USER_QUERY_PATTERNS = (
    re.compile(r"\bwhat should (?:i|we|the caller) do legally\b", re.IGNORECASE),
    re.compile(r"\bwill (?:i|we|the caller) win\b", re.IGNORECASE),
    re.compile(r"\binterpret (?:this|my|our|the) (?:document|contract|agreement)\b", re.IGNORECASE),
    re.compile(r"\bwhat does this clause mean\b", re.IGNORECASE),
)
GUARDRAIL_REPLACEMENT_MESSAGE = (
    "I can help with intake triage and routing based on the knowledge base, but I cannot provide legal advice on what the "
    "caller should do legally, interpret legal documents, or predict case outcomes. Please share the applicant's facts and I "
    "will continue with the next required triage question or route."
)


def sanitize_assistant_content(content: str) -> str:
    sanitized = content
    # Remove user-facing guardrail blocks; they are enforced server-side.
    sanitized = GUARDRAILS_SECTION_RE.sub("\n", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()

    if any(pattern.search(sanitized) for pattern in DISALLOWED_LEGAL_ADVICE_PATTERNS):
        return GUARDRAIL_REPLACEMENT_MESSAGE
    return sanitized


def is_high_risk_user_query(user_message: str | None) -> bool:
    if not user_message:
        return False
    return any(pattern.search(user_message) for pattern in HIGH_RISK_USER_QUERY_PATTERNS)


def enforce_backend_guardrails_on_chat_response(chat_response: dict[str, Any]) -> dict[str, Any]:
    message = chat_response.get("message")
    if not isinstance(message, dict):
        return chat_response
    content = message.get("content")
    if not isinstance(content, str):
        return chat_response
    message["content"] = sanitize_assistant_content(content)
    return chat_response


async def single_chat_response_stream(chat_response: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
    message = chat_response.get("message", {})
    context = chat_response.get("context")
    session_state = chat_response.get("session_state")
    role = message.get("role", "assistant")
    content = message.get("content", "")
    yield {"delta": {"role": role}, "context": context, "session_state": session_state}
    yield {"delta": {"role": role, "content": content}}
    yield {"delta": {"role": role}, "context": context, "session_state": session_state}


async def enforce_backend_guardrails_on_stream(
    events: AsyncGenerator[dict[str, Any], None],
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Lightweight streaming guardrail pass:
    - keeps true streaming for latency
    - strips explicit user-facing guardrail headers from streamed text
    - blocks further content if disallowed legal-advice patterns appear
    """
    generated_text = ""
    blocked = False
    emitted_replacement = False

    async for event in events:
        delta = event.get("delta") if isinstance(event, dict) else None
        if not isinstance(delta, dict):
            yield event
            continue

        content = delta.get("content")
        if not isinstance(content, str):
            yield event
            continue

        if blocked:
            # Drop remaining content deltas once blocked, keep metadata/context events.
            continue

        candidate = generated_text + content
        if any(pattern.search(candidate) for pattern in DISALLOWED_LEGAL_ADVICE_PATTERNS):
            blocked = True
            if not emitted_replacement:
                emitted_replacement = True
                yield {"delta": {"role": delta.get("role", "assistant"), "content": f"\n\n{GUARDRAIL_REPLACEMENT_MESSAGE}"}}
            continue

        # Remove any explicit "Guardrails" heading if generated.
        cleaned = re.sub(r"\*\*Guardrails(?:[^\n]*)\*\*", "", content, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        generated_text += cleaned
        if cleaned:
            event["delta"]["content"] = cleaned
            yield event


def get_last_user_message_text(messages: list[dict[str, Any]]) -> str | None:
    """Extract the last user message as plain text for query routing; returns None if not a string (e.g. multimodal)."""
    if not messages:
        return None
    content = messages[-1].get("content")
    if isinstance(content, str):
        return content
    return None


def get_last_assistant_message_text(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
    return None


def classify_short_answer(text: str | None) -> str | None:
    if not text:
        return None
    normalized = text.strip().lower().rstrip(".!?")
    if normalized in {"yes", "y"}:
        return "yes"
    if normalized in {"no", "n"}:
        return "no"
    if normalized in {"not sure", "unsure", "don't know", "do not know"}:
        return "not_sure"
    return None


PENDING_ENTRY_RE = re.compile(r"Selected Entry:\s*(GEN3-[A-Z0-9]+)", re.IGNORECASE)
PENDING_QUESTION_RE = re.compile(r"\bQ([0-9]+[A-Z]?)\s*:", re.IGNORECASE)

DETERMINISTIC_QUESTIONS: dict[str, dict[str, str]] = {
    "GEN3-T01": {
        "Q1": "Is the caller currently represented by a lawyer on this same matter?",
        "Q2": "Is the caller the person who needs legal help, or are they calling on behalf of someone else?",
        "Q3": "Is this a business/commercial matter (company dispute, B2B contract, commercial debt, shareholder dispute)?",
        "Q3A": "Is the matter for a non-profit / charity / social enterprise (or someone seeking to incorporate one), or for a for-profit business?",
        "Q4": "Has the caller already received legal advice from a lawyer on this same matter?",
        "Q4A": "Is the caller seeking legal guidance (clinic consultation) or legal representation (a lawyer to act on their behalf)?",
        "Q5": "What type of matter is the caller facing (criminal, matrimonial/family, or civil/other)?",
    },
    "GEN3-T02": {
        "Q1": "Is the offence a capital offence (punishable with death)?",
        "Q2": "Is there a court date/deadline within 14 days?",
        "Q3": "Has the caller been charged in court?",
        "Q4": "Is the caller a Singapore Citizen or PR?",
        "Q5": "Has the caller applied to, or been told about, the Public Defender’s Office (PDO)?",
        "Q5A": "What is the PDO status — processing/accepted, unable to assist, or unknown?",
        "Q6": "Is the caller within CLAS means thresholds (PCHI <= S$1,650, savings threshold by age, and non-private housing)?",
    },
    "GEN3-T03": {
        "Q1": "Is there active/recent family violence, or a court date/deadline within 14 days?",
        "Q2": "Is the caller a Singapore Citizen or PR?",
        "Q3": "Has the caller applied to the Legal Aid Bureau (LAB) for civil legal aid?",
        "Q4": "Does the caller have at least one Singaporean child (under 21)?",
        "Q5": "Is the caller within FJSS Pro Bono means thresholds?",
        "Q5A": "Is the caller marginally above Pro Bono thresholds, or clearly well above thresholds?",
    },
    "GEN3-T04": {
        "Q1": "Is the caller a Singapore Citizen or PR?",
        "Q2": "Is the caller seeking representation or guidance?",
        "Q3": "Has the caller applied to the Legal Aid Bureau (LAB)?",
        "Q4": "Is the caller within legal clinic means thresholds?",
        "Q4A": "Is the caller marginal/exceptional, or clearly outside criteria with no exceptional circumstances?",
    },
}


def parse_pending_entry_and_question(last_assistant: str | None) -> tuple[str | None, str | None]:
    if not last_assistant:
        return None, None
    entry_match = PENDING_ENTRY_RE.search(last_assistant)
    question_matches = PENDING_QUESTION_RE.findall(last_assistant)
    if not entry_match or not question_matches:
        return None, None
    # Use the last question label shown in the assistant turn.
    return entry_match.group(1).upper(), f"Q{question_matches[-1].upper()}"


def normalize_answer_label(answer: str, entry_id: str, question_id: str) -> str | None:
    normalized = answer.strip().lower()
    short = classify_short_answer(answer)

    if short:
        return short

    if question_id == "Q2" and entry_id == "GEN3-T01":
        if "on behalf" in normalized or "for someone else" in normalized:
            return "on_behalf"
        if "self" in normalized or "for myself" in normalized or "me" == normalized:
            return "self"
    if question_id == "Q3A" and entry_id == "GEN3-T01":
        if "non-profit" in normalized or "charity" in normalized or "social enterprise" in normalized:
            return "non_profit"
        if "for-profit" in normalized or "business" in normalized or "company" in normalized:
            return "for_profit"
    if question_id == "Q4A" and entry_id == "GEN3-T01":
        if "guidance" in normalized or "clinic" in normalized or "advice" in normalized:
            return "guidance"
        if "representation" in normalized or "lawyer to act" in normalized:
            return "representation"
    if question_id == "Q5" and entry_id == "GEN3-T01":
        if "criminal" in normalized or "charged" in normalized or "police" in normalized:
            return "criminal"
        if "family" in normalized or "matrimonial" in normalized or "divorce" in normalized:
            return "matrimonial"
        if "civil" in normalized or "contract" in normalized or "employment" in normalized or "probate" in normalized:
            return "civil"
    if question_id == "Q2" and entry_id == "GEN3-T04":
        if "representation" in normalized:
            return "representation"
        if "guidance" in normalized:
            return "guidance"
    if question_id == "Q5A" and entry_id == "GEN3-T02":
        if "unable" in normalized or "rejected" in normalized:
            return "unable_to_assist"
        if "processing" in normalized or "accepted" in normalized or "approved" in normalized:
            return "processing_or_accepted"
        if "unknown" in normalized:
            return "unknown"
    if question_id == "Q5A" and entry_id == "GEN3-T03":
        if "marginal" in normalized:
            return "marginal"
        if "well above" in normalized or "clearly above" in normalized:
            return "well_above"
    if question_id == "Q4A" and entry_id == "GEN3-T04":
        if "marginal" in normalized or "exceptional" in normalized:
            return "marginal_or_exceptional"
        if "outside" in normalized or "none" in normalized:
            return "outside_no_exceptional"

    return None


def make_question_response(entry_id: str, question_id: str) -> str:
    question_text = DETERMINISTIC_QUESTIONS[entry_id][question_id]
    return (
        f"**Selected Entry:** {entry_id}\n\n"
        "**Part B Question**\n\n"
        f"> **{question_id}: {question_text}**\n\n"
        "Enter the applicant's answer and I will determine the next required question or route."
    )


def make_route_response(entry_id: str, route_text: str) -> str:
    return (
        f"**Selected Entry:** {entry_id}\n\n"
        "**Part C — Routing recommendation:**\n\n"
        f"{route_text}\n\n"
        "Escalate to PBSG Staff if any key facts are unclear."
    )


def deterministic_transition(entry_id: str, question_id: str, answer_label: str) -> str | None:
    if entry_id == "GEN3-T01":
        if question_id == "Q1":
            if answer_label == "yes":
                return make_route_response("GEN3-T01", "Route A (Already Represented — Reject).")
            if answer_label == "no":
                return make_question_response("GEN3-T01", "Q2")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q2":
            if answer_label == "on_behalf":
                return make_route_response("GEN3-T01", "If caller is calling on behalf and person can self-help: Route B (Refer Person to Contact PBSG Directly). Otherwise continue to Q3.")
            if answer_label in {"self", "no"}:
                return make_question_response("GEN3-T01", "Q3")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q3":
            if answer_label == "yes":
                return make_question_response("GEN3-T01", "Q3A")
            if answer_label == "no":
                return make_question_response("GEN3-T01", "Q4")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q3A":
            if answer_label == "non_profit":
                return make_route_response("GEN3-T01", "Route C (Non-Profit Legal Services).")
            if answer_label == "for_profit":
                return make_route_response("GEN3-T01", "Route D (Reject — Business/Commercial Matter).")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q4":
            if answer_label == "yes":
                return make_question_response("GEN3-T01", "Q4A")
            if answer_label == "no":
                return make_question_response("GEN3-T01", "Q5")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q4A":
            if answer_label == "guidance":
                return make_route_response("GEN3-T01", "Route E (Reject for Guidance — Prior Advice Received).")
            if answer_label == "representation":
                return make_question_response("GEN3-T01", "Q5")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q5":
            if answer_label == "criminal":
                return make_route_response("GEN3-T01", "Route G (Criminal Stream) -> Proceed to GEN3-T02.")
            if answer_label == "matrimonial":
                return make_route_response("GEN3-T01", "Route H (Matrimonial Stream) -> Proceed to GEN3-T03.")
            if answer_label == "civil":
                return make_route_response("GEN3-T01", "Route I (Civil Stream) -> Proceed to GEN3-T04.")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T01", "Route F (Escalate to PBSG Staff).")

    if entry_id == "GEN3-T02":
        if question_id == "Q1":
            if answer_label == "yes":
                return make_route_response("GEN3-T02", "Route A (LASCO).")
            if answer_label == "no":
                return make_question_response("GEN3-T02", "Q2")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T02", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q2":
            if answer_label in {"yes", "not_sure"}:
                return make_route_response("GEN3-T02", "Route D (CLAS + Urgent concurrent).")
            if answer_label == "no":
                return make_question_response("GEN3-T02", "Q3")
        if question_id == "Q3":
            if answer_label == "yes":
                return make_question_response("GEN3-T02", "Q4")
            if answer_label == "no":
                return make_route_response("GEN3-T02", "Proceed to GEN3-T04 — Civil and Guidance Stream Triage.")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T02", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q4":
            if answer_label == "yes":
                return make_question_response("GEN3-T02", "Q5")
            if answer_label == "no":
                return make_question_response("GEN3-T02", "Q6")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T02", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q5":
            if answer_label == "no":
                return make_route_response("GEN3-T02", "Route B (Refer to PDO First).")
            if answer_label == "yes":
                return make_question_response("GEN3-T02", "Q5A")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T02", "Route B (Refer to PDO First).")
        if question_id == "Q5A":
            if answer_label == "processing_or_accepted":
                return make_route_response("GEN3-T02", "Route C (PDO is handling — PBSG not needed).")
            if answer_label == "unable_to_assist":
                return make_route_response("GEN3-T02", "Proceed to GEN3-T04 — Civil and Guidance Stream Triage.")
            if answer_label in {"unknown", "not_sure"}:
                return make_route_response("GEN3-T02", "Route B (Refer to PDO First).")
        if question_id == "Q6":
            if answer_label == "yes":
                return make_route_response("GEN3-T02", "Route E (CLAS — Standard Intake).")
            if answer_label == "no":
                return make_route_response("GEN3-T02", "Proceed to GEN3-T04 — Civil and Guidance Stream Triage.")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T02", "Route F (Escalate to PBSG Staff).")

    if entry_id == "GEN3-T03":
        if question_id == "Q1":
            if answer_label in {"yes", "not_sure"}:
                return make_route_response("GEN3-T03", "Route A (FJSS + Urgent concurrent).")
            if answer_label == "no":
                return make_question_response("GEN3-T03", "Q2")
        if question_id == "Q2":
            if answer_label == "yes":
                return make_question_response("GEN3-T03", "Q3")
            if answer_label == "no":
                return make_question_response("GEN3-T03", "Q4")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T03", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q3":
            if answer_label == "no":
                return make_route_response("GEN3-T03", "Route B (Refer to LAB First).")
            if answer_label == "yes":
                return make_question_response("GEN3-T03", "Q5")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T03", "Route B (Refer to LAB First).")
        if question_id == "Q4":
            if answer_label == "yes":
                return make_question_response("GEN3-T03", "Q5")
            if answer_label == "no":
                return make_route_response("GEN3-T03", "Proceed to GEN3-T04 — Civil and Guidance Stream Triage.")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T03", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q5":
            if answer_label == "yes":
                return make_route_response("GEN3-T03", "Route D (FJSS Pro Bono — Standard Intake).")
            if answer_label == "no":
                return make_question_response("GEN3-T03", "Q5A")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T03", "Route F (Escalate to PBSG Staff).")
        if question_id == "Q5A":
            if answer_label == "marginal":
                return make_route_response("GEN3-T03", "Route E (FJSS Modest Means — Standard Intake).")
            if answer_label == "well_above":
                return make_route_response("GEN3-T03", "Proceed to GEN3-T04 — Civil and Guidance Stream Triage.")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T03", "Route F (Escalate to PBSG Staff).")

    if entry_id == "GEN3-T04":
        if question_id == "Q1":
            if answer_label == "yes":
                return make_question_response("GEN3-T04", "Q2")
            if answer_label == "no":
                return make_question_response("GEN3-T04", "Q4")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T04", "Route C (Escalate to PBSG Staff).")
        if question_id == "Q2":
            if answer_label == "representation":
                return make_question_response("GEN3-T04", "Q3")
            if answer_label == "guidance":
                return make_question_response("GEN3-T04", "Q4")
            if answer_label == "not_sure":
                return make_question_response("GEN3-T04", "Q4")
        if question_id == "Q3":
            if answer_label == "no":
                return make_route_response("GEN3-T04", "Route B (Refer to LAB).")
            if answer_label == "yes":
                return make_question_response("GEN3-T04", "Q4")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T04", "Route B (Refer to LAB).")
        if question_id == "Q4":
            if answer_label == "yes":
                return make_route_response("GEN3-T04", "Route A (Legal Clinic).")
            if answer_label == "no":
                return make_question_response("GEN3-T04", "Q4A")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T04", "Route C (Escalate to PBSG Staff).")
        if question_id == "Q4A":
            if answer_label == "marginal_or_exceptional":
                return make_route_response("GEN3-T04", "Route C (Escalate to PBSG Staff).")
            if answer_label == "outside_no_exceptional":
                return make_route_response("GEN3-T04", "Route D (Reject and Share Self-Help Resources).")
            if answer_label == "not_sure":
                return make_route_response("GEN3-T04", "Route C (Escalate to PBSG Staff).")

    return None


def get_deterministic_triage_override_response(messages: list[dict[str, Any]], session_state: Any) -> dict[str, Any] | None:
    """
    Fast deterministic response for known terminal branch to avoid LLM drift and latency.
    """
    last_assistant = get_last_assistant_message_text(messages)
    last_user = get_last_user_message_text(messages)
    entry_id, question_id = parse_pending_entry_and_question(last_assistant)
    if not entry_id or not question_id or not last_user:
        return None
    if entry_id not in DETERMINISTIC_QUESTIONS:
        return None

    answer_label = normalize_answer_label(last_user, entry_id, question_id)
    if not answer_label:
        return None
    override_content = deterministic_transition(entry_id, question_id, answer_label)
    if not override_content:
        return None
    return {
        "message": {"content": override_content, "role": "assistant"},
        "context": {"thoughts": [], "data_points": {}, "followup_questions": None},
        "session_state": session_state,
    }


@bp.route("/chat", methods=["POST"])
@authenticated
async def chat(auth_claims: dict[str, Any]):
    if not request.is_json:
        return jsonify({"error": "request must be json"}), 415
    request_json = await request.get_json()
    context = request_json.get("context", {})
    context["auth_claims"] = auth_claims
    try:
        session_state = request_json.get("session_state")
        if session_state is None:
            session_state = create_session_id(
                current_app.config[CONFIG_CHAT_HISTORY_COSMOS_ENABLED],
                current_app.config[CONFIG_CHAT_HISTORY_BROWSER_ENABLED],
            )
        deterministic_override = get_deterministic_triage_override_response(request_json["messages"], session_state)
        if deterministic_override is not None:
            result = deterministic_override
            if BACKEND_GUARDRAIL_ENFORCEMENT:
                result = enforce_backend_guardrails_on_chat_response(result)
            return jsonify(result)

        # Query router: if enabled and not skipped, check relevance before invoking RAG.
        if current_app.config.get(CONFIG_QUERY_ROUTER_ENABLED) and not context.get("overrides", {}).get(
            "skip_query_router"
        ):
            last_text = get_last_user_message_text(request_json["messages"])
            if last_text is not None:
                # Fast path: obvious greetings / non-queries → out-of-scope without LLM call
                if is_obvious_non_query(last_text):
                    result = out_of_scope_response(
                        message=current_app.config[CONFIG_QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE],
                        session_state=session_state,
                    )
                    return jsonify(result)
                relevant = await is_query_relevant(
                    client=current_app.config[CONFIG_OPENAI_CLIENT],
                    model=current_app.config[CONFIG_QUERY_ROUTER_MODEL],
                    deployment=current_app.config.get(CONFIG_QUERY_ROUTER_DEPLOYMENT),
                    user_message=last_text,
                    scope_description=current_app.config[CONFIG_QUERY_ROUTER_SCOPE_DESCRIPTION],
                )
                if not relevant:
                    result = out_of_scope_response(
                        message=current_app.config[CONFIG_QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE],
                        session_state=session_state,
                    )
                    return jsonify(result)

        approach: Approach = cast(Approach, current_app.config[CONFIG_CHAT_APPROACH])
        result = await approach.run(
            request_json["messages"],
            context=context,
            session_state=session_state,
        )
        if BACKEND_GUARDRAIL_ENFORCEMENT:
            result = enforce_backend_guardrails_on_chat_response(result)
        return jsonify(result)
    except Exception as error:
        return error_response(error, "/chat")


@bp.route("/chat/stream", methods=["POST"])
@authenticated
async def chat_stream(auth_claims: dict[str, Any]):
    if not request.is_json:
        return jsonify({"error": "request must be json"}), 415
    request_json = await request.get_json()
    context = request_json.get("context", {})
    context["auth_claims"] = auth_claims
    try:
        session_state = request_json.get("session_state")
        if session_state is None:
            session_state = create_session_id(
                current_app.config[CONFIG_CHAT_HISTORY_COSMOS_ENABLED],
                current_app.config[CONFIG_CHAT_HISTORY_BROWSER_ENABLED],
            )
        deterministic_override = get_deterministic_triage_override_response(request_json["messages"], session_state)
        if deterministic_override is not None:
            result = deterministic_override
            if BACKEND_GUARDRAIL_ENFORCEMENT:
                result = enforce_backend_guardrails_on_chat_response(result)
            response = await make_response(format_as_ndjson(single_chat_response_stream(result)))
            response.timeout = None  # type: ignore
            response.mimetype = "application/json-lines"
            return response

        # Query router is temporarily disabled: always pass through to RAG.
        if False and current_app.config.get(CONFIG_QUERY_ROUTER_ENABLED) and not context.get("overrides", {}).get(
            "skip_query_router"
        ):
            last_text = get_last_user_message_text(request_json["messages"])
            if last_text is not None:
                # Fast path: obvious greetings / non-queries → out-of-scope without LLM call
                if is_obvious_non_query(last_text):
                    result = out_of_scope_stream(
                        message=current_app.config[CONFIG_QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE],
                        session_state=session_state,
                    )
                    response = await make_response(format_as_ndjson(result))
                    response.timeout = None  # type: ignore
                    response.mimetype = "application/json-lines"
                    return response
                relevant = await is_query_relevant(
                    client=current_app.config[CONFIG_OPENAI_CLIENT],
                    model=current_app.config[CONFIG_QUERY_ROUTER_MODEL],
                    deployment=current_app.config.get(CONFIG_QUERY_ROUTER_DEPLOYMENT),
                    user_message=last_text,
                    scope_description=current_app.config[CONFIG_QUERY_ROUTER_SCOPE_DESCRIPTION],
                )
                if not relevant:
                    result = out_of_scope_stream(
                        message=current_app.config[CONFIG_QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE],
                        session_state=session_state,
                    )
                    response = await make_response(format_as_ndjson(result))
                    response.timeout = None  # type: ignore
                    response.mimetype = "application/json-lines"
                    return response

        approach: Approach = cast(Approach, current_app.config[CONFIG_CHAT_APPROACH])
        if BACKEND_GUARDRAIL_ENFORCEMENT and is_high_risk_user_query(
            get_last_user_message_text(request_json["messages"])
        ):
            # High-risk prompts use strict full-response sanitization.
            non_stream_result = await approach.run(
                request_json["messages"],
                context=context,
                session_state=session_state,
            )
            guarded_result = enforce_backend_guardrails_on_chat_response(non_stream_result)
            result = single_chat_response_stream(guarded_result)
        else:
            result = await approach.run_stream(
                request_json["messages"],
                context=context,
                session_state=session_state,
            )
            if BACKEND_GUARDRAIL_ENFORCEMENT:
                result = enforce_backend_guardrails_on_stream(result)
        response = await make_response(format_as_ndjson(result))
        response.timeout = None  # type: ignore
        response.mimetype = "application/json-lines"
        return response
    except Exception as error:
        return error_response(error, "/chat")


# Send MSAL.js settings to the client UI
@bp.route("/auth_setup", methods=["GET"])
def auth_setup():
    auth_helper = current_app.config[CONFIG_AUTH_CLIENT]
    return jsonify(auth_helper.get_auth_setup_for_client())


@bp.route("/config", methods=["GET"])
def config():
    return jsonify(
        {
            "showMultimodalOptions": current_app.config[CONFIG_MULTIMODAL_ENABLED],
            "showSemanticRankerOption": current_app.config[CONFIG_SEMANTIC_RANKER_DEPLOYED],
            "showQueryRewritingOption": current_app.config[CONFIG_QUERY_REWRITING_ENABLED],
            "showReasoningEffortOption": current_app.config[CONFIG_REASONING_EFFORT_ENABLED],
            "streamingEnabled": current_app.config[CONFIG_STREAMING_ENABLED],
            "defaultReasoningEffort": current_app.config[CONFIG_DEFAULT_REASONING_EFFORT],
            "defaultRetrievalReasoningEffort": current_app.config[CONFIG_DEFAULT_RETRIEVAL_REASONING_EFFORT],
            "showVectorOption": current_app.config[CONFIG_VECTOR_SEARCH_ENABLED],
            "showUserUpload": current_app.config[CONFIG_USER_UPLOAD_ENABLED],
            "showLanguagePicker": current_app.config[CONFIG_LANGUAGE_PICKER_ENABLED],
            "showSpeechInput": current_app.config[CONFIG_SPEECH_INPUT_ENABLED],
            "showSpeechOutputBrowser": current_app.config[CONFIG_SPEECH_OUTPUT_BROWSER_ENABLED],
            "showSpeechOutputAzure": current_app.config[CONFIG_SPEECH_OUTPUT_AZURE_ENABLED],
            "showChatHistoryBrowser": current_app.config[CONFIG_CHAT_HISTORY_BROWSER_ENABLED],
            "showChatHistoryCosmos": current_app.config[CONFIG_CHAT_HISTORY_COSMOS_ENABLED],
            "showAgenticRetrievalOption": current_app.config[CONFIG_AGENTIC_KNOWLEDGEBASE_ENABLED],
            "ragSearchTextEmbeddings": current_app.config[CONFIG_RAG_SEARCH_TEXT_EMBEDDINGS],
            "ragSearchImageEmbeddings": current_app.config[CONFIG_RAG_SEARCH_IMAGE_EMBEDDINGS],
            "ragSendTextSources": current_app.config[CONFIG_RAG_SEND_TEXT_SOURCES],
            "ragSendImageSources": current_app.config[CONFIG_RAG_SEND_IMAGE_SOURCES],
            "webSourceEnabled": current_app.config[CONFIG_WEB_SOURCE_ENABLED],
            "sharepointSourceEnabled": current_app.config[CONFIG_SHAREPOINT_SOURCE_ENABLED],
            "queryRouterEnabled": False,
        }
    )


@bp.route("/speech", methods=["POST"])
async def speech():
    if not request.is_json:
        return jsonify({"error": "request must be json"}), 415

    speech_token = current_app.config.get(CONFIG_SPEECH_SERVICE_TOKEN)
    if speech_token is None or speech_token.expires_on < time.time() + 60:
        speech_token = await current_app.config[CONFIG_CREDENTIAL].get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        current_app.config[CONFIG_SPEECH_SERVICE_TOKEN] = speech_token

    request_json = await request.get_json()
    text = request_json["text"]
    try:
        # Construct a token as described in documentation:
        # https://learn.microsoft.com/azure/ai-services/speech-service/how-to-configure-azure-ad-auth?pivots=programming-language-python
        auth_token = (
            "aad#"
            + current_app.config[CONFIG_SPEECH_SERVICE_ID]
            + "#"
            + current_app.config[CONFIG_SPEECH_SERVICE_TOKEN].token
        )
        speech_config = SpeechConfig(auth_token=auth_token, region=current_app.config[CONFIG_SPEECH_SERVICE_LOCATION])
        speech_config.speech_synthesis_voice_name = current_app.config[CONFIG_SPEECH_SERVICE_VOICE]
        speech_config.set_speech_synthesis_output_format(SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)
        synthesizer = SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        result: SpeechSynthesisResult = synthesizer.speak_text_async(text).get()
        if result.reason == ResultReason.SynthesizingAudioCompleted:
            return result.audio_data, 200, {"Content-Type": "audio/mp3"}
        elif result.reason == ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            current_app.logger.error(
                "Speech synthesis canceled: %s %s", cancellation_details.reason, cancellation_details.error_details
            )
            raise Exception("Speech synthesis canceled. Check logs for details.")
        else:
            current_app.logger.error("Unexpected result reason: %s", result.reason)
            raise Exception("Speech synthesis failed. Check logs for details.")
    except Exception as e:
        current_app.logger.exception("Exception in /speech")
        return jsonify({"error": str(e)}), 500


@bp.post("/upload")
@authenticated
async def upload(auth_claims: dict[str, Any]):
    request_files = await request.files
    if "file" not in request_files:
        return jsonify({"message": "No file part in the request", "status": "failed"}), 400

    try:
        user_oid = auth_claims["oid"]
        file = request_files.getlist("file")[0]
        adls_manager: AdlsBlobManager = current_app.config[CONFIG_USER_BLOB_MANAGER]
        file_url = await adls_manager.upload_blob(file, file.filename, user_oid)
        ingester: UploadUserFileStrategy = current_app.config[CONFIG_INGESTER]
        await ingester.add_file(File(content=file, url=file_url, acls={"oids": [user_oid]}), user_oid=user_oid)
        return jsonify({"message": "File uploaded successfully"}), 200
    except Exception as error:
        current_app.logger.error("Error uploading file: %s", error)
        return jsonify({"message": "Error uploading file, check server logs for details.", "status": "failed"}), 500


@bp.post("/delete_uploaded")
@authenticated
async def delete_uploaded(auth_claims: dict[str, Any]):
    request_json = await request.get_json()
    filename = request_json.get("filename")
    user_oid = auth_claims["oid"]
    adls_manager: AdlsBlobManager = current_app.config[CONFIG_USER_BLOB_MANAGER]
    await adls_manager.remove_blob(filename, user_oid)
    ingester: UploadUserFileStrategy = current_app.config[CONFIG_INGESTER]
    await ingester.remove_file(filename, user_oid)
    return jsonify({"message": f"File {filename} deleted successfully"}), 200


@bp.get("/list_uploaded")
@authenticated
async def list_uploaded(auth_claims: dict[str, Any]):
    """Lists the uploaded documents for the current user.
    Only returns files directly in the user's directory, not in subdirectories.
    Excludes image files and the images directory."""
    user_oid = auth_claims["oid"]
    adls_manager: AdlsBlobManager = current_app.config[CONFIG_USER_BLOB_MANAGER]
    files = await adls_manager.list_blobs(user_oid)
    return jsonify(files), 200


@bp.before_app_serving
async def setup_clients():
    # Replace these with your own values, either in environment variables or directly here
    AZURE_STORAGE_ACCOUNT = os.environ["AZURE_STORAGE_ACCOUNT"]
    AZURE_STORAGE_CONTAINER = os.environ["AZURE_STORAGE_CONTAINER"]
    AZURE_IMAGESTORAGE_CONTAINER = os.environ.get("AZURE_IMAGESTORAGE_CONTAINER")
    AZURE_USERSTORAGE_ACCOUNT = os.environ.get("AZURE_USERSTORAGE_ACCOUNT")
    AZURE_USERSTORAGE_CONTAINER = os.environ.get("AZURE_USERSTORAGE_CONTAINER")
    AZURE_SEARCH_SERVICE = os.environ["AZURE_SEARCH_SERVICE"]
    AZURE_SEARCH_ENDPOINT = f"https://{AZURE_SEARCH_SERVICE}.search.windows.net"
    AZURE_SEARCH_INDEX = os.environ["AZURE_SEARCH_INDEX"]
    AZURE_SEARCH_KNOWLEDGEBASE_NAME = os.getenv("AZURE_SEARCH_KNOWLEDGEBASE_NAME", "")
    # Shared by all OpenAI deployments
    OPENAI_HOST = OpenAIHost(os.getenv("OPENAI_HOST", "azure"))
    OPENAI_CHATGPT_MODEL = os.environ["AZURE_OPENAI_CHATGPT_MODEL"]
    AZURE_OPENAI_KNOWLEDGEBASE_MODEL = os.getenv("AZURE_OPENAI_KNOWLEDGEBASE_MODEL")
    AZURE_OPENAI_KNOWLEDGEBASE_DEPLOYMENT = os.getenv("AZURE_OPENAI_KNOWLEDGEBASE_DEPLOYMENT")
    OPENAI_EMB_MODEL = os.getenv("AZURE_OPENAI_EMB_MODEL_NAME", "text-embedding-ada-002")
    OPENAI_EMB_DIMENSIONS = int(os.getenv("AZURE_OPENAI_EMB_DIMENSIONS") or 1536)
    OPENAI_REASONING_EFFORT = os.getenv("AZURE_OPENAI_REASONING_EFFORT")
    # Used with Azure OpenAI deployments
    AZURE_OPENAI_SERVICE = os.getenv("AZURE_OPENAI_SERVICE")
    AZURE_OPENAI_CHATGPT_DEPLOYMENT = (
        os.getenv("AZURE_OPENAI_CHATGPT_DEPLOYMENT")
        if OPENAI_HOST in [OpenAIHost.AZURE, OpenAIHost.AZURE_CUSTOM]
        else None
    )
    AZURE_OPENAI_EMB_DEPLOYMENT = (
        os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT") if OPENAI_HOST in [OpenAIHost.AZURE, OpenAIHost.AZURE_CUSTOM] else None
    )
    AZURE_OPENAI_CUSTOM_URL = os.getenv("AZURE_OPENAI_CUSTOM_URL")
    AZURE_VISION_ENDPOINT = os.getenv("AZURE_VISION_ENDPOINT", "")
    AZURE_OPENAI_API_KEY_OVERRIDE = os.getenv("AZURE_OPENAI_API_KEY_OVERRIDE")
    # Used only with non-Azure OpenAI deployments
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_ORGANIZATION = os.getenv("OPENAI_ORGANIZATION")

    AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")
    AZURE_USE_AUTHENTICATION = os.getenv("AZURE_USE_AUTHENTICATION", "").lower() == "true"
    AZURE_ENFORCE_ACCESS_CONTROL = os.getenv("AZURE_ENFORCE_ACCESS_CONTROL", "").lower() == "true"
    AZURE_ENABLE_UNAUTHENTICATED_ACCESS = os.getenv("AZURE_ENABLE_UNAUTHENTICATED_ACCESS", "").lower() == "true"
    AZURE_SERVER_APP_ID = os.getenv("AZURE_SERVER_APP_ID")
    AZURE_SERVER_APP_SECRET = os.getenv("AZURE_SERVER_APP_SECRET")
    AZURE_CLIENT_APP_ID = os.getenv("AZURE_CLIENT_APP_ID")
    AZURE_AUTH_TENANT_ID = os.getenv("AZURE_AUTH_TENANT_ID", AZURE_TENANT_ID)

    KB_FIELDS_CONTENT = os.getenv("KB_FIELDS_CONTENT", "content")
    KB_FIELDS_SOURCEPAGE = os.getenv("KB_FIELDS_SOURCEPAGE", "sourcepage")

    AZURE_SEARCH_QUERY_LANGUAGE = os.getenv("AZURE_SEARCH_QUERY_LANGUAGE") or "en-us"
    AZURE_SEARCH_QUERY_SPELLER = os.getenv("AZURE_SEARCH_QUERY_SPELLER") or "lexicon"
    AZURE_SEARCH_SEMANTIC_RANKER = os.getenv("AZURE_SEARCH_SEMANTIC_RANKER", "free").lower()
    AZURE_SEARCH_QUERY_REWRITING = os.getenv("AZURE_SEARCH_QUERY_REWRITING", "false").lower()
    # This defaults to the previous field name "embedding", for backwards compatibility
    AZURE_SEARCH_FIELD_NAME_EMBEDDING = os.getenv("AZURE_SEARCH_FIELD_NAME_EMBEDDING", "embedding")

    AZURE_SPEECH_SERVICE_ID = os.getenv("AZURE_SPEECH_SERVICE_ID")
    AZURE_SPEECH_SERVICE_LOCATION = os.getenv("AZURE_SPEECH_SERVICE_LOCATION")
    AZURE_SPEECH_SERVICE_VOICE = os.getenv("AZURE_SPEECH_SERVICE_VOICE") or "en-US-AndrewMultilingualNeural"

    USE_MULTIMODAL = os.getenv("USE_MULTIMODAL", "").lower() == "true"
    RAG_SEARCH_TEXT_EMBEDDINGS = os.getenv("RAG_SEARCH_TEXT_EMBEDDINGS", "true").lower() == "true"
    RAG_SEARCH_IMAGE_EMBEDDINGS = os.getenv("RAG_SEARCH_IMAGE_EMBEDDINGS", "true").lower() == "true"
    RAG_SEND_TEXT_SOURCES = os.getenv("RAG_SEND_TEXT_SOURCES", "true").lower() == "true"
    RAG_SEND_IMAGE_SOURCES = os.getenv("RAG_SEND_IMAGE_SOURCES", "true").lower() == "true"
    USE_USER_UPLOAD = os.getenv("USE_USER_UPLOAD", "").lower() == "true"
    ENABLE_LANGUAGE_PICKER = os.getenv("ENABLE_LANGUAGE_PICKER", "").lower() == "true"
    USE_SPEECH_INPUT_BROWSER = os.getenv("USE_SPEECH_INPUT_BROWSER", "").lower() == "true"
    USE_SPEECH_OUTPUT_BROWSER = os.getenv("USE_SPEECH_OUTPUT_BROWSER", "").lower() == "true"
    USE_SPEECH_OUTPUT_AZURE = os.getenv("USE_SPEECH_OUTPUT_AZURE", "").lower() == "true"
    USE_CHAT_HISTORY_BROWSER = os.getenv("USE_CHAT_HISTORY_BROWSER", "").lower() == "true"
    USE_CHAT_HISTORY_COSMOS = os.getenv("USE_CHAT_HISTORY_COSMOS", "").lower() == "true"
    USE_AGENTIC_KNOWLEDGEBASE = os.getenv("USE_AGENTIC_KNOWLEDGEBASE", "").lower() == "true"
    USE_WEB_SOURCE = os.getenv("USE_WEB_SOURCE", "").lower() == "true"
    USE_SHAREPOINT_SOURCE = os.getenv("USE_SHAREPOINT_SOURCE", "").lower() == "true"
    AGENTIC_KNOWLEDGEBASE_REASONING_EFFORT = os.getenv("AGENTIC_KNOWLEDGEBASE_REASONING_EFFORT", "low")
    QUERY_ROUTER_ENABLED = os.getenv("QUERY_ROUTER_ENABLED", "").lower() == "true"
    QUERY_ROUTER_SCOPE_DESCRIPTION = os.getenv(
        "QUERY_ROUTER_SCOPE_DESCRIPTION", "legal enquiries based on our knowledge base"
    )
    QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE = os.getenv(
        "QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE",
        "I handle legal enquiries based on our knowledge base. "
        "Your question seems outside that scope. Please ask about legal matters I can look up for you.",
    )
    USE_VECTORS = os.getenv("USE_VECTORS", "").lower() != "false"

    # WEBSITE_HOSTNAME is always set by App Service, RUNNING_IN_PRODUCTION is set in main.bicep
    RUNNING_ON_AZURE = os.getenv("WEBSITE_HOSTNAME") is not None or os.getenv("RUNNING_IN_PRODUCTION") is not None

    # Use the current user identity for keyless authentication to Azure services.
    # This assumes you use 'azd auth login' locally, and managed identity when deployed on Azure.
    # The managed identity is setup in the infra/ folder.
    azure_credential: AzureDeveloperCliCredential | ManagedIdentityCredential
    azure_ai_token_provider: Callable[[], Awaitable[str]]
    if RUNNING_ON_AZURE:
        current_app.logger.info("Setting up Azure credential using ManagedIdentityCredential")
        if AZURE_CLIENT_ID := os.getenv("AZURE_CLIENT_ID"):
            # ManagedIdentityCredential should use AZURE_CLIENT_ID if set in env, but its not working for some reason,
            # so we explicitly pass it in as the client ID here. This is necessary for user-assigned managed identities.
            current_app.logger.info(
                "Setting up Azure credential using ManagedIdentityCredential with client_id %s", AZURE_CLIENT_ID
            )
            azure_credential = ManagedIdentityCredential(client_id=AZURE_CLIENT_ID)
        else:
            current_app.logger.info("Setting up Azure credential using ManagedIdentityCredential")
            azure_credential = ManagedIdentityCredential()
    elif AZURE_TENANT_ID:
        current_app.logger.info(
            "Setting up Azure credential using AzureDeveloperCliCredential with tenant_id %s", AZURE_TENANT_ID
        )
        azure_credential = AzureDeveloperCliCredential(tenant_id=AZURE_TENANT_ID, process_timeout=60)
    else:
        current_app.logger.info("Setting up Azure credential using AzureDeveloperCliCredential for home tenant")
        azure_credential = AzureDeveloperCliCredential(process_timeout=60)
    azure_ai_token_provider = get_bearer_token_provider(
        azure_credential, "https://cognitiveservices.azure.com/.default"
    )

    # Set the Azure credential in the app config for use in other parts of the app
    current_app.config[CONFIG_CREDENTIAL] = azure_credential

    # Set up clients for AI Search and Storage
    search_client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX,
        credential=azure_credential,
    )

    knowledgebase_client = KnowledgeBaseRetrievalClient(
        endpoint=AZURE_SEARCH_ENDPOINT, knowledge_base_name=AZURE_SEARCH_KNOWLEDGEBASE_NAME, credential=azure_credential
    )
    knowledgebase_client_with_web = None
    knowledgebase_client_with_sharepoint = None
    knowledgebase_client_with_web_and_sharepoint = None

    if AZURE_SEARCH_KNOWLEDGEBASE_NAME:
        if USE_WEB_SOURCE:
            knowledgebase_client_with_web = KnowledgeBaseRetrievalClient(
                endpoint=AZURE_SEARCH_ENDPOINT,
                knowledge_base_name=f"{AZURE_SEARCH_KNOWLEDGEBASE_NAME}-with-web",
                credential=azure_credential,
            )
        if USE_SHAREPOINT_SOURCE:
            knowledgebase_client_with_sharepoint = KnowledgeBaseRetrievalClient(
                endpoint=AZURE_SEARCH_ENDPOINT,
                knowledge_base_name=f"{AZURE_SEARCH_KNOWLEDGEBASE_NAME}-with-sp",
                credential=azure_credential,
            )
        if USE_WEB_SOURCE and USE_SHAREPOINT_SOURCE:
            knowledgebase_client_with_web_and_sharepoint = KnowledgeBaseRetrievalClient(
                endpoint=AZURE_SEARCH_ENDPOINT,
                knowledge_base_name=f"{AZURE_SEARCH_KNOWLEDGEBASE_NAME}-with-web-and-sp",
                credential=azure_credential,
            )

    # Set up the global blob storage manager (used for global content/images, but not user uploads)
    global_blob_manager = BlobManager(
        endpoint=f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=azure_credential,
        container=AZURE_STORAGE_CONTAINER,
        image_container=AZURE_IMAGESTORAGE_CONTAINER,
    )
    current_app.config[CONFIG_GLOBAL_BLOB_MANAGER] = global_blob_manager

    # Set up authentication helper
    search_index = None
    if AZURE_USE_AUTHENTICATION:
        current_app.logger.info("AZURE_USE_AUTHENTICATION is true, setting up search index client")
        search_index_client = SearchIndexClient(
            endpoint=AZURE_SEARCH_ENDPOINT,
            credential=azure_credential,
        )
        search_index = await search_index_client.get_index(AZURE_SEARCH_INDEX)
        await search_index_client.close()
    auth_helper = AuthenticationHelper(
        search_index=search_index,
        use_authentication=AZURE_USE_AUTHENTICATION,
        server_app_id=AZURE_SERVER_APP_ID,
        server_app_secret=AZURE_SERVER_APP_SECRET,
        client_app_id=AZURE_CLIENT_APP_ID,
        tenant_id=AZURE_AUTH_TENANT_ID,
        enforce_access_control=AZURE_ENFORCE_ACCESS_CONTROL,
        enable_unauthenticated_access=AZURE_ENABLE_UNAUTHENTICATED_ACCESS,
    )

    if USE_SPEECH_OUTPUT_AZURE:
        current_app.logger.info("USE_SPEECH_OUTPUT_AZURE is true, setting up Azure speech service")
        if not AZURE_SPEECH_SERVICE_ID or AZURE_SPEECH_SERVICE_ID == "":
            raise ValueError("Azure speech resource not configured correctly, missing AZURE_SPEECH_SERVICE_ID")
        if not AZURE_SPEECH_SERVICE_LOCATION or AZURE_SPEECH_SERVICE_LOCATION == "":
            raise ValueError("Azure speech resource not configured correctly, missing AZURE_SPEECH_SERVICE_LOCATION")
        current_app.config[CONFIG_SPEECH_SERVICE_ID] = AZURE_SPEECH_SERVICE_ID
        current_app.config[CONFIG_SPEECH_SERVICE_LOCATION] = AZURE_SPEECH_SERVICE_LOCATION
        current_app.config[CONFIG_SPEECH_SERVICE_VOICE] = AZURE_SPEECH_SERVICE_VOICE
        # Wait until token is needed to fetch for the first time
        current_app.config[CONFIG_SPEECH_SERVICE_TOKEN] = None

    openai_client, azure_openai_endpoint = setup_openai_client(
        openai_host=OPENAI_HOST,
        azure_credential=azure_credential,
        azure_openai_service=AZURE_OPENAI_SERVICE,
        azure_openai_custom_url=AZURE_OPENAI_CUSTOM_URL,
        azure_openai_api_key=AZURE_OPENAI_API_KEY_OVERRIDE,
        openai_api_key=OPENAI_API_KEY,
        openai_organization=OPENAI_ORGANIZATION,
    )

    user_blob_manager = None
    if USE_USER_UPLOAD:
        current_app.logger.info("USE_USER_UPLOAD is true, setting up user upload feature")
        if not AZURE_USERSTORAGE_ACCOUNT or not AZURE_USERSTORAGE_CONTAINER:
            raise ValueError(
                "AZURE_USERSTORAGE_ACCOUNT and AZURE_USERSTORAGE_CONTAINER must be set when USE_USER_UPLOAD is true"
            )
        if not AZURE_ENFORCE_ACCESS_CONTROL:
            raise ValueError("AZURE_ENFORCE_ACCESS_CONTROL must be true when USE_USER_UPLOAD is true")
        user_blob_manager = AdlsBlobManager(
            endpoint=f"https://{AZURE_USERSTORAGE_ACCOUNT}.dfs.core.windows.net",
            container=AZURE_USERSTORAGE_CONTAINER,
            credential=azure_credential,
        )
        current_app.config[CONFIG_USER_BLOB_MANAGER] = user_blob_manager

        # Set up ingester
        file_processors, figure_processor = setup_file_processors(
            azure_credential=azure_credential,
            document_intelligence_service=os.getenv("AZURE_DOCUMENTINTELLIGENCE_SERVICE"),
            local_pdf_parser=os.getenv("USE_LOCAL_PDF_PARSER", "").lower() == "true",
            local_html_parser=os.getenv("USE_LOCAL_HTML_PARSER", "").lower() == "true",
            use_content_understanding=os.getenv("USE_CONTENT_UNDERSTANDING", "").lower() == "true",
            content_understanding_endpoint=os.getenv("AZURE_CONTENTUNDERSTANDING_ENDPOINT"),
            use_multimodal=USE_MULTIMODAL,
            openai_client=openai_client,
            openai_model=OPENAI_CHATGPT_MODEL,
            openai_deployment=AZURE_OPENAI_CHATGPT_DEPLOYMENT if OPENAI_HOST == OpenAIHost.AZURE else None,
        )
        search_info = setup_search_info(
            search_service=AZURE_SEARCH_SERVICE,
            index_name=AZURE_SEARCH_INDEX,
            azure_credential=azure_credential,
            use_agentic_knowledgebase=USE_AGENTIC_KNOWLEDGEBASE,
            azure_openai_endpoint=azure_openai_endpoint,
            knowledgebase_name=AZURE_SEARCH_KNOWLEDGEBASE_NAME,
            azure_openai_knowledgebase_deployment=AZURE_OPENAI_KNOWLEDGEBASE_DEPLOYMENT,
            azure_openai_knowledgebase_model=AZURE_OPENAI_KNOWLEDGEBASE_MODEL,
        )

        text_embeddings_service = None
        if USE_VECTORS:
            text_embeddings_service = setup_embeddings_service(
                open_ai_client=openai_client,
                openai_host=OPENAI_HOST,
                emb_model_name=OPENAI_EMB_MODEL,
                emb_model_dimensions=OPENAI_EMB_DIMENSIONS,
                azure_openai_deployment=AZURE_OPENAI_EMB_DEPLOYMENT,
                azure_openai_endpoint=azure_openai_endpoint,
            )

        image_embeddings_service = setup_image_embeddings_service(
            azure_credential=azure_credential,
            vision_endpoint=AZURE_VISION_ENDPOINT,
            use_multimodal=USE_MULTIMODAL,
        )
        ingester = UploadUserFileStrategy(
            search_info=search_info,
            file_processors=file_processors,
            embeddings=text_embeddings_service,
            image_embeddings=image_embeddings_service,
            search_field_name_embedding=AZURE_SEARCH_FIELD_NAME_EMBEDDING,
            blob_manager=user_blob_manager,
            figure_processor=figure_processor,
        )
        current_app.config[CONFIG_INGESTER] = ingester

    image_embeddings_client = None
    if USE_MULTIMODAL:
        image_embeddings_client = ImageEmbeddings(AZURE_VISION_ENDPOINT, azure_ai_token_provider)

    current_app.config[CONFIG_OPENAI_CLIENT] = openai_client
    current_app.config[CONFIG_SEARCH_CLIENT] = search_client
    current_app.config[CONFIG_KNOWLEDGEBASE_CLIENT] = knowledgebase_client
    current_app.config[CONFIG_KNOWLEDGEBASE_CLIENT_WITH_WEB] = knowledgebase_client_with_web
    current_app.config[CONFIG_KNOWLEDGEBASE_CLIENT_WITH_SHAREPOINT] = knowledgebase_client_with_sharepoint
    current_app.config[CONFIG_KNOWLEDGEBASE_CLIENT_WITH_WEB_AND_SHAREPOINT] = (
        knowledgebase_client_with_web_and_sharepoint
    )
    current_app.config[CONFIG_AUTH_CLIENT] = auth_helper

    current_app.config[CONFIG_SEMANTIC_RANKER_DEPLOYED] = AZURE_SEARCH_SEMANTIC_RANKER != "disabled"
    current_app.config[CONFIG_QUERY_REWRITING_ENABLED] = (
        AZURE_SEARCH_QUERY_REWRITING == "true" and AZURE_SEARCH_SEMANTIC_RANKER != "disabled"
    )
    current_app.config[CONFIG_DEFAULT_REASONING_EFFORT] = OPENAI_REASONING_EFFORT
    current_app.config[CONFIG_DEFAULT_RETRIEVAL_REASONING_EFFORT] = AGENTIC_KNOWLEDGEBASE_REASONING_EFFORT
    current_app.config[CONFIG_REASONING_EFFORT_ENABLED] = OPENAI_CHATGPT_MODEL in Approach.GPT_REASONING_MODELS
    current_app.config[CONFIG_STREAMING_ENABLED] = (
        OPENAI_CHATGPT_MODEL not in Approach.GPT_REASONING_MODELS
        or Approach.GPT_REASONING_MODELS[OPENAI_CHATGPT_MODEL].streaming
    )
    current_app.config[CONFIG_VECTOR_SEARCH_ENABLED] = bool(USE_VECTORS)
    current_app.config[CONFIG_USER_UPLOAD_ENABLED] = bool(USE_USER_UPLOAD)
    current_app.config[CONFIG_LANGUAGE_PICKER_ENABLED] = ENABLE_LANGUAGE_PICKER
    current_app.config[CONFIG_SPEECH_INPUT_ENABLED] = USE_SPEECH_INPUT_BROWSER
    current_app.config[CONFIG_SPEECH_OUTPUT_BROWSER_ENABLED] = USE_SPEECH_OUTPUT_BROWSER
    current_app.config[CONFIG_SPEECH_OUTPUT_AZURE_ENABLED] = USE_SPEECH_OUTPUT_AZURE
    current_app.config[CONFIG_CHAT_HISTORY_BROWSER_ENABLED] = USE_CHAT_HISTORY_BROWSER
    current_app.config[CONFIG_CHAT_HISTORY_COSMOS_ENABLED] = USE_CHAT_HISTORY_COSMOS
    current_app.config[CONFIG_AGENTIC_KNOWLEDGEBASE_ENABLED] = USE_AGENTIC_KNOWLEDGEBASE
    current_app.config[CONFIG_MULTIMODAL_ENABLED] = USE_MULTIMODAL
    current_app.config[CONFIG_RAG_SEARCH_TEXT_EMBEDDINGS] = RAG_SEARCH_TEXT_EMBEDDINGS
    current_app.config[CONFIG_RAG_SEARCH_IMAGE_EMBEDDINGS] = RAG_SEARCH_IMAGE_EMBEDDINGS
    current_app.config[CONFIG_RAG_SEND_TEXT_SOURCES] = RAG_SEND_TEXT_SOURCES
    current_app.config[CONFIG_RAG_SEND_IMAGE_SOURCES] = RAG_SEND_IMAGE_SOURCES
    current_app.config[CONFIG_WEB_SOURCE_ENABLED] = USE_WEB_SOURCE
    if AGENTIC_KNOWLEDGEBASE_REASONING_EFFORT == "minimal" and current_app.config[CONFIG_WEB_SOURCE_ENABLED]:
        raise ValueError("Web source cannot be used with minimal retrieval reasoning effort")
    current_app.config[CONFIG_SHAREPOINT_SOURCE_ENABLED] = USE_SHAREPOINT_SOURCE

    current_app.config[CONFIG_QUERY_ROUTER_ENABLED] = QUERY_ROUTER_ENABLED
    current_app.config[CONFIG_QUERY_ROUTER_SCOPE_DESCRIPTION] = QUERY_ROUTER_SCOPE_DESCRIPTION
    current_app.config[CONFIG_QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE] = QUERY_ROUTER_OUT_OF_SCOPE_MESSAGE
    current_app.config[CONFIG_QUERY_ROUTER_MODEL] = OPENAI_CHATGPT_MODEL
    current_app.config[CONFIG_QUERY_ROUTER_DEPLOYMENT] = AZURE_OPENAI_CHATGPT_DEPLOYMENT

    prompt_manager = PromptManager()

    # ChatReadRetrieveReadApproach is used by /chat for multi-turn conversation
    current_app.config[CONFIG_CHAT_APPROACH] = ChatReadRetrieveReadApproach(
        search_client=search_client,
        search_index_name=AZURE_SEARCH_INDEX,
        knowledgebase_model=AZURE_OPENAI_KNOWLEDGEBASE_MODEL,
        knowledgebase_deployment=AZURE_OPENAI_KNOWLEDGEBASE_DEPLOYMENT,
        knowledgebase_client=knowledgebase_client,
        knowledgebase_client_with_web=knowledgebase_client_with_web,
        knowledgebase_client_with_sharepoint=knowledgebase_client_with_sharepoint,
        knowledgebase_client_with_web_and_sharepoint=knowledgebase_client_with_web_and_sharepoint,
        openai_client=openai_client,
        chatgpt_model=OPENAI_CHATGPT_MODEL,
        chatgpt_deployment=AZURE_OPENAI_CHATGPT_DEPLOYMENT,
        embedding_model=OPENAI_EMB_MODEL,
        embedding_deployment=AZURE_OPENAI_EMB_DEPLOYMENT,
        embedding_dimensions=OPENAI_EMB_DIMENSIONS,
        embedding_field=AZURE_SEARCH_FIELD_NAME_EMBEDDING,
        sourcepage_field=KB_FIELDS_SOURCEPAGE,
        content_field=KB_FIELDS_CONTENT,
        query_language=AZURE_SEARCH_QUERY_LANGUAGE,
        query_speller=AZURE_SEARCH_QUERY_SPELLER,
        prompt_manager=prompt_manager,
        reasoning_effort=OPENAI_REASONING_EFFORT,
        multimodal_enabled=USE_MULTIMODAL,
        image_embeddings_client=image_embeddings_client,
        global_blob_manager=global_blob_manager,
        user_blob_manager=user_blob_manager,
        use_web_source=current_app.config[CONFIG_WEB_SOURCE_ENABLED],
        use_sharepoint_source=current_app.config[CONFIG_SHAREPOINT_SOURCE_ENABLED],
        retrieval_reasoning_effort=AGENTIC_KNOWLEDGEBASE_REASONING_EFFORT,
        enforce_access_control=AZURE_ENFORCE_ACCESS_CONTROL,
    )


@bp.after_app_serving
async def close_clients():
    await current_app.config[CONFIG_SEARCH_CLIENT].close()
    await current_app.config[CONFIG_GLOBAL_BLOB_MANAGER].close_clients()
    if user_blob_manager := current_app.config.get(CONFIG_USER_BLOB_MANAGER):
        await user_blob_manager.close_clients()
    await current_app.config[CONFIG_CREDENTIAL].close()


def create_app():
    app = Quart(__name__)
    app.register_blueprint(bp)
    app.register_blueprint(chat_history_cosmosdb_bp)

    if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        app.logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING is set, enabling Azure Monitor")
        configure_azure_monitor(
            instrumentation_options={
                "django": {"enabled": False},
                "psycopg2": {"enabled": False},
                "fastapi": {"enabled": False},
            }
        )
        # This tracks HTTP requests made by aiohttp:
        AioHttpClientInstrumentor().instrument()
        # This tracks HTTP requests made by httpx:
        HTTPXClientInstrumentor().instrument()
        # This tracks OpenAI SDK requests:
        OpenAIInstrumentor().instrument()
        # This middleware tracks app route requests:
        app.asgi_app = OpenTelemetryMiddleware(app.asgi_app)  # type: ignore[ty:invalid-assignment]

    # Log levels should be one of https://docs.python.org/3/library/logging.html#logging-levels
    # Set root level to WARNING to avoid seeing overly verbose logs from SDKS
    logging.basicConfig(level=logging.WARNING)
    # Set our own logger levels to INFO by default
    app_level = os.getenv("APP_LOG_LEVEL", "INFO")
    app.logger.setLevel(os.getenv("APP_LOG_LEVEL", app_level))
    logging.getLogger("scripts").setLevel(app_level)

    if allowed_origin := os.getenv("ALLOWED_ORIGIN"):
        allowed_origins = allowed_origin.split(";")
        if len(allowed_origins) > 0:
            app.logger.info("CORS enabled for %s", allowed_origins)
            cors(app, allow_origin=allowed_origins, allow_methods=["GET", "POST"])

    return app
