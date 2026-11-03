"""Workflow state definition for the onboarding LangGraph."""
from __future__ import annotations

from typing import Annotated, Any

from typing_extensions import TypedDict


def _replace(existing: Any, new: Any) -> Any:
    """Reducer: always replace with the latest value."""
    return new


class OnboardingState(TypedDict, total=False):
    """State that flows through the onboarding workflow graph.

    Each node reads what it needs and returns updates to the keys it owns.
    """

    # ── Input ──
    raw_email: Annotated[dict, _replace]

    # ── After email parsing (Node: parse_email) ──
    deal_data: Annotated[dict | None, _replace]

    # ── After validation (Node: validate_email) ──
    validation_ok: Annotated[bool, _replace]
    validation_errors: Annotated[list[str], _replace]

    # ── After voice call (Node: call_ae) ──
    voice_result: Annotated[dict | None, _replace]
    voice_attempts: Annotated[int, _replace]

    # ── After voice validation (Node: validate_voice) ──
    plan_tier: Annotated[str | None, _replace]
    voice_confirmed: Annotated[bool, _replace]

    # ── After duplicate check (Node: check_duplicate) ──
    duplicate_found: Annotated[bool, _replace]

    # ── After project creation (Node: create_project) ──
    project_data: Annotated[dict | None, _replace]

    # ── After Slack channel (Node: create_slack_channel) ──
    slack_channel_data: Annotated[dict | None, _replace]

    # ── Workflow status ──
    status: Annotated[str, _replace]  # processing | completed | failed | escalated
    error: Annotated[str, _replace]
