"""Node functions for the onboarding LangGraph workflow.

Each node receives the full OnboardingState and returns a dict of
state updates. The graph merges these updates into the state before
passing it to the next node.
"""
from __future__ import annotations

from onboarding_agent.agents.intake_agent import IntakeAgent
from onboarding_agent.agents.communication_agent import CommunicationAgent
from onboarding_agent.config import settings
from onboarding_agent.graph.state import OnboardingState
from onboarding_agent.guardrails import (
    log_action,
    validate_deal_email,
    validate_voice_call_result,
    validate_project_not_duplicate,
)
from onboarding_agent.models import DealEmail, PlanTier, VoiceCallResult


_intake_agent: IntakeAgent | None = None
_comms_agent: CommunicationAgent | None = None


def set_agents(intake: IntakeAgent, comms: CommunicationAgent) -> None:
    """Inject agent instances (allows mocking in tests)."""
    global _intake_agent, _comms_agent
    _intake_agent = intake
    _comms_agent = comms


def _get_intake() -> IntakeAgent:
    global _intake_agent
    if _intake_agent is None:
        _intake_agent = IntakeAgent()
    return _intake_agent


def _get_comms() -> CommunicationAgent:
    global _comms_agent
    if _comms_agent is None:
        _comms_agent = CommunicationAgent()
    return _comms_agent


# ── Node: Parse Email ───────────────────────────────────────────────

def parse_email(state: OnboardingState) -> dict:
    """Use the LLM to extract structured fields from the raw email."""
    raw_email = state["raw_email"]
    agent = _get_intake()

    deal = agent.parse_email(raw_email)

    if deal is None:
        return {
            "deal_data": None,
            "status": "failed",
            "error": "Email parsing failed — could not extract required fields",
        }

    return {
        "deal_data": deal.model_dump(mode="json"),
        "status": "processing",
        "error": "",
    }


# ── Node: Validate Email ───────────────────────────────────────────

def validate_email(state: OnboardingState) -> dict:
    """Validate that all parsed fields are present and well-formed."""
    deal_data = state.get("deal_data")

    if deal_data is None:
        return {
            "validation_ok": False,
            "validation_errors": ["No deal data to validate"],
            "status": "failed",
        }

    result = validate_deal_email(deal_data)

    log_action(
        agent_name="WorkflowNode:validate_email",
        action="field_validation",
        inputs={"customer_name": deal_data.get("customer_name")},
        outputs={"valid": result.is_valid, "errors": result.errors},
        success=result.is_valid,
        error_message="; ".join(result.errors) if not result.is_valid else "",
        decision_rationale=(
            "All fields valid — proceeding to voice call"
            if result.is_valid
            else "Missing or invalid fields — cannot proceed without clarification"
        ),
    )

    return {
        "validation_ok": result.is_valid,
        "validation_errors": result.errors,
        "status": "processing" if result.is_valid else "failed",
        "error": "; ".join(result.errors) if not result.is_valid else "",
    }


# ── Node: Call AE (Voice) ──────────────────────────────────────────

def call_ae(state: OnboardingState) -> dict:
    """Place a voice call to the AE to confirm the plan tier."""
    deal_data = state["deal_data"]
    deal = DealEmail(**deal_data)
    attempts = state.get("voice_attempts", 0) + 1
    agent = _get_intake()

    log_action(
        agent_name="WorkflowNode:call_ae",
        action="voice_call_attempt",
        inputs={"attempt": attempts, "ae_name": deal.ae_name},
        outputs={},
        decision_rationale=f"Voice call attempt {attempts} to confirm plan tier",
    )

    result = agent.voice_client.call_ae_for_plan_confirmation(
        ae_phone=deal.ae_phone,
        ae_name=deal.ae_name,
        customer_name=deal.customer_name,
    )

    return {
        "voice_result": result.model_dump(mode="json"),
        "voice_attempts": attempts,
    }


# ── Node: Validate Voice ───────────────────────────────────────────

def validate_voice(state: OnboardingState) -> dict:
    """Check if the voice call gave a clear, unambiguous plan tier."""
    voice_data = state.get("voice_result")

    if voice_data is None:
        return {
            "voice_confirmed": False,
            "plan_tier": None,
            "status": "failed",
            "error": "No voice result to validate",
        }

    result = VoiceCallResult(**voice_data)
    validation = validate_voice_call_result(result)

    log_action(
        agent_name="WorkflowNode:validate_voice",
        action="voice_validation",
        inputs={
            "status": result.status,
            "plan_tier": result.plan_tier.value if result.plan_tier else None,
            "confidence": result.confidence,
        },
        outputs={"valid": validation.is_valid, "errors": validation.errors},
        success=validation.is_valid,
        error_message="; ".join(validation.errors) if not validation.is_valid else "",
        decision_rationale=(
            f"Plan tier '{result.plan_tier.value}' confirmed at {result.confidence:.0%}"
            if validation.is_valid
            else f"Voice validation failed — {'; '.join(validation.errors)}"
        ),
    )

    return {
        "voice_confirmed": validation.is_valid,
        "plan_tier": result.plan_tier.value if validation.is_valid and result.plan_tier else None,
    }


