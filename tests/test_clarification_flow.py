"""
Tests for the clarification question handling during triage.

Verifies that when an applicant asks a clarification question mid-triage
(e.g. "what's a legal aid bureau?"), the system:
1. Does NOT block it via the query router
2. Anchors search retrieval to the same triage topic
3. Includes OUTPUT E instructions in the system prompt
"""

import pytest

from approaches.promptmanager import PromptManager
from query_router import is_obvious_in_scope, is_obvious_non_query


class TestQueryRouterClarificationQuestions:
    """Clarification questions about legal terms/schemes must not be blocked."""

    @pytest.mark.parametrize(
        "question",
        [
            "what's a legal aid bureau?",
            "what is LAB?",
            "what does PCHI mean?",
            "what is FJSS?",
            "how does the means test work?",
            "what is CLAS?",
            "what does pro bono mean?",
            "what is the legal aid bureau?",
            "what does PDO stand for?",
            "what's PBSG?",
        ],
    )
    def test_clarification_questions_are_in_scope(self, question):
        assert not is_obvious_non_query(question), f"Should not be treated as non-query: {question}"
        assert is_obvious_in_scope(question), f"Should be in scope: {question}"

    @pytest.mark.parametrize(
        "question",
        [
            "what is a HDB?",
            "what does SGC mean?",
            "what's a PPO?",
        ],
    )
    def test_general_acronyms_not_blocked_as_non_query(self, question):
        assert not is_obvious_non_query(question)


class TestQueryRewritePromptClarification:
    """The query rewrite prompt must instruct the model to anchor on the same topic for clarification questions."""

    def setup_method(self):
        self.prompt_manager = PromptManager()

    def test_rewrite_prompt_contains_clarification_instruction(self):
        rendered = self.prompt_manager.build_system_prompt(
            "query_rewrite.system.jinja2",
            {
                "user_query": "what's a legal aid bureau?",
                "past_messages": [
                    {"role": "user", "content": "applicant wants to file for divorce, she is a singapore citizen"},
                    {
                        "role": "assistant",
                        "content": "Ask the applicant: Q3: Have you applied to the Legal Aid Bureau (LAB)?",
                    },
                ],
            },
        )
        content = rendered["content"]
        assert "clarification question" in content.lower()
        assert "what is LAB" in content or "what's a legal aid bureau" in content
        assert "same legal topic" in content or "same Golden Set entry" in content

    def test_rewrite_prompt_preserves_followup_answer_logic(self):
        rendered = self.prompt_manager.build_system_prompt(
            "query_rewrite.system.jinja2",
            {
                "user_query": "yes",
                "past_messages": [
                    {"role": "user", "content": "applicant wants to file for divorce"},
                    {"role": "assistant", "content": "Ask the applicant: Q1: Is there active family violence?"},
                ],
            },
        )
        content = rendered["content"]
        assert "short follow-up answer" in content


