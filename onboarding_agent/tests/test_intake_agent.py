"""Tests for the Intake & Routing Agent."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from onboarding_agent.agents.intake_agent import IntakeAgent
from onboarding_agent.guardrails import get_audit_trail
from onboarding_agent.models import DealEmail, OnboardingProject, PlanTier, VoiceCallResult
from onboarding_agent.tests.conftest import (
    VALID_DEAL_EMAIL,
    MALFORMED_EMAIL_MISSING_NAME,
    MALFORMED_EMAIL_NO_CONTACT,
)


def _make_llm_response(parsed_fields: dict) -> MagicMock:
    """Create a mock LLM response returning JSON (Groq/OpenAI format)."""
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(parsed_fields)
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


def _make_agent(
    llm_parsed: dict | None = None,
    voice_result: VoiceCallResult | None = None,
    existing_projects: list[dict] | None = None,
    create_project_result: dict | None = None,
    template_id: str | None = "tpl_001",
) -> IntakeAgent:
    """Build an IntakeAgent with fully mocked dependencies."""
    llm_client = MagicMock()
    if llm_parsed is not None:
        llm_client.chat.completions.create.return_value = _make_llm_response(llm_parsed)

    voice_client = MagicMock()
    if voice_result is not None:
        voice_client.call_ae_for_plan_confirmation.return_value = voice_result

    rocketlane_client = MagicMock()
    rocketlane_client.check_existing_projects.return_value = existing_projects or []
    rocketlane_client.get_template_id.return_value = template_id
    if create_project_result is not None:
        rocketlane_client.create_project.return_value = create_project_result

    return IntakeAgent(
        voice_client=voice_client,
        rocketlane_client=rocketlane_client,
        llm_client=llm_client,
    )


# ---------- Happy Path ----------

class TestHappyPath:

    def test_parse_valid_email_returns_deal(self):
        parsed = {
            "customer_name": "Acme Corp",
            "customer_contact_email": "jane.doe@acmecorp.com",
            "ae_name": "John Smith",
            "ae_phone": "+1-555-123-4567",
            "salesforce_opportunity_link": "https://novacrm.salesforce.com/opportunity/006ABC123",
        }
        agent = _make_agent(llm_parsed=parsed)
        deal = agent.parse_email(VALID_DEAL_EMAIL)

        assert deal is not None
        assert deal.customer_name == "Acme Corp"
        assert deal.customer_contact_email == "jane.doe@acmecorp.com"
        assert deal.ae_name == "John Smith"

    def test_enterprise_plan_confirmation(self):
        voice_result = VoiceCallResult(
            call_id="call_001",
            status="completed",
            plan_tier=PlanTier.ENTERPRISE,
            confidence=0.95,
            transcript="Yes, Acme Corp is on the Enterprise plan. That's correct.",
            duration_seconds=45.0,
        )
        agent = _make_agent(voice_result=voice_result)
        deal = DealEmail(
            customer_name="Acme Corp",
            customer_contact_email="jane@acme.com",
            ae_name="John",
            ae_phone="+15551234567",
            salesforce_opportunity_link="https://salesforce.com/opp/1",
        )

        result = agent.confirm_plan_tier(deal)

        assert result is not None
        assert result.plan_tier == PlanTier.ENTERPRISE
        assert result.confidence >= 0.8

    def test_growth_plan_confirmation(self):
        voice_result = VoiceCallResult(
            call_id="call_002",
            status="completed",
            plan_tier=PlanTier.GROWTH,
            confidence=0.90,
            transcript="They're on Growth. Correct.",
            duration_seconds=30.0,
        )
        agent = _make_agent(voice_result=voice_result)
        deal = DealEmail(
            customer_name="StartupXYZ",
            customer_contact_email="cto@startupxyz.com",
            ae_name="Alice",
            ae_phone="+15559876543",
            salesforce_opportunity_link="https://salesforce.com/opp/2",
        )

        result = agent.confirm_plan_tier(deal)

        assert result is not None
        assert result.plan_tier == PlanTier.GROWTH

    def test_full_happy_path_enterprise(self):
        parsed = {
            "customer_name": "Acme Corp",
            "customer_contact_email": "jane.doe@acmecorp.com",
            "ae_name": "John Smith",
            "ae_phone": "+1-555-123-4567",
            "salesforce_opportunity_link": "https://novacrm.salesforce.com/opportunity/006ABC",
        }
        voice_result = VoiceCallResult(
            call_id="call_003",
            status="completed",
            plan_tier=PlanTier.ENTERPRISE,
            confidence=0.95,
            transcript="Enterprise, confirmed.",
            duration_seconds=40.0,
        )
        project = OnboardingProject(
            project_id="proj_001",
            project_name="Acme Corp - Onboarding",
            customer_name="Acme Corp",
            plan_tier=PlanTier.ENTERPRISE,
            template_name="Enterprise Onboarding - 30 Day",
            duration_days=30,
            csm_type="dedicated",
        )
        agent = _make_agent(
            llm_parsed=parsed,
            voice_result=voice_result,
            create_project_result=project,
        )

        result = agent.process_deal(VALID_DEAL_EMAIL)

        assert result is not None
        assert result.project_id == "proj_001"
        assert result.plan_tier == PlanTier.ENTERPRISE
        assert result.duration_days == 30
        assert result.csm_type == "dedicated"


# ---------- Validation Tests ----------

class TestValidation:

    def test_rejects_missing_customer_name(self):
        parsed = {
            "customer_name": None,
            "customer_contact_email": "contact@example.com",
            "ae_name": "Someone",
            "ae_phone": "+15559990000",
            "salesforce_opportunity_link": "https://novacrm.salesforce.com/opp/1",
        }
        agent = _make_agent(llm_parsed=parsed)
        deal = agent.parse_email(MALFORMED_EMAIL_MISSING_NAME)

        assert deal is None
        trail = get_audit_trail()
        failed = [a for a in trail if not a.success]
        assert len(failed) > 0

    def test_rejects_missing_contact_email(self):
        parsed = {
            "customer_name": "Beta Inc",
            "customer_contact_email": None,
            "ae_name": "Jane Doe",
            "ae_phone": "+15551112222",
            "salesforce_opportunity_link": "https://novacrm.salesforce.com/opp/2",
        }
        agent = _make_agent(llm_parsed=parsed)
        deal = agent.parse_email(MALFORMED_EMAIL_NO_CONTACT)

        assert deal is None

    def test_rejects_invalid_email_format(self):
        parsed = {
            "customer_name": "Test Co",
            "customer_contact_email": "not-an-email",
            "ae_name": "Bob",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://salesforce.com/opp/1",
        }
        agent = _make_agent(llm_parsed=parsed)
        deal = agent.parse_email(VALID_DEAL_EMAIL)

        assert deal is None

    def test_rejects_short_phone_number(self):
        parsed = {
            "customer_name": "Test Co",
            "customer_contact_email": "a@b.com",
            "ae_name": "Bob",
            "ae_phone": "123",
            "salesforce_opportunity_link": "https://salesforce.com/opp/1",
        }
        agent = _make_agent(llm_parsed=parsed)
        deal = agent.parse_email(VALID_DEAL_EMAIL)

        assert deal is None


# ---------- Template Accuracy ----------

class TestTemplateAccuracy:

    def test_enterprise_gets_30_day_dedicated(self):
        parsed = {
            "customer_name": "BigCorp",
            "customer_contact_email": "pm@bigcorp.com",
            "ae_name": "AE1",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://salesforce.com/opp/e",
        }
        voice_result = VoiceCallResult(
            call_id="c1", status="completed",
            plan_tier=PlanTier.ENTERPRISE, confidence=0.95,
            transcript="Enterprise", duration_seconds=30,
        )
        project = OnboardingProject(
            project_id="p1", project_name="BigCorp - Onboarding",
            customer_name="BigCorp", plan_tier=PlanTier.ENTERPRISE,
            template_name="Enterprise Onboarding - 30 Day",
            duration_days=30, csm_type="dedicated",
        )
        agent = _make_agent(
            llm_parsed=parsed, voice_result=voice_result,
            create_project_result=project,
        )
        result = agent.process_deal(VALID_DEAL_EMAIL)

        assert result is not None
        assert result.duration_days == 30
        assert result.csm_type == "dedicated"
        assert "Enterprise" in result.template_name

    def test_growth_gets_14_day_pooled(self):
        parsed = {
            "customer_name": "SmallCo",
            "customer_contact_email": "hi@smallco.com",
            "ae_name": "AE2",
            "ae_phone": "+15559876543",
            "salesforce_opportunity_link": "https://salesforce.com/opp/g",
        }
        voice_result = VoiceCallResult(
            call_id="c2", status="completed",
            plan_tier=PlanTier.GROWTH, confidence=0.90,
            transcript="Growth plan", duration_seconds=25,
        )
        project = OnboardingProject(
            project_id="p2", project_name="SmallCo - Onboarding",
            customer_name="SmallCo", plan_tier=PlanTier.GROWTH,
            template_name="Growth Onboarding - 14 Day",
            duration_days=14, csm_type="pooled",
        )
        agent = _make_agent(
            llm_parsed=parsed, voice_result=voice_result,
            create_project_result=project,
        )
        result = agent.process_deal(VALID_DEAL_EMAIL)

        assert result is not None
        assert result.duration_days == 14
        assert result.csm_type == "pooled"
        assert "Growth" in result.template_name


# ---------- Edge Cases ----------

class TestEdgeCases:

    def test_rocketlane_api_down(self):
        import requests
        parsed = {
            "customer_name": "DownCorp",
            "customer_contact_email": "a@down.com",
            "ae_name": "AE",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://salesforce.com/opp/d",
        }
        voice_result = VoiceCallResult(
            call_id="c3", status="completed",
            plan_tier=PlanTier.ENTERPRISE, confidence=0.95,
            transcript="Enterprise confirmed", duration_seconds=30,
        )
        agent = _make_agent(llm_parsed=parsed, voice_result=voice_result)
        agent.rocketlane_client.create_project.side_effect = requests.ConnectionError(
            "Rocketlane API is unreachable"
        )

        with pytest.raises(requests.ConnectionError):
            agent.process_deal(VALID_DEAL_EMAIL)

    def test_duplicate_project_blocked(self):
        parsed = {
            "customer_name": "DupeCorp",
            "customer_contact_email": "a@dupe.com",
            "ae_name": "AE",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://salesforce.com/opp/dup",
        }
        voice_result = VoiceCallResult(
            call_id="c4", status="completed",
            plan_tier=PlanTier.GROWTH, confidence=0.90,
            transcript="Growth", duration_seconds=25,
        )
        existing = [{"name": "DupeCorp - Onboarding", "id": "existing_1", "status": "active"}]
        agent = _make_agent(
            llm_parsed=parsed, voice_result=voice_result,
            existing_projects=existing,
        )

        result = agent.process_deal(VALID_DEAL_EMAIL)

        assert result is None
        trail = get_audit_trail()
        blocked = [a for a in trail if "duplicate" in a.action]
        assert len(blocked) > 0

    def test_ae_no_answer(self):
        voice_result = VoiceCallResult(
            call_id="c5", status="no_answer",
        )
        agent = _make_agent(voice_result=voice_result)
        deal = DealEmail(
            customer_name="NoAnswer Inc",
            customer_contact_email="x@noanswer.com",
            ae_name="Ghost",
            ae_phone="+15550000000",
            salesforce_opportunity_link="https://salesforce.com/opp/na",
        )

        result = agent.confirm_plan_tier(deal, max_retries=2)

        assert result is None
        trail = get_audit_trail()
        escalations = [a for a in trail if "escalation" in a.action]
        assert len(escalations) > 0

    def test_ambiguous_response(self):
        voice_result = VoiceCallResult(
            call_id="c6", status="completed",
            plan_tier=None, confidence=0.3,
            transcript="Hmm, I think it might be enterprise, or maybe growth, not sure.",
            duration_seconds=60,
        )
        agent = _make_agent(voice_result=voice_result)
        deal = DealEmail(
            customer_name="Confused Co",
            customer_contact_email="x@confused.com",
            ae_name="Unsure AE",
            ae_phone="+15551111111",
            salesforce_opportunity_link="https://salesforce.com/opp/amb",
        )

        result = agent.confirm_plan_tier(deal, max_retries=2)

        assert result is None

    def test_llm_returns_garbage(self):
        agent = _make_agent()
        mock_choice = MagicMock()
        mock_choice.message.content = "Sorry, I can't help with that."
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        agent.llm.chat.completions.create.return_value = mock_resp

        deal = agent.parse_email(VALID_DEAL_EMAIL)

        assert deal is None
