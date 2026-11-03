from __future__ import annotations

import re
from dataclasses import dataclass, field

from onboarding_agent.models import DealEmail, VoiceCallResult, PlanTier


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_deal_email(data: dict) -> ValidationResult:
    """Validate that all required fields are present and well-formed."""
    errors: list[str] = []
    warnings: list[str] = []

    required_fields = {
        "customer_name": "Customer name",
        "customer_contact_email": "Customer contact email",
        "ae_name": "Account Executive name",
        "ae_phone": "Account Executive phone number",
        "salesforce_opportunity_link": "Salesforce opportunity link",
    }

    for fld, label in required_fields.items():
        val = data.get(fld)
        if not val or (isinstance(val, str) and not val.strip()):
            errors.append(f"Missing required field: {label}")

    email = data.get("customer_contact_email", "")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        errors.append(f"Invalid email format: {email}")

    phone = data.get("ae_phone", "")
    if phone:
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 10:
            errors.append(f"Phone number too short (need at least 10 digits): {phone}")

    sf_link = data.get("salesforce_opportunity_link", "")
    if sf_link and "salesforce" not in sf_link.lower() and "sf.com" not in sf_link.lower():
        warnings.append(f"Salesforce link may be invalid: {sf_link}")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def validate_voice_call_result(result: VoiceCallResult) -> ValidationResult:
    """Ensure the voice call gave a clear, unambiguous plan tier."""
    errors: list[str] = []
    warnings: list[str] = []

    if result.status != "completed":
        errors.append(f"Voice call did not complete successfully. Status: {result.status}")
        return ValidationResult(is_valid=False, errors=errors)

    if result.plan_tier is None:
        errors.append("AE did not provide a clear plan tier during the call.")

    if result.confidence < 0.8:
        errors.append(
            f"Plan tier confidence too low ({result.confidence:.0%}). "
            "Cannot proceed without unambiguous confirmation."
        )

    if result.duration_seconds < 5:
        warnings.append("Call was very short — verify the AE actually engaged.")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def validate_project_not_duplicate(
    customer_name: str, existing_projects: list[dict]
) -> ValidationResult:
    """Check that the customer doesn't already have an active onboarding project."""
    errors: list[str] = []

    for proj in existing_projects:
        proj_name = proj.get("name", "").lower()
        proj_status = proj.get("status", "").lower()
        if customer_name.lower() in proj_name and proj_status in (
            "active",
            "in_progress",
            "in progress",
            "not started",
        ):
            errors.append(
                f"Customer '{customer_name}' already has an active onboarding project: "
                f"{proj.get('name')} (ID: {proj.get('id')})"
            )

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)
