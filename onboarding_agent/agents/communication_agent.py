from __future__ import annotations

from onboarding_agent.guardrails import log_action
from onboarding_agent.integrations import SlackClient
from onboarding_agent.models import OnboardingProject, PlanTier, SlackChannel


class CommunicationAgent:
    """
    Agent 2 — Communication.

    Creates a personalized Slack channel for the customer's onboarding
    and posts a welcome message tailored to the plan tier.
    """

    def __init__(self, slack_client: SlackClient | None = None) -> None:
        self.slack_client = slack_client or SlackClient()

    def setup_customer_channel(
        self,
        project: OnboardingProject,
        customer_email: str,
    ) -> SlackChannel | None:
        log_action(
            agent_name="CommunicationAgent",
            action="setup_channel_start",
            inputs={
                "customer_name": project.customer_name,
                "plan_tier": project.plan_tier.value,
                "project_id": project.project_id,
            },
            outputs={},
            decision_rationale=(
                f"Setting up Slack channel for {project.plan_tier.value} "
                f"customer '{project.customer_name}'"
            ),
        )

        try:
            channel = self.slack_client.create_onboarding_channel(
                customer_name=project.customer_name,
                plan_tier=project.plan_tier,
                customer_email=customer_email,
            )

            log_action(
                agent_name="CommunicationAgent",
                action="channel_setup_complete",
                inputs={"project_id": project.project_id},
                outputs={
                    "channel_id": channel.channel_id,
                    "channel_name": channel.channel_name,
                },
                decision_rationale=(
                    f"Slack channel '{channel.channel_name}' created with "
                    f"personalized welcome message for {project.plan_tier.value} tier"
                ),
            )
            return channel

        except Exception as e:
            log_action(
                agent_name="CommunicationAgent",
                action="channel_setup_failed",
                inputs={"customer_name": project.customer_name},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale=(
                    "Slack channel creation failed. Project was created but "
                    "communication channel needs manual setup."
                ),
            )
            return None
