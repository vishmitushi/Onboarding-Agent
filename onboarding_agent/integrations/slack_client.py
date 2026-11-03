from __future__ import annotations

import re

import requests

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action
from onboarding_agent.models import PlanTier, SlackChannel, PLAN_TIER_CONFIG


class SlackClient:
    """Client for Slack API — channel creation and messaging."""

    def __init__(self) -> None:
        self.token = settings.SLACK_BOT_TOKEN
        self.base_url = "https://slack.com/api"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def create_onboarding_channel(
        self,
        customer_name: str,
        plan_tier: PlanTier,
        customer_email: str,
    ) -> SlackChannel:
        config = PLAN_TIER_CONFIG[plan_tier]
        channel_name = self._sanitize_channel_name(
            f"onb-{customer_name}-{plan_tier.value}"
        )

        topic = (
            f"Onboarding: {customer_name} | "
            f"Plan: {plan_tier.value.title()} | "
            f"Duration: {config['duration_days']} days | "
            f"CSM: {config['csm_type'].title()}"
        )

        welcome_message = self._build_welcome_message(
            customer_name, plan_tier, config
        )

        log_action(
            agent_name="SlackClient",
            action="create_channel",
            inputs={
                "channel_name": channel_name,
                "customer_name": customer_name,
                "plan_tier": plan_tier.value,
            },
            outputs={},
            decision_rationale=f"Creating personalized Slack channel for {plan_tier.value} customer",
        )

        try:
            resp = requests.post(
                f"{self.base_url}/conversations.create",
                json={"name": channel_name, "is_private": False},
                headers=self.headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error')}")

            channel_id = data["channel"]["id"]

            self._set_channel_topic(channel_id, topic)
            self._post_message(channel_id, welcome_message)

            channel = SlackChannel(
                channel_id=channel_id,
                channel_name=channel_name,
                topic=topic,
                welcome_message=welcome_message,
                customer_name=customer_name,
                plan_tier=plan_tier,
            )

            log_action(
                agent_name="SlackClient",
                action="channel_created",
                inputs={"channel_id": channel_id},
                outputs=channel.model_dump(),
                decision_rationale="Slack channel created and welcome message posted",
            )
            return channel

        except (requests.RequestException, RuntimeError) as e:
            log_action(
                agent_name="SlackClient",
                action="create_channel_failed",
                inputs={"channel_name": channel_name},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="Slack channel creation failed",
            )
            raise

    def _set_channel_topic(self, channel_id: str, topic: str) -> None:
        requests.post(
            f"{self.base_url}/conversations.setTopic",
            json={"channel": channel_id, "topic": topic},
            headers=self.headers,
            timeout=15,
        )

    def _post_message(self, channel_id: str, text: str) -> None:
        requests.post(
            f"{self.base_url}/chat.postMessage",
            json={"channel": channel_id, "text": text},
            headers=self.headers,
            timeout=15,
        )

    def _build_welcome_message(
        self, customer_name: str, plan_tier: PlanTier, config: dict
    ) -> str:
        if plan_tier == PlanTier.ENTERPRISE:
            return (
                f":wave: Welcome to your onboarding channel, *{customer_name}*!\n\n"
                f"We're excited to partner with you on your *Enterprise* onboarding journey.\n\n"
                f":calendar: *Timeline*: {config['duration_days']} days\n"
                f":bust_in_silhouette: *Your dedicated CSM* will be introduced shortly\n"
                f":clipboard: *Phases*: {' → '.join(config['phases'])}\n\n"
                f"Your dedicated Customer Success Manager will reach out within 24 hours "
                f"to schedule your kickoff call. In the meantime, feel free to drop any "
                f"questions here!"
            )
        return (
            f":wave: Welcome to your onboarding channel, *{customer_name}*!\n\n"
            f"We're thrilled to get you started on the *Growth* plan.\n\n"
            f":calendar: *Timeline*: {config['duration_days']} days\n"
            f":busts_in_silhouette: *CSM*: Pooled support team\n"
            f":clipboard: *Phases*: {' → '.join(config['phases'])}\n\n"
            f"Our Customer Success team will be in touch shortly to kick things off. "
            f"This channel is your go-to place for all onboarding questions!"
        )

    @staticmethod
    def _sanitize_channel_name(name: str) -> str:
        name = name.lower().strip()
        name = re.sub(r"[^a-z0-9-]", "-", name)
        name = re.sub(r"-+", "-", name)
        return name[:80].strip("-")
