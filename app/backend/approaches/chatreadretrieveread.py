import json
import re
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
    PBSGTransition,
    PBSGTurnClassification,
    active_fact_for_question,
    build_triage_state,
    classify_turn_interrupt,
    collapse_duplicate_route_cards,
    format_state_prompt,
    label_from_branch_key,
    load_golden_set_entries,
    parse_transition_outcome,
    question_text_from_entry,
    resolve_expected_transition,
    resolve_initial_topic,
    safe_escalation_response,
    validate_response_transition,
    validate_response_questions,
)
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
    "lab": "LAB is the Legal Aid Bureau, the government legal aid office for eligible Singapore Citizens or PRs facing civil or family proceedings.",
    "fjss": "FJSS refers to Family Justice Support Scheme pathways for eligible matrimonial and family matters.",
    "pchi": "PCHI means per capita household income. PCHI means total monthly household income divided by household members, including the applicant and all dependants.",
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

    def convert_question_to_second_person(self, question: str) -> str:
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

    def normalize_asked_question_text(self, content: Optional[str], extra_info: ExtraInfo) -> Optional[str]:
        if not content:
            return content

        selected_entry_matches = re.findall(r"\*\*Selected Entry:\*\*\s*([A-Z0-9-]+)", content, flags=re.IGNORECASE)
        selected_entry_id = selected_entry_matches[-1] if selected_entry_matches else None
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

        normalized_question = self.convert_question_to_second_person(canonical_question)
        replacement = (
            f"{match.group('prefix')}{match.group('quote')}{normalized_question}"
            f"{match.group('closing_quote')}{match.group('suffix')}"
        )
        start = marker_position + match.start()
        end = marker_position + match.end()
        return content[:start] + replacement + content[end:]

    def label_from_branch_key(self, branch_key: str) -> str:
        return label_from_branch_key(branch_key)

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
            QuickReplyOption(id=key, label=self.label_from_branch_key(key), value=self.label_from_branch_key(key))
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

        next_label = f"{transition.target_question_id} from {transition.target_entry_id}"
        question_prefix = transition.target_question_id
        if transition.transition_type == "nested_stream":
            next_label = f"{transition.target_entry_id} {transition.target_question_id} (Urgent concurrent path)"
            question_prefix = f"{transition.target_entry_id} {transition.target_question_id}"

        return "\n".join(
            [
                f"**Selected Entry:** {transition.entry_id}",
                "",
                "What I gathered from your description:",
                "",
                f"- {transition.question_id}: {self.label_from_branch_key(transition.branch_key)} [{transition.entry_id}.json]",
                "",
                "Triage progress:",
                "",
                f"- Last answered: {transition.question_id} = {self.label_from_branch_key(transition.branch_key)} → {transition.outcome} [{transition.entry_id}.json]",
                f"- Next question: {next_label}",
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **{question_prefix}: "{self.convert_question_to_second_person(question)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{transition.target_entry_id}.json]",
            ]
        )

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
        self.ensure_golden_set_source_entries(data_points, [resolution.entry_id, *resolution.overlays])

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
        return {
            "message": {"content": content, "role": "assistant"},
            "context": {
                "thoughts": extra_info.thoughts,
                "data_points": {
                    key: value for key, value in asdict(extra_info.data_points).items() if value is not None
                },
                "followup_questions": extra_info.followup_questions,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(deterministic_result.state),
            },
            "session_state": session_state,
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
        return QuickReply(
            mode="single",
            entryId=workflow_id,
            questionId="CONTINUE",
            options=[
                QuickReplyOption(
                    id=f"continue_queued_workflow:{workflow_id}",
                    label=f"Topic resolved - continue to {workflow_id}",
                    value=f"Continue queued workflow: {workflow_id}{label_suffix}",
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
        note = "\n".join(
            [
                "",
                "**Queued topic ready:**",
                "",
                f"- {workflow_id}: {topic}",
                "- Click the button when this routed topic has been resolved and you are ready to continue.",
                "",
                "Topics identified:",
                f"1. {triage_state.active_workflow or triage_state.workflow_id or 'Current workflow'} — routed workflow",
                f"2. {workflow_id} — queued workflow",
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
        active_line = f"1. {active_workflow} — active workflow" if active_workflow else "1. Current stream — active workflow"
        queued_lines = [
            f"{index}. {topic.entry_id} — queued workflow (noted from: {topic.evidence})"
            for index, topic in enumerate(unique_topics, start=2)
        ]
        note = "\n".join(
            [
                "",
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
        turn_classification = classify_turn_interrupt(self.pbsg_golden_set_entries, triage_state, latest_content)
        if turn_classification.should_call_llm:
            return None
        deterministic_result = self.pbsg_routing_engine.execute_locked_turn(messages[:-1], latest_content)
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

    def local_side_enquiry_answer(self, latest_content: str) -> str | None:
        normalized = latest_content.lower()
        if not re.search(r"\b(what is|what's|what does|explain|meaning of)\b", normalized):
            return None
        for term, answer in PBSG_GLOSSARY_ANSWERS.items():
            if re.search(rf"\b{re.escape(term)}\b", normalized):
                return answer
        return None

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

    def parse_continue_queued_workflow(self, latest_content: str) -> str | None:
        match = re.search(r"\bContinue queued workflow:\s*(GEN3-[A-Z0-9-]+)\b", latest_content, flags=re.IGNORECASE)
        return match.group(1).upper() if match else None

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
        content = "\n".join(
            [
                f"**Selected Entry:** {active_workflow}",
                "",
                f"**Note:** {active_workflow} has been routed. I will not start the queued topic until you confirm this topic is resolved.",
                "",
                "**Queued topic ready:**",
                "",
                f"- {next_workflow}: {next_topic}",
                "- Click the button when you are ready to continue.",
                "",
                "Topics identified:",
                f"1. {active_workflow} — routed workflow",
                f"2. {next_workflow} — queued workflow",
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
        return {
            "message": {"content": content, "role": "assistant"},
            "context": {
                "thoughts": extra_info.thoughts,
                "data_points": {key: value for key, value in asdict(data_points).items() if value is not None},
                "followup_questions": None,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(triage_state),
            },
            "session_state": session_state,
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
        if triage_state.routing_completion_status != "awaiting_topic_resolution" or not triage_state.queued_workflows:
            return None
        workflow_to_continue = self.parse_continue_queued_workflow(latest_content)
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
            question = question_text_from_entry(self.pbsg_golden_set_entries, entry_id, question_id)
            if not active_fact_for_question(triage_state.fact_ledger, question):
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
        selected_entry_matches = re.findall(r"\*\*Selected Entry:\*\*\s*(GEN3-[A-Z0-9-]+)", content, flags=re.IGNORECASE)
        selected_entry_id = selected_entry_matches[-1].upper() if selected_entry_matches else None
        if not selected_entry_id or selected_entry_id not in self.pbsg_golden_set_entries:
            return content
        targets = re.findall(r"Next question:\s*(Q\d+[A-Z]?)\b|>\s*\**(Q\d+[A-Z]?)\s*:", content, flags=re.IGNORECASE)
        target_question_id = None
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
        return "\n".join(
            [
                f"**Selected Entry:** {selected_entry_id}",
                "",
                "**Note:** I cannot rely on unverified answers for this workflow. We need to ask the first unanswered required question.",
                "",
                "Triage progress:",
                "",
                f"- Repaired skipped prerequisite: {missing_question_id} from {selected_entry_id}",
                f"- Next question: {missing_question_id} from {selected_entry_id}",
                "",
                "**Ask the applicant (read verbatim):**",
                "",
                f'> **{missing_question_id}: "{self.convert_question_to_second_person(question)}"**',
                "",
                f"Type the applicant's answer here and I will determine the next question or route. [{selected_entry_id}.json]",
            ]
        )

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
        content_lines = [
            f"**Selected Entry:** {entry_id}",
            "",
            f"**Note:** {note}",
        ]
        if turn_classification.reason and turn_classification.turn_type == "clarification":
            content_lines.extend(["", "**Clarification:**", "", turn_classification.reason])
        if turn_classification.new_topics:
            content_lines.extend(
                [
                    "",
                    "**Queued topic note:** I noted a separate possible topic and will handle it after this stream is routed.",
                    "",
                    "Topics identified:",
                    f"1. {triage_state.active_workflow or entry_id} — active workflow",
                ]
            )
            content_lines.extend(
                f"{index}. {topic.entry_id} — queued workflow (noted from: {topic.evidence})"
                for index, topic in enumerate(turn_classification.new_topics, start=2)
            )
        if question_id and question:
            content_lines.extend(
                [
                    "",
                    "Triage progress:",
                    "",
                    f"- Current question remains: {question_id} from {entry_id}",
                    "",
                    "**Ask the applicant (read verbatim):**",
                    "",
                    f'> **{question_id}: "{self.convert_question_to_second_person(question)}"**',
                ]
            )
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
        return {
            "message": {"content": content, "role": "assistant"},
            "context": {
                "thoughts": extra_info.thoughts,
                "data_points": {key: value for key, value in asdict(data_points).items() if value is not None},
                "followup_questions": None,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
                "pbsg_triage_state": asdict(triage_state),
            },
            "session_state": session_state,
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
        initial_response = self.try_deterministic_initial_response(messages, session_state)
        if initial_response:
            return initial_response
        deterministic_response = self.try_deterministic_locked_response(messages, session_state)
        if deterministic_response:
            return deterministic_response
        side_enquiry_response = self.try_local_side_enquiry_response(messages, session_state)
        if side_enquiry_response:
            return side_enquiry_response
        queued_topic_response = self.try_queued_topic_control_response(messages, session_state)
        if queued_topic_response:
            return queued_topic_response
        structured_response = await self.try_structured_llm_locked_response(messages, overrides, session_state)
        if structured_response:
            return structured_response

        extra_info, chat_coroutine = await self.run_until_final_call(
            messages, overrides, auth_claims, should_stream=False
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
        extra_info.quick_reply = self.build_quick_reply(content, extra_info)
        # Assume last thought is for generating answer
        # TODO: Update for agentic? This isn't still true?
        if self.include_token_usage and extra_info.thoughts and chat_completion_response.usage:
            extra_info.thoughts[-1].update_token_usage(chat_completion_response.usage)
        chat_app_response = {
            "message": {"content": content, "role": role},
            "context": {
                "thoughts": extra_info.thoughts,
                "data_points": {
                    key: value for key, value in asdict(extra_info.data_points).items() if value is not None
                },
                "followup_questions": extra_info.followup_questions,
                "quick_reply": asdict(extra_info.quick_reply) if extra_info.quick_reply else None,
            },
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
        initial_response = self.try_deterministic_initial_response(messages, session_state)
        if initial_response:
            yield {"delta": {"role": "assistant"}, "context": initial_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": initial_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": initial_response["context"], "session_state": session_state}
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
        queued_topic_response = self.try_queued_topic_control_response(messages, session_state)
        if queued_topic_response:
            yield {"delta": {"role": "assistant"}, "context": queued_topic_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": queued_topic_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": queued_topic_response["context"], "session_state": session_state}
            return
        structured_response = await self.try_structured_llm_locked_response(messages, overrides, session_state)
        if structured_response:
            yield {"delta": {"role": "assistant"}, "context": structured_response["context"], "session_state": session_state}
            yield {"delta": {"role": "assistant", "content": structured_response["message"]["content"]}}
            yield {"delta": {"role": "assistant"}, "context": structured_response["context"], "session_state": session_state}
            return

        extra_info, chat_coroutine = await self.run_until_final_call(
            messages, overrides, auth_claims, should_stream=True
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
            extra_info = await self.run_search_approach(messages, overrides, auth_claims)

        if extra_info.answer:
            # If agentic retrieval already provided an answer, skip final call to LLM
            async def return_answer() -> ChatCompletion:
                return self.chat_completion_from_content(extra_info.answer or "")

            return (extra_info, return_answer())

        golden_set_entries = self.extract_golden_set_entries(extra_info.data_points.text)
        triage_state_prompt = ""
        if isinstance(original_user_query, str) and golden_set_entries:
            triage_state = build_triage_state(messages[:-1], golden_set_entries, original_user_query)
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
        self, messages: list[ChatCompletionMessageParam], overrides: dict[str, Any], auth_claims: dict[str, Any]
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

        rewrite_result = await self.rewrite_query(
            prompt_template="query_rewrite.system.jinja2",
            prompt_variables={
                "user_query": original_user_query,
                "past_messages": messages[:-1],
            },
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