class TestSystemPromptClarificationOutput:
    """The system prompt must include OUTPUT E for clarification handling."""

    def setup_method(self):
        self.prompt_manager = PromptManager()

    def test_system_prompt_contains_output_e(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "OUTPUT E" in content
        assert "Clarification" in content

    def test_system_prompt_contains_deterministic_phase_contract(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T01.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "TWO-PHASE EXECUTION MODEL" in content
        assert "PHASE 1 — Workflow identification" in content
        assert "PHASE 2 — Deterministic workflow execution" in content
        assert "MANDATORY INTERNAL CHECK BEFORE EVERY RESPONSE" in content
        assert "do not display this as JSON" in content
        assert "If any check fails, stop and escalate rather than guessing" in content

    def test_system_prompt_contains_three_mode_contract(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T01.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "THREE-MODE SYSTEM ARCHITECTURE" in content
        assert "MODE 1 — ORCHESTRATION MODE" in content
        assert "MODE 2 — FAST ROUTING MODE" in content
        assert "MODE 3 — REPAIR MODE" in content
        assert "For simple answers" in content
        assert "execute the branch immediately" in content
        assert "Invalidate all downstream decisions dependent on the contradicted answer" in content
        assert "Do not expose dependency graphs" in content

    def test_system_prompt_contains_multi_workflow_orchestration_rules(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T01.json", "GEN3-T02.json", "GEN3-T03.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "MULTI-WORKFLOW DETECTION AND ORDERING" in content
        assert "Do not assume one narrative = one workflow" in content
        assert "Only one workflow may be active at a time" in content
        assert "active_workflow, queued_workflows, completed_workflows, concurrent_monitors" in content
        assert "first-contact and gating workflows come before downstream substantive routing" in content
        assert "They may interrupt only when the urgency threshold is met" in content
        assert "Never improvise cross-workflow logic" in content

    def test_system_prompt_output_e_instructions(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Do NOT advance the triage flow" in content
        assert "Do NOT treat the clarification question as an answer" in content
        assert "re-ask" in content.lower() or "Re-ask" in content
        assert "Back to triage" in content

    def test_system_prompt_how_to_decide_includes_output_e(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "clarification question" in content.lower()
        decision_section_idx = content.find("HOW TO DECIDE WHICH OUTPUT TO USE")
        assert decision_section_idx != -1
        decision_section = content[decision_section_idx:]
        assert "OUTPUT E" in decision_section

    def test_system_prompt_does_not_treat_proceed_to_question_as_route_script(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Use OUTPUT B whenever the matched `branching_logic` outcome is **Proceed to Q<next>**" in content
        assert "The next question appears exactly once" in content
        assert "Tell the applicant` is for terminal Route scripts only" in content
        assert "Do not include `Routing Recommendation`" in content

    def test_system_prompt_final_routes_are_expanded_only_for_terminal_routes(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Final routing must be extensive and intern-friendly" in content
        assert "Use OUTPUT A only when the matched `branching_logic` outcome is a terminal **Route <letter>**" in content
        assert "Do **not** use OUTPUT A for `Proceed to Q<next>` outcomes" in content
        assert "Use OUTPUT B whenever the matched `branching_logic` outcome is **Proceed to Q<next>**" in content
        assert "The next question appears exactly once" in content

    def test_system_prompt_final_route_output_contains_clear_handoff_sections(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "**Why this route applies:**" in content
        assert "**What the applicant needs to know:**" in content
        assert "**How to access this route:**" in content
        assert "**What the applicant should prepare:**" in content
        assert "**Next steps for you (the intern):**" in content
        assert "**Important caveat:**" in content

    def test_system_prompt_final_routes_include_source_route_access_details_without_invention(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Pull route details from the active selected entry's `routing` array entry matching **Route <letter>**" in content
        assert "Include all available links, phone numbers, emails, addresses, opening hours" in content
        assert "appointment instructions, application steps, and counter/in-person fallback instructions" in content
        assert "Do not invent contact details, documents, addresses, deadlines, eligibility criteria, or application steps" in content
        assert "For escalation routes, clearly list the exact details to collect" in content
        assert "For third-party resources, include the caveat from the matching route text" in content

    def test_system_prompt_locks_proceed_to_question_to_selected_entry(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T01.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Active stream lock" in content
        assert "Q` numbers are local to each Golden Set entry, not global" in content
        assert "every `Proceed to Q<n>` transition MUST resolve `Q<n>` from that same entry" in content
        assert "Do not use `GEN3-T01` questions unless `Selected Entry` is `GEN3-T01`" in content
        assert "Selected Entry` controls the lookup source" in content
        assert "Never show raw paths like `.branching_logic.` in staff-facing output" in content
        assert "Next question: Q4 (Means) from GEN3-T04" in content

    def test_system_prompt_keeps_followups_inside_selected_topic_stream(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T01.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Topic Stream Authority" in content
        assert "Selected Entry` is the active state authority across turns" in content
        assert "do not re-run `GEN3-T01` first-contact questions" in content
        assert "do not ask `GEN3-T01 Q1`" in content
        assert "or `GEN3-T01 Q5`" in content
        assert "`GEN3-T04 Q1 = Yes` means `Proceed to Q2`" in content

    def test_system_prompt_contains_strict_branch_lookup_rule(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Strict Branch Lookup Rule" in content
        assert "the next step is determined ONLY by looking up the pending question's matching `if_*` key" in content
        assert "Do not infer the next step from semantic similarity" in content
        assert "GEN3-T03 Q2 = No, foreigner` → `if_no_foreigner` → **Proceed to Q4** in `GEN3-T03`" in content
        assert "That is NOT a handoff to `GEN3-T04`" in content
        assert "Do NOT treat `Proceed to Q<n>` as a handoff" in content

    def test_system_prompt_contains_gen3_t03_q2_no_foreigner_to_q4_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Example — GEN3-T03 Q2 = No, foreigner" in content
        assert "GEN3-T03.branching_logic.Q2.if_no_foreigner" in content
        assert "Proceed to Q4 (FJSS Pro Bono pathway)" in content
        assert "Stay in `GEN3-T03`" in content
        assert "Next question: Q4 (Foreigner path) from GEN3-T03" in content
        assert "Do you have at least one Singaporean child under 21?" in content
        assert "Do NOT switch to `GEN3-T04` here" in content
        assert "Do NOT carry over to `GEN3-T04 Q1`" in content
        assert "GEN3-T04` is reached only after `GEN3-T03 Q4 = No`" in content

    def test_system_prompt_contains_cross_topic_handoff_state_transfer_rule(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Cross-Topic Handoff State Transfer Rule" in content
        assert "Handoffs happen ONLY when the active entry's `branching_logic` explicitly says `Proceed to GEN3-Txx`" in content
        assert "Do not hand off early just because a parent answer would also answer a target entry's Q1" in content
        assert "Applies ONLY after an explicit `Proceed to GEN3-Txx` handoff" in content
        assert "While still in the parent entry, follow the parent's `branching_logic` only" in content
        assert "do not ask a bridge/topic-selection question" in content
        assert "If a carried-over answer resolves the target entry's Q1" in content

    def test_system_prompt_contains_gen3_t03_q4_no_to_gen3_t04_q4_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json", "GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Example — GEN3-T03 Q4 = No handoff to GEN3-T04" in content
        assert "GEN3-T03.branching_logic.Q4.if_no" in content
        assert "Even if the latest user message is only \"No\" answering `GEN3-T03 Q4`" in content
        assert "Use the full conversation state, not only the latest user message" in content
        assert "Switch the active selected entry to `GEN3-T04`" in content
        assert "`GEN3-T03 Q2 = No, foreigner` answers `GEN3-T04 Q1 = No, foreigner`" in content
        assert "GEN3-T04.branching_logic.Q1.if_no_foreigner" in content
        assert "Because `GEN3-T04 Q1` is already answered by carryover, do not ask `GEN3-T04 Q1` again" in content
        assert "Carried over: GEN3-T04 Q1 = No, foreigner → Proceed to Q4" in content
        assert "Next question: Q4 (Means) from GEN3-T04" in content
        assert "Per Capita Household Income (PCHI) ≤ S$5,000" in content
        assert "Do NOT ask \"Are you a Singapore Citizen or PR?\"" in content
        assert "Do NOT ask \"Is the issue about a civil matter or general legal issue?\"" in content

    def test_system_prompt_fresh_stream_starts_at_q1(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Fresh Stream Start Rule" in content
        assert "the current question is that stream's `Q1`" in content
        assert "Later-question facts may be remembered as background" in content
        assert "must not be displayed as answered triage state or used for routing" in content
        assert "charged in state courts" in content
        assert "only background until `GEN3-T02 Q1` and `GEN3-T02 Q2` have been answered" in content

    def test_system_prompt_contains_gen3_t02_charged_message_starts_at_q1_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert 'fresh GEN3-T02 message says "caller has been charged in state courts"' in content
        assert "This identifies the topic as `GEN3-T02`" in content
        assert "Current question: Q1 from GEN3-T02" in content
        assert "Background noted: charged in court, but Q3 is not yet validated" in content
        assert "Next question: Q1 from GEN3-T02" in content
        assert "Is the offence a capital offence (punishable with death)?" in content
        assert "Do NOT show `Q3: charged in court -> Yes`" in content
        assert "Do NOT ask `GEN3-T02 Q3` first" in content

    def test_system_prompt_contains_gen3_t02_q1_no_to_q2_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json", "GEN3-T06.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Same-Topic Transition Rule" in content
        assert "Cross-Reference Gate Rule" in content
        assert "Example — GEN3-T02 Q1 = No" in content
        assert "GEN3-T02.branching_logic.Q1.if_no" in content
        assert "Proceed to Q2" in content
        assert "GEN3-T02.branching_logic.Q2.question" in content
        assert "Is there a court date/deadline within 14 days?" in content
        assert "Do NOT ask \"Is there a specific legal deadline within 14 days" in content
        assert "That is `GEN3-T06 Q3`, and `GEN3-T06` has not been invoked yet" in content

    def test_system_prompt_contains_gen3_t02_route_d_to_nested_gen3_t06_q1_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json", "GEN3-T06.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Example — GEN3-T02 Q2 = Yes" in content
        assert "GEN3-T02.branching_logic.Q2.if_yes" in content
        assert "Route D (CLAS + Urgent concurrent — cross-reference GEN3-T06)" in content
        assert "Now, and only now, start the nested urgent leg" in content
        assert "Nested `GEN3-T06` starts at `GEN3-T06 Q1`" in content
        assert "Next question: GEN3-T06 Q1 (Urgent concurrent path)" in content
        assert "GEN3-T06 Q1: \"Is there an immediate threat to your (or someone else's) life or physical safety right now?\"" in content

    def test_system_prompt_forbids_later_question_state_before_prerequisites(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T02.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Prerequisite-state display rule" in content
        assert "`What I gathered` may include a short `Background noted` line" in content
        assert "do not list those later questions as answered" in content
        assert "do not show `Q3: charged in court -> Yes` in `GEN3-T02`" in content
        assert "until after `GEN3-T02 Q1` and `GEN3-T02 Q2` are resolved" in content

    def test_system_prompt_contains_gen3_t04_yes_to_representation_guidance_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Example — GEN3-T04 Q1 = Yes" in content
        assert "GEN3-T04.branching_logic.Q1.if_yes" in content
        assert "GEN3-T04.branching_logic.Q2.question" in content
        assert "Last answered: Q1 = Yes → Proceed to Q2 (SGC/PR path)" in content
        assert "Next question: Q2 (SGC/PR path) from GEN3-T04" in content
        assert "Are you seeking representation (a lawyer to act for you) or guidance (initial advice)?" in content
        assert "Do NOT ask \"Are you currently represented by a lawyer on this same matter?\"" in content
        assert "That is `GEN3-T01 Q1`" in content

    def test_system_prompt_contains_gen3_t04_foreigner_to_means_question_example(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Example — GEN3-T04 Q1 = No, foreigner" in content
        assert "GEN3-T04.branching_logic.Q1.if_no_foreigner" in content
        assert "GEN3-T04.branching_logic.Q4.question" in content
        assert "Per Capita Household Income (PCHI) ≤ S$5,000" in content
        assert "Last answered: Q1 = No, foreigner → Proceed to Q4 (Means)" in content
        assert "Next question: Q4 (Means) from GEN3-T04" in content
        assert "Next question: Q4 from `GEN3-T04.branching_logic.Q4.question`" not in content
        assert "Do NOT ask \"What type of matter are you facing?\"" in content
        assert "That is `GEN3-T01 Q5`" in content

    def test_system_prompt_visible_output_uses_friendly_question_labels(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T04.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "Next question: Q<next> (<short label, if any>) from <ENTRY_ID>" in content
        assert "Do not expose raw lookup paths like `.branching_logic.` in visible text" in content
        assert "Derive the short label from the parenthetical label in `triage_questions`" in content
        assert "Q3 (Representation, SGC/PR)" in content

    def test_system_prompt_clarification_does_not_break_other_outputs(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json"],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "OUTPUT A" in content
        assert "OUTPUT B" in content
        assert "OUTPUT C" in content
        assert "OUTPUT D" in content
        assert "OUTPUT E" in content

    def test_system_prompt_override_skips_clarification(self):
        rendered = self.prompt_manager.build_system_prompt(
            "chat_answer.system.jinja2",
            {
                "override_prompt": "You are a custom bot. Do whatever.",
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": [],
                "injected_prompt": "",
            },
        )
        content = rendered["content"]
        assert "OUTPUT E" not in content
        assert "You are a custom bot" in content


class TestConversationBuildWithClarification:
    """Full conversation build includes clarification context in system prompt."""

    def setup_method(self):
        self.prompt_manager = PromptManager()

    def test_full_conversation_with_clarification_mid_triage(self):
        messages = self.prompt_manager.build_conversation(
            system_template_path="chat_answer.system.jinja2",
            system_template_variables={
                "override_prompt": None,
                "include_follow_up_questions": False,
                "image_sources": None,
                "citations": ["GEN3-T03.json"],
                "injected_prompt": "",
            },
            user_template_path="chat_answer.user.jinja2",
            user_template_variables={
                "user_query": "what's a legal aid bureau?",
                "text_sources": ['GEN3-T03.json: {"id": "GEN3-T03", "topic": "Matrimonial Stream Triage"}'],
            },
            past_messages=[
                {"role": "user", "content": "applicant wants to file for divorce, she is a singapore citizen"},
                {
                    "role": "assistant",
                    "content": (
                        "**Selected Entry:** GEN3-T03\n\n"
                        "Ask the applicant:\n\n"
                        '> Q3: "Have you applied to the Legal Aid Bureau (LAB) for help with this family matter yet?"'
                    ),
                },
            ],
        )

        assert len(messages) == 4  # system + 2 history + user
        system_content = messages[0]["content"]
        assert "OUTPUT E" in system_content
        assert "Clarification" in system_content

        user_content = messages[-1]["content"]
        assert "what's a legal aid bureau?" in user_content
        assert "GEN3-T03.json" in user_content
