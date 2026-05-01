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
