"""
Customer Onboarding Automation — LangGraph Orchestrator

Polls Gmail for deal notifications, then runs each email through
the LangGraph onboarding workflow:

    parse_email → validate → voice call → validate → duplicate check
        → create Rocketlane project → create Slack channel

Each guardrail is a conditional edge in the graph. Failed validations
route to the escalation node instead of proceeding.
"""
from __future__ import annotations

import sys
import time

from onboarding_agent.agents import IntakeAgent, CommunicationAgent
from onboarding_agent.config import settings
from onboarding_agent.graph import onboarding_workflow, set_agents
from onboarding_agent.graph.state import OnboardingState
from onboarding_agent.guardrails import log_action, export_audit_trail
from onboarding_agent.integrations import GmailClient


def run_workflow(raw_email: dict) -> OnboardingState:
    """Run a single email through the LangGraph onboarding workflow."""
    initial_state: OnboardingState = {
        "raw_email": raw_email,
        "deal_data": None,
        "validation_ok": False,
        "validation_errors": [],
        "voice_result": None,
        "voice_attempts": 0,
        "plan_tier": None,
        "voice_confirmed": False,
        "duplicate_found": False,
        "project_data": None,
        "slack_channel_data": None,
        "status": "processing",
        "error": "",
    }

    final_state = onboarding_workflow.invoke(initial_state)
    return final_state


def run_once(gmail: GmailClient) -> int:
    """Process all unread deal emails. Returns count of successfully onboarded."""
    emails = gmail.fetch_unread_deal_emails()
    if not emails:
        log_action(
            agent_name="Orchestrator",
            action="poll_inbox",
            inputs={},
            outputs={"new_emails": 0},
            decision_rationale="No new deal emails found in this polling cycle",
        )
        return 0

    log_action(
        agent_name="Orchestrator",
        action="new_deals_found",
        inputs={},
        outputs={"count": len(emails)},
        decision_rationale=f"Found {len(emails)} new deal email(s) to process",
    )

    success_count = 0

    for raw_email in emails:
        log_action(
            agent_name="Orchestrator",
            action="workflow_start",
            inputs={"subject": raw_email.get("subject", "")},
            outputs={},
            decision_rationale="Invoking LangGraph onboarding workflow",
        )

        final_state = run_workflow(raw_email)

        status = final_state.get("status", "unknown")
        customer = (final_state.get("deal_data") or {}).get("customer_name", "Unknown")

        log_action(
            agent_name="Orchestrator",
            action="workflow_complete",
            inputs={"customer_name": customer},
            outputs={
                "status": status,
                "project_id": (final_state.get("project_data") or {}).get("project_id"),
                "slack_channel": (final_state.get("slack_channel_data") or {}).get("channel_name"),
            },
            decision_rationale=f"Workflow finished with status: {status}",
            success=status == "completed",
            error_message=final_state.get("error", ""),
        )

        if status == "completed":
            gmail.mark_as_read(raw_email["message_id"])
            success_count += 1

    return success_count


def run_polling_loop() -> None:
    """Continuously poll Gmail and process deals through LangGraph."""
    gmail = GmailClient()
    intake = IntakeAgent()
    comms = CommunicationAgent()
    set_agents(intake, comms)

    print("Onboarding Agent (LangGraph) started. Polling for new deal emails...")
    print(f"Poll interval: {settings.GMAIL_POLL_INTERVAL_SECONDS}s")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            processed = run_once(gmail)
            if processed:
                print(f"Processed {processed} deal(s) successfully.")
            time.sleep(settings.GMAIL_POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        trail_path = export_audit_trail()
        print(f"Audit trail exported to {trail_path}")


if __name__ == "__main__":
    if "--once" in sys.argv:
        gmail = GmailClient()
        set_agents(IntakeAgent(), CommunicationAgent())
        count = run_once(gmail)
        trail_path = export_audit_trail()
        print(f"Processed {count} deal(s). Audit trail: {trail_path}")
    else:
        run_polling_loop()
