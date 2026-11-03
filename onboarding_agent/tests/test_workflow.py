"""Tests for the LangGraph onboarding workflow."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from onboarding_agent.agents.intake_agent import IntakeAgent
from onboarding_agent.agents.communication_agent import CommunicationAgent
from onboarding_agent.graph import set_agents, build_onboarding_graph
from onboarding_agent.guardrails import get_audit_trail
from onboarding_agent.models import (
    DealEmail,
    OnboardingProject,
    PlanTier,
    SlackChannel,
    VoiceCallResult,
)


SAMPLE_EMAIL = {
    "message_id": "msg_wf_001",
    "from": "john.smith@novacrm.com",
    "subject": "New Deal Closed - Acme Corp",
    "body": (
        "Customer Name: Acme Corp\n"
        "Customer Contact: jane.doe@acmecorp.com\n"
        "AE: John Smith\n"
        "AE Phone: +1-555-123-4567\n"
        "Salesforce Opportunity: https://novacrm.salesforce.com/opportunity/006ABC\n"
    ),
}


def _make_llm_response(parsed: dict) -> MagicMock:
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(parsed)
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    return mock_resp


def _parsed_fields() -> dict:
    return {
        "customer_name": "Acme Corp",
        "customer_contact_email": "jane.doe@acmecorp.com",
        "ae_name": "John Smith",
        "ae_phone": "+1-555-123-4567",
        "salesforce_opportunity_link": "https://novacrm.salesforce.com/opportunity/006ABC",
    }


def _setup_agents(
    voice_result: VoiceCallResult | None = None,
    existing_projects: list[dict] | None = None,
    project: OnboardingProject | None = None,
    slack_channel: SlackChannel | None = None,
    llm_parsed: dict | None = None,
):
    """Wire up mocked agents and inject them into the graph nodes."""
    llm_client = MagicMock()
    llm_client.chat.completions.create.return_value = _make_llm_response(
        llm_parsed or _parsed_fields()
    )

    voice_client = MagicMock()
    if voice_result:
        voice_client.call_ae_for_plan_confirmation.return_value = voice_result

    rocketlane_client = MagicMock()
    rocketlane_client.check_existing_projects.return_value = existing_projects or []
    rocketlane_client.get_template_id.return_value = "tpl_001"
    if project:
        rocketlane_client.create_project.return_value = project

    slack_client = MagicMock()
    if slack_channel:
        slack_client.create_onboarding_channel.return_value = slack_channel

    intake = IntakeAgent(
        voice_client=voice_client,
        rocketlane_client=rocketlane_client,
        llm_client=llm_client,
    )
    comms = CommunicationAgent(slack_client=slack_client)
    set_agents(intake, comms)
    return intake, comms


# ── Happy Path ──────────────────────────────────────────────────────

class TestWorkflowHappyPath:

    def test_enterprise_end_to_end(self):
        project = OnboardingProject(
            project_id="proj_e2e",
            project_name="Acme Corp - Onboarding",
            customer_name="Acme Corp",
            plan_tier=PlanTier.ENTERPRISE,
            template_name="Enterprise Onboarding - 30 Day",
            duration_days=30,
            csm_type="dedicated",
        )
        channel = SlackChannel(
            channel_id="C_E2E",
            channel_name="onb-acme-corp-enterprise",
            topic="Onboarding: Acme Corp",
            welcome_message="Welcome!",
            customer_name="Acme Corp",
            plan_tier=PlanTier.ENTERPRISE,
        )
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc1", status="completed",
                plan_tier=PlanTier.ENTERPRISE, confidence=0.95,
                transcript="Enterprise confirmed", duration_seconds=30,
            ),
            project=project,
            slack_channel=channel,
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] == "completed"
        assert result["plan_tier"] == "enterprise"
        assert result["project_data"]["project_id"] == "proj_e2e"
        assert result["project_data"]["duration_days"] == 30
        assert result["project_data"]["csm_type"] == "dedicated"
        assert result["slack_channel_data"]["channel_name"] == "onb-acme-corp-enterprise"

    def test_growth_end_to_end(self):
        project = OnboardingProject(
            project_id="proj_g",
            project_name="SmallCo - Onboarding",
            customer_name="SmallCo",
            plan_tier=PlanTier.GROWTH,
            template_name="Growth Onboarding - 14 Day",
            duration_days=14,
            csm_type="pooled",
        )
        channel = SlackChannel(
            channel_id="C_G",
            channel_name="onb-smallco-growth",
            topic="Onboarding: SmallCo",
            welcome_message="Welcome!",
            customer_name="SmallCo",
            plan_tier=PlanTier.GROWTH,
        )
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc2", status="completed",
                plan_tier=PlanTier.GROWTH, confidence=0.90,
                transcript="Growth plan", duration_seconds=25,
            ),
            llm_parsed={
                "customer_name": "SmallCo",
                "customer_contact_email": "hi@smallco.com",
                "ae_name": "Alice",
                "ae_phone": "+15559876543",
                "salesforce_opportunity_link": "https://salesforce.com/opp/2",
            },
            project=project,
            slack_channel=channel,
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] == "completed"
        assert result["plan_tier"] == "growth"
        assert result["project_data"]["duration_days"] == 14
        assert result["project_data"]["csm_type"] == "pooled"


# ── Validation Failures ─────────────────────────────────────────────

class TestWorkflowValidation:

    def test_malformed_email_escalates(self):
        _setup_agents(
            llm_parsed={
                "customer_name": None,
                "customer_contact_email": None,
                "ae_name": None,
                "ae_phone": None,
                "salesforce_opportunity_link": None,
            },
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] in ("failed", "escalated")
        assert result.get("project_data") is None

    def test_llm_garbage_escalates(self):
        llm_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "I cannot help with that."
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        llm_client.chat.completions.create.return_value = mock_resp
        intake = IntakeAgent(
            voice_client=MagicMock(),
            rocketlane_client=MagicMock(),
            llm_client=llm_client,
        )
        set_agents(intake, CommunicationAgent(slack_client=MagicMock()))

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] in ("failed", "escalated")


# ── Voice Call Edge Cases ───────────────────────────────────────────

class TestWorkflowVoiceEdgeCases:

    def test_ae_no_answer_escalates(self):
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc_na", status="no_answer",
            ),
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] == "escalated"
        assert result.get("project_data") is None

    def test_ambiguous_response_escalates(self):
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc_amb", status="completed",
                plan_tier=None, confidence=0.3,
                transcript="Maybe enterprise or growth, not sure",
                duration_seconds=40,
            ),
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] == "escalated"
        assert result["voice_attempts"] >= 2


# ── Duplicate Project ───────────────────────────────────────────────

class TestWorkflowDuplicate:

    def test_duplicate_project_escalates(self):
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc_dup", status="completed",
                plan_tier=PlanTier.ENTERPRISE, confidence=0.95,
                transcript="Enterprise confirmed", duration_seconds=30,
            ),
            existing_projects=[
                {"name": "Acme Corp - Onboarding", "id": "existing_1", "status": "active"}
            ],
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] in ("failed", "escalated")
        assert result["duplicate_found"] is True


# ── Rocketlane API Down ─────────────────────────────────────────────

class TestWorkflowAPIFailure:

    def test_rocketlane_down_fails(self):
        import requests as req
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc_api", status="completed",
                plan_tier=PlanTier.ENTERPRISE, confidence=0.95,
                transcript="Enterprise", duration_seconds=30,
            ),
        )
        # Make create_project raise
        from onboarding_agent.graph.nodes import _get_intake
        _get_intake().rocketlane_client.create_project.side_effect = req.ConnectionError(
            "Rocketlane unreachable"
        )

        workflow = build_onboarding_graph()
        result = workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        assert result["status"] in ("failed", "escalated")
        assert result.get("project_data") is None


# ── Audit Trail ─────────────────────────────────────────────────────

class TestWorkflowAuditTrail:

    def test_happy_path_logs_all_steps(self):
        project = OnboardingProject(
            project_id="proj_audit",
            project_name="AuditCo - Onboarding",
            customer_name="AuditCo",
            plan_tier=PlanTier.GROWTH,
            template_name="Growth Onboarding - 14 Day",
            duration_days=14,
            csm_type="pooled",
        )
        _setup_agents(
            voice_result=VoiceCallResult(
                call_id="vc_a", status="completed",
                plan_tier=PlanTier.GROWTH, confidence=0.90,
                transcript="Growth confirmed", duration_seconds=20,
            ),
            project=project,
            slack_channel=SlackChannel(
                channel_id="C_A", channel_name="onb-auditco-growth",
                topic="t", welcome_message="w",
                customer_name="AuditCo", plan_tier=PlanTier.GROWTH,
            ),
        )

        workflow = build_onboarding_graph()
        workflow.invoke({
            "raw_email": SAMPLE_EMAIL,
            "voice_attempts": 0,
            "status": "processing",
            "error": "",
        })

        trail = get_audit_trail()
        agent_names = {a.agent_name for a in trail}

        assert any("parse" in a.action for a in trail)
        assert any("voice" in a.action or "call" in a.action for a in trail)
        assert any("duplicate" in a.action or "check" in a.action for a in trail)
        assert len(trail) >= 5
