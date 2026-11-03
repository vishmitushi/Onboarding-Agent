from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class PlanTier(str, enum.Enum):
    ENTERPRISE = "enterprise"
    GROWTH = "growth"


class DealEmail(BaseModel):
    """Parsed fields from an AE deal-notification email."""

    customer_name: str = Field(..., min_length=1)
    customer_contact_email: EmailStr
    ae_name: str = Field(..., min_length=1)
    ae_phone: str = Field(..., min_length=10)
    salesforce_opportunity_link: str = Field(..., min_length=1)
    raw_subject: str = ""
    raw_body: str = ""
    gmail_message_id: str = ""


class VoiceCallResult(BaseModel):
    """Outcome of the voice call to the AE."""

    call_id: str
    status: str  # completed, no_answer, failed
    plan_tier: Optional[PlanTier] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    transcript: str = ""
    duration_seconds: float = 0.0


class OnboardingProject(BaseModel):
    """Rocketlane project created for a customer."""

    project_id: str
    project_name: str
    customer_name: str
    plan_tier: PlanTier
    template_name: str
    duration_days: int
    csm_type: str  # dedicated | pooled
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SlackChannel(BaseModel):
    """Slack channel created for customer onboarding."""

    channel_id: str
    channel_name: str
    topic: str
    welcome_message: str
    customer_name: str
    plan_tier: PlanTier


class AgentAction(BaseModel):
    """Audit log entry for any agent action."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str
    action: str
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    decision_rationale: str = ""
    success: bool = True
    error_message: str = ""


PLAN_TIER_CONFIG = {
    PlanTier.ENTERPRISE: {
        "template_name": "Enterprise Onboarding - 30 Day",
        "duration_days": 30,
        "csm_type": "dedicated",
        "phases": ["Kickoff", "Data Migration", "Configuration", "Go-Live"],
    },
    PlanTier.GROWTH: {
        "template_name": "Growth Onboarding - 14 Day",
        "duration_days": 14,
        "csm_type": "pooled",
        "phases": ["Kickoff", "Data Migration", "Configuration", "Go-Live"],
    },
}
