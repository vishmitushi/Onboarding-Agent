"""Tests for the Communication Agent."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from onboarding_agent.agents.communication_agent import CommunicationAgent
from onboarding_agent.guardrails import get_audit_trail
from onboarding_agent.models import OnboardingProject, PlanTier, SlackChannel


def _make_project(
    plan_tier: PlanTier = PlanTier.ENTERPRISE, name: str = "TestCo"
) -> OnboardingProject:
    config = {
        PlanTier.ENTERPRISE: ("Enterprise Onboarding - 30 Day", 30, "dedicated"),
        PlanTier.GROWTH: ("Growth Onboarding - 14 Day", 14, "pooled"),
    }
    tpl, days, csm = config[plan_tier]
    return OnboardingProject(
        project_id="proj_test",
        project_name=f"{name} - Onboarding",
        customer_name=name,
        plan_tier=plan_tier,
        template_name=tpl,
        duration_days=days,
        csm_type=csm,
    )


class TestCommunicationAgent:

    def test_creates_channel_for_enterprise(self):
        expected_channel = SlackChannel(
            channel_id="C123",
            channel_name="onb-testco-enterprise",
            topic="Onboarding: TestCo | Plan: Enterprise | Duration: 30 days | CSM: Dedicated",
            welcome_message="Welcome enterprise...",
            customer_name="TestCo",
            plan_tier=PlanTier.ENTERPRISE,
        )
        slack_mock = MagicMock()
        slack_mock.create_onboarding_channel.return_value = expected_channel

        agent = CommunicationAgent(slack_client=slack_mock)
        project = _make_project(PlanTier.ENTERPRISE)

        channel = agent.setup_customer_channel(project, "test@testco.com")

        assert channel is not None
        assert channel.plan_tier == PlanTier.ENTERPRISE
        assert channel.channel_name == "onb-testco-enterprise"

    def test_creates_channel_for_growth(self):
        expected_channel = SlackChannel(
            channel_id="C456",
            channel_name="onb-smallco-growth",
            topic="Onboarding: SmallCo | Plan: Growth | Duration: 14 days | CSM: Pooled",
            welcome_message="Welcome growth...",
            customer_name="SmallCo",
            plan_tier=PlanTier.GROWTH,
        )
        slack_mock = MagicMock()
        slack_mock.create_onboarding_channel.return_value = expected_channel

        agent = CommunicationAgent(slack_client=slack_mock)
        project = _make_project(PlanTier.GROWTH, name="SmallCo")

        channel = agent.setup_customer_channel(project, "hi@smallco.com")

        assert channel is not None
        assert channel.plan_tier == PlanTier.GROWTH

    def test_handles_slack_failure_gracefully(self):
        slack_mock = MagicMock()
        slack_mock.create_onboarding_channel.side_effect = RuntimeError("Slack API down")

        agent = CommunicationAgent(slack_client=slack_mock)
        project = _make_project(PlanTier.ENTERPRISE)

        channel = agent.setup_customer_channel(project, "test@co.com")

        assert channel is None
        trail = get_audit_trail()
        failed = [a for a in trail if not a.success]
        assert len(failed) > 0

    def test_audit_trail_logged(self):
        slack_mock = MagicMock()
        slack_mock.create_onboarding_channel.return_value = SlackChannel(
            channel_id="C789", channel_name="onb-co-growth",
            topic="t", welcome_message="w",
            customer_name="Co", plan_tier=PlanTier.GROWTH,
        )

        agent = CommunicationAgent(slack_client=slack_mock)
        project = _make_project(PlanTier.GROWTH, name="Co")
        agent.setup_customer_channel(project, "x@co.com")

        trail = get_audit_trail()
        assert len(trail) >= 2
        assert all(a.agent_name == "CommunicationAgent" for a in trail)
