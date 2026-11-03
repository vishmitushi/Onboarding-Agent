from __future__ import annotations

import pytest

from onboarding_agent.guardrails import clear_audit_trail


VALID_DEAL_EMAIL = {
    "message_id": "msg_001",
    "thread_id": "thread_001",
    "from": "john.smith@novacrm.com",
    "subject": "New Deal Closed - Acme Corp",
    "date": "2026-11-01",
    "body": (
        "Hi CS Team,\n\n"
        "Great news! We just closed a new deal with Acme Corp.\n\n"
        "Customer Name: Acme Corp\n"
        "Customer Contact: jane.doe@acmecorp.com\n"
        "AE: John Smith\n"
        "AE Phone: +1-555-123-4567\n"
        "Salesforce Opportunity: https://novacrm.salesforce.com/opportunity/006ABC123\n\n"
        "Please kick off the onboarding process.\n\n"
        "Thanks,\nJohn"
    ),
}

MALFORMED_EMAIL_MISSING_NAME = {
    "message_id": "msg_002",
    "thread_id": "thread_002",
    "from": "someone@novacrm.com",
    "subject": "New Deal",
    "date": "2026-11-01",
    "body": (
        "Hi,\n\n"
        "New deal closed.\n"
        "Customer Contact: contact@example.com\n"
        "AE Phone: +1-555-999-0000\n"
        "SF Link: https://novacrm.salesforce.com/opportunity/006XYZ\n"
    ),
}

MALFORMED_EMAIL_NO_CONTACT = {
    "message_id": "msg_003",
    "thread_id": "thread_003",
    "from": "ae@novacrm.com",
    "subject": "New Deal - Beta Inc",
    "date": "2026-11-01",
    "body": (
        "Customer Name: Beta Inc\n"
        "AE: Jane Doe\n"
        "AE Phone: +1-555-111-2222\n"
        "Salesforce: https://novacrm.salesforce.com/opportunity/006DEF\n"
    ),
}


@pytest.fixture(autouse=True)
def _clean_audit_trail():
    clear_audit_trail()
    yield
    clear_audit_trail()