# ── Node: Check Duplicate ──────────────────────────────────────────

def check_duplicate(state: OnboardingState) -> dict:
    """Query Rocketlane for existing active projects for this customer."""
    deal_data = state["deal_data"]
    customer_name = deal_data["customer_name"]
    agent = _get_intake()

    existing = agent.rocketlane_client.check_existing_projects(customer_name)
    dup_result = validate_project_not_duplicate(customer_name, existing)

    log_action(
        agent_name="WorkflowNode:check_duplicate",
        action="duplicate_check",
        inputs={"customer_name": customer_name},
        outputs={"duplicate_found": not dup_result.is_valid, "existing_count": len(existing)},
        success=dup_result.is_valid,
        error_message="; ".join(dup_result.errors) if not dup_result.is_valid else "",
        decision_rationale=(
            "No active duplicates — safe to create project"
            if dup_result.is_valid
            else f"Duplicate project found — blocking creation"
        ),
    )

    return {
        "duplicate_found": not dup_result.is_valid,
        "status": "processing" if dup_result.is_valid else "failed",
        "error": "; ".join(dup_result.errors) if not dup_result.is_valid else "",
    }


# ── Node: Create Project ───────────────────────────────────────────

def create_project(state: OnboardingState) -> dict:
    """Create the onboarding project in Rocketlane."""
    deal_data = state["deal_data"]
    plan_tier = PlanTier(state["plan_tier"])
    agent = _get_intake()

    try:
        project = agent.create_project(
            DealEmail(**deal_data), plan_tier
        )

        if project is None:
            return {
                "project_data": None,
                "status": "failed",
                "error": "Project creation returned None",
            }

        return {
            "project_data": project.model_dump(mode="json"),
            "status": "processing",
        }

    except Exception as e:
        log_action(
            agent_name="WorkflowNode:create_project",
            action="project_creation_error",
            inputs={"customer": deal_data["customer_name"]},
            outputs={},
            success=False,
            error_message=str(e),
            decision_rationale="Rocketlane API error during project creation",
        )
        return {
            "project_data": None,
            "status": "failed",
            "error": f"Rocketlane API error: {e}",
        }


# ── Node: Create Slack Channel ─────────────────────────────────────

def create_slack_channel(state: OnboardingState) -> dict:
    """Create a personalized Slack channel for the customer."""
    from onboarding_agent.models import OnboardingProject

    project_data = state.get("project_data")
    if project_data is None:
        return {
            "slack_channel_data": None,
            "status": "failed",
            "error": state.get("error", "No project to create channel for"),
        }

    deal_data = state["deal_data"]
    project = OnboardingProject(**project_data)
    agent = _get_comms()

    channel = agent.setup_customer_channel(
        project=project,
        customer_email=deal_data["customer_contact_email"],
    )

    if channel is None:
        return {
            "slack_channel_data": None,
            "status": "completed",
            "error": "Slack channel creation failed — project was created successfully",
        }

    return {
        "slack_channel_data": channel.model_dump(mode="json"),
        "status": "completed",
        "error": "",
    }


# ── Node: Escalate ─────────────────────────────────────────────────

def escalate(state: OnboardingState) -> dict:
    """Escalate to human when the workflow cannot proceed."""
    error = state.get("error", "Unknown error")
    customer = (state.get("deal_data") or {}).get("customer_name", "Unknown")

    log_action(
        agent_name="WorkflowNode:escalate",
        action="human_escalation",
        inputs={"customer_name": customer, "error": error},
        outputs={"escalation_target": settings.ESCALATION_EMAIL},
        success=False,
        error_message=error,
        decision_rationale=(
            f"Workflow cannot proceed automatically. "
            f"Escalating to {settings.ESCALATION_EMAIL} for manual resolution."
        ),
    )

    return {"status": "escalated"}


# ── Routing functions (conditional edges) ───────────────────────────

def route_after_parse(state: OnboardingState) -> str:
    """After parsing: if deal_data exists, validate; otherwise escalate."""
    if state.get("deal_data") is not None:
        return "validate_email"
    return "escalate"


def route_after_validation(state: OnboardingState) -> str:
    """After validation: if valid, call AE; otherwise escalate."""
    if state.get("validation_ok"):
        return "call_ae"
    return "escalate"


def route_after_voice_validation(state: OnboardingState) -> str:
    """After voice validation: confirmed → duplicate check, retry or escalate."""
    if state.get("voice_confirmed"):
        return "check_duplicate"

    attempts = state.get("voice_attempts", 0)
    max_retries = settings.VOICE_CALL_MAX_RETRIES

    if attempts < max_retries:
        return "call_ae"

    return "escalate"


def route_after_duplicate_check(state: OnboardingState) -> str:
    """After duplicate check: no duplicate → create project; otherwise escalate."""
    if not state.get("duplicate_found"):
        return "create_project"
    return "escalate"


def route_after_create_project(state: OnboardingState) -> str:
    """After project creation: success → Slack channel; failure → escalate."""
    if state.get("project_data") is not None:
        return "create_slack_channel"
    return "escalate"
