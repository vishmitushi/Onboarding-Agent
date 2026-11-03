from __future__ import annotations

import json

from onboarding_agent.config import settings
from onboarding_agent.guardrails import (
    log_action,
    validate_deal_email,
    validate_voice_call_result,
    validate_project_not_duplicate,
)
from onboarding_agent.integrations import BlandVoiceClient, RocketlaneClient
from onboarding_agent.models import DealEmail, OnboardingProject, PlanTier, VoiceCallResult

EMAIL_PARSE_PROMPT = """\
You are an email parser for a CRM onboarding system. Extract the following fields \
from the deal notification email below. Return ONLY a JSON object with these keys:

- customer_name: The name of the customer/company
- customer_contact_email: The customer's contact email address
- ae_name: The Account Executive's name (the sender)
- ae_phone: The AE's phone number
- salesforce_opportunity_link: The Salesforce opportunity URL

If a field is not found in the email, set its value to null.

Email Subject: {subject}
Email From: {from_addr}
Email Body:
{body}
"""


def _build_llm_client(provider: str | None = None):
    """Build an LLM client based on the configured provider.

    Supports:
      - "groq"      → Groq API (free, Llama 3.3 70B) via OpenAI-compatible SDK
      - "anthropic"  → Anthropic Claude API
    """
    provider = provider or settings.LLM_PROVIDER

    if provider == "groq":
        from openai import OpenAI
        return OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )

    if provider == "anthropic":
        import anthropic
        return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    raise ValueError(f"Unknown LLM provider: {provider}")


def _call_llm(client, prompt: str) -> str:
    """Call the LLM and return the text response, regardless of provider."""
    provider = settings.LLM_PROVIDER

    if provider == "groq":
        response = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0,
        )
        return response.choices[0].message.content

    if provider == "anthropic":
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    raise ValueError(f"Unknown LLM provider: {provider}")


class IntakeAgent:
    """
    Agent 1 — Intake & Routing.

    Monitors Gmail for deal emails, parses them with an LLM, calls the AE
    via voice to confirm the plan tier, then creates the Rocketlane project.
    """

    def __init__(
        self,
        voice_client: BlandVoiceClient | None = None,
        rocketlane_client: RocketlaneClient | None = None,
        llm_client=None,
    ) -> None:
        self.voice_client = voice_client or BlandVoiceClient()
        self.rocketlane_client = rocketlane_client or RocketlaneClient()
        self.llm = llm_client or _build_llm_client()

    def parse_email(self, raw_email: dict) -> DealEmail | None:
        """Use the LLM to extract structured deal data from a raw email."""
        prompt = EMAIL_PARSE_PROMPT.format(
            subject=raw_email.get("subject", ""),
            from_addr=raw_email.get("from", ""),
            body=raw_email.get("body", ""),
        )

        log_action(
            agent_name="IntakeAgent",
            action="parse_email_start",
            inputs={"subject": raw_email.get("subject", "")},
            outputs={},
            decision_rationale="Extracting structured fields from deal notification email",
        )

        try:
            content = _call_llm(self.llm, prompt)

            start = content.find("{")
            end = content.rfind("}") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON object found in LLM response")
            parsed = json.loads(content[start:end])

            parsed["raw_subject"] = raw_email.get("subject", "")
            parsed["raw_body"] = raw_email.get("body", "")
            parsed["gmail_message_id"] = raw_email.get("message_id", "")

            validation = validate_deal_email(parsed)

            if not validation.is_valid:
                log_action(
                    agent_name="IntakeAgent",
                    action="email_validation_failed",
                    inputs=parsed,
                    outputs={"errors": validation.errors},
                    success=False,
                    error_message="; ".join(validation.errors),
                    decision_rationale=(
                        "Email is missing required fields — cannot proceed. "
                        "Will request clarification instead of guessing."
                    ),
                )
                return None

            if validation.warnings:
                log_action(
                    agent_name="IntakeAgent",
                    action="email_validation_warnings",
                    inputs=parsed,
                    outputs={"warnings": validation.warnings},
                    decision_rationale="Non-blocking warnings noted during validation",
                )

            deal = DealEmail(**{k: v for k, v in parsed.items() if v is not None})

            log_action(
                agent_name="IntakeAgent",
                action="email_parsed",
                inputs={"message_id": deal.gmail_message_id},
                outputs=deal.model_dump(),
                decision_rationale="All required fields extracted and validated successfully",
            )
            return deal

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            log_action(
                agent_name="IntakeAgent",
                action="parse_email_failed",
                inputs=raw_email,
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="LLM output could not be parsed into structured data",
            )
            return None

    def confirm_plan_tier(
        self, deal: DealEmail, max_retries: int | None = None
    ) -> VoiceCallResult | None:
        """Call the AE via voice AI to confirm the plan tier."""
        retries = max_retries if max_retries is not None else settings.VOICE_CALL_MAX_RETRIES

        for attempt in range(1, retries + 1):
            log_action(
                agent_name="IntakeAgent",
                action="voice_call_attempt",
                inputs={
                    "attempt": attempt,
                    "ae_name": deal.ae_name,
                    "ae_phone": deal.ae_phone,
                    "customer_name": deal.customer_name,
                },
                outputs={},
                decision_rationale=f"Attempt {attempt}/{retries} to confirm plan tier via voice call",
            )

            result = self.voice_client.call_ae_for_plan_confirmation(
                ae_phone=deal.ae_phone,
                ae_name=deal.ae_name,
                customer_name=deal.customer_name,
            )

            validation = validate_voice_call_result(result)

            if validation.is_valid:
                log_action(
                    agent_name="IntakeAgent",
                    action="plan_tier_confirmed",
                    inputs={"call_id": result.call_id},
                    outputs={
                        "plan_tier": result.plan_tier.value if result.plan_tier else None,
                        "confidence": result.confidence,
                    },
                    decision_rationale=(
                        f"Plan tier '{result.plan_tier.value}' confirmed with "
                        f"{result.confidence:.0%} confidence"
                    ),
                )
                return result

            log_action(
                agent_name="IntakeAgent",
                action="voice_call_validation_failed",
                inputs={"attempt": attempt, "call_id": result.call_id},
                outputs={"errors": validation.errors},
                success=False,
                error_message="; ".join(validation.errors),
                decision_rationale=(
                    f"Attempt {attempt} did not yield unambiguous confirmation. "
                    f"{'Retrying...' if attempt < retries else 'Escalating to human.'}"
                ),
            )

        log_action(
            agent_name="IntakeAgent",
            action="plan_tier_escalation",
            inputs={
                "ae_name": deal.ae_name,
                "customer_name": deal.customer_name,
            },
            outputs={},
            success=False,
            error_message="All voice call attempts failed or were ambiguous",
            decision_rationale=(
                "Could not get unambiguous plan tier confirmation after all retries. "
                "Escalating to CS Manager for manual resolution."
            ),
        )
        return None

    def create_project(
        self, deal: DealEmail, plan_tier: PlanTier
    ) -> OnboardingProject | None:
        """Create the onboarding project in Rocketlane after all validations pass."""
        existing = self.rocketlane_client.check_existing_projects(deal.customer_name)
        dup_check = validate_project_not_duplicate(deal.customer_name, existing)

        if not dup_check.is_valid:
            log_action(
                agent_name="IntakeAgent",
                action="duplicate_project_blocked",
                inputs={"customer_name": deal.customer_name},
                outputs={"errors": dup_check.errors},
                success=False,
                error_message="; ".join(dup_check.errors),
                decision_rationale="Customer already has an active onboarding project",
            )
            return None

        template_id = self.rocketlane_client.get_template_id(plan_tier)

        log_action(
            agent_name="IntakeAgent",
            action="creating_rocketlane_project",
            inputs={
                "customer_name": deal.customer_name,
                "plan_tier": plan_tier.value,
                "template_id": template_id,
            },
            outputs={},
            decision_rationale=(
                f"All validations passed. Creating project with "
                f"{'template ' + str(template_id) if template_id else 'default settings'}"
            ),
        )

        return self.rocketlane_client.create_project(
            customer_name=deal.customer_name,
            plan_tier=plan_tier,
            customer_email=deal.customer_contact_email,
            template_id=template_id,
        )

    def process_deal(self, raw_email: dict) -> OnboardingProject | None:
        """Full pipeline: parse → validate → voice confirm → create project."""
        deal = self.parse_email(raw_email)
        if deal is None:
            return None

        call_result = self.confirm_plan_tier(deal)
        if call_result is None or call_result.plan_tier is None:
            return None

        return self.create_project(deal, call_result.plan_tier)
