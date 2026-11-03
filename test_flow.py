"""
Step-by-step test runner for the onboarding agent flow.

Run individual steps to test each integration:
    python test_flow.py --step 1     # Test Rocketlane API connection
    python test_flow.py --step 2     # Test Rocketlane project creation
    python test_flow.py --step 3     # Test Bland.ai voice call
    python test_flow.py --step 4     # Test email parsing (LLM)
    python test_flow.py --step 5     # Test Slack channel creation
    python test_flow.py --step 6     # Test full happy path (all integrations)
    python test_flow.py --step all   # Run all steps sequentially
"""
from __future__ import annotations

import argparse
import json
import sys
import os

from dotenv import load_dotenv

load_dotenv()

# ── Colored output helpers ──────────────────────────────────────────

def green(text: str) -> str: return f"\033[92m{text}\033[0m"
def red(text: str) -> str: return f"\033[91m{text}\033[0m"
def yellow(text: str) -> str: return f"\033[93m{text}\033[0m"
def cyan(text: str) -> str: return f"\033[96m{text}\033[0m"
def bold(text: str) -> str: return f"\033[1m{text}\033[0m"

def header(step: int, title: str):
    print(f"\n{'='*60}")
    print(bold(f"  STEP {step}: {title}"))
    print(f"{'='*60}\n")

def success(msg: str): print(green(f"  [PASS] {msg}"))
def fail(msg: str): print(red(f"  [FAIL] {msg}"))
def info(msg: str): print(cyan(f"  [INFO] {msg}"))
def warn(msg: str): print(yellow(f"  [WARN] {msg}"))


# ── Step 1: Test Rocketlane API Connection ──────────────────────────

def step1_rocketlane_connection():
    header(1, "Rocketlane API Connection")

    api_key = os.getenv("ROCKETLANE_API_KEY", "")
    if not api_key:
        fail("ROCKETLANE_API_KEY not set in .env")
        print(f"\n  How to get your API key:")
        print(f"  1. Go to {cyan('https://your-domain.rocketlane.com')}")
        print(f"  2. Click your profile icon (bottom-left) → {bold('Settings')}")
        print(f"  3. Go to {bold('API')} tab → {bold('Create API key')}")
        print(f"  4. Copy the key and add to your .env file:")
        print(f"     ROCKETLANE_API_KEY=your_key_here\n")
        return False

    info(f"API Key found: {api_key[:8]}...{api_key[-4:]}")

    import requests
    base_url = os.getenv("ROCKETLANE_API_URL", "https://api.rocketlane.com/api/1.0")
    headers = {
        "api-key": api_key,
        "Accept": "application/json",
    }

    try:
        verify_ssl = os.getenv("ROCKETLANE_VERIFY_SSL", "true").lower() != "false"
        info("Testing GET /projects ...")
        resp = requests.get(f"{base_url}/projects", headers=headers, timeout=15, verify=verify_ssl)
        info(f"Status: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            projects = data if isinstance(data, list) else data.get("data", data.get("projects", []))
            success(f"Connected! Found {len(projects)} existing project(s)")
            for p in projects[:5]:
                name = p.get("projectName", p.get("name", "Unknown"))
                info(f"  - {name}")
            return True
        elif resp.status_code == 401:
            fail("Authentication failed — API key is invalid")
            return False
        else:
            fail(f"Unexpected response: {resp.status_code}")
            print(f"  Response: {resp.text[:300]}")
            return False

    except requests.ConnectionError as e:
        fail(f"Could not connect to Rocketlane API")
        info(f"Error details: {e}")
        info("Possible causes:")
        info("  - Corporate proxy/firewall blocking the request")
        info("  - VPN required to reach external APIs")
        info("  - DNS resolution failure")
        info("Try running: curl -I https://api.rocketlane.com/api/1.0/projects")
        return False
    except Exception as e:
        fail(f"Unexpected error: {type(e).__name__}: {e}")
        return False


# ── Step 2: Test Rocketlane Project Creation ────────────────────────

def step2_rocketlane_create_project():
    header(2, "Rocketlane Project Creation")

    from onboarding_agent.integrations.rocketlane_client import RocketlaneClient
    from onboarding_agent.models import PlanTier

    client = RocketlaneClient()

    info("Listing existing templates...")
    try:
        templates = client.list_templates()
        if templates:
            success(f"Found {len(templates)} template(s):")
            for t in templates:
                info(f"  - {t.get('name', 'Unknown')} (ID: {t.get('id', 'N/A')})")
        else:
            warn("No templates found. Project will be created without a template.")
    except Exception as e:
        warn(f"Could not list templates: {e}")

    info("Creating test project: 'Test Corp - Onboarding' (Enterprise)...")
    try:
        project = client.create_project(
            customer_name="Test Corp",
            plan_tier=PlanTier.ENTERPRISE,
            customer_email="test@testcorp.com",
        )
        success(f"Project created! ID: {project.project_id}")
        info(f"  Name: {project.project_name}")
        info(f"  Plan: {project.plan_tier.value}")
        info(f"  Duration: {project.duration_days} days")
        info(f"  CSM: {project.csm_type}")
        return True
    except Exception as e:
        fail(f"Project creation failed: {e}")
        return False


# ── Step 3: Test Bland.ai Voice Call ────────────────────────────────

def step3_voice_call():
    header(3, "Voice AI Call")

    provider = os.getenv("VOICE_PROVIDER", "vapi")

    if provider == "vapi":
        api_key = os.getenv("VAPI_API_KEY", "")
        phone_id = os.getenv("VAPI_PHONE_ID", "")
        if not api_key:
            fail("VAPI_API_KEY not set in .env")
            print(f"\n  How to set up Vapi.ai:")
            print(f"  1. Sign up at {cyan('https://vapi.ai')} (free credits included)")
            print(f"  2. Go to {bold('Phone Numbers')} → buy a number")
            print(f"  3. Copy the Phone Number ID")
            print(f"  4. Go to {bold('Dashboard')} → copy your API key")
            print(f"  5. Add to .env:")
            print(f"     VOICE_PROVIDER=vapi")
            print(f"     VAPI_API_KEY=your_key")
            print(f"     VAPI_PHONE_ID=your_phone_id\n")
            return False
        if not phone_id:
            fail("VAPI_PHONE_ID not set in .env (buy a number in Vapi dashboard)")
            return False
        info(f"Vapi.ai API key found: {api_key[:8]}...")
        info(f"Phone Number ID: {phone_id[:8]}...")
    else:
        api_key = os.getenv("BLAND_API_KEY", "")
        if not api_key:
            fail("BLAND_API_KEY not set in .env")
            return False
        info(f"Bland.ai API key found: {api_key[:8]}...")

    phone = input(f"  Enter your phone number to receive the test call (e.g. +919876543210): ").strip()
    if not phone:
        warn("No phone number provided, skipping live call test")
        return True

    from onboarding_agent.integrations.voice_client import BlandVoiceClient
    client = BlandVoiceClient()

    info(f"Placing real AI voice call to {phone} via {provider}...")
    info("Answer your phone — the AI will ask you to confirm the plan tier.")
    info("Say 'Enterprise' or 'Growth' when asked.\n")

    result = client.call_ae_for_plan_confirmation(
        ae_phone=phone,
        ae_name="Test AE",
        customer_name="Acme Corp",
    )

    info(f"Call ID: {result.call_id}")
    info(f"Status: {result.status}")
    info(f"Plan Tier: {result.plan_tier}")
    info(f"Confidence: {result.confidence:.0%}")
    info(f"Duration: {result.duration_seconds}s")

    if result.transcript:
        info(f"Transcript: {result.transcript[:300]}")

    if result.status == "completed" and result.plan_tier:
        if result.confidence >= 0.8:
            success(f"Voice call confirmed: {result.plan_tier.value} ({result.confidence:.0%} ≥ 80% gate)")
        else:
            warn(f"Plan tier detected but confidence too low ({result.confidence:.0%} < 80%) — would trigger retry")
        return True
    elif result.status == "failed":
        fail(f"Call failed: {result.transcript[:200] if result.transcript else 'No details'}")
        return False
    else:
        warn(f"Call status: {result.status}")
        return True


# ── Step 4: Test Email Parsing (LLM) ───────────────────────────────

def step4_email_parsing():
    header(4, "Email Parsing with LLM")

    provider = os.getenv("LLM_PROVIDER", "groq")
    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            fail("GROQ_API_KEY not set in .env")
            return False
        info(f"Groq API key found — using model: {os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')}")
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            fail("ANTHROPIC_API_KEY not set in .env")
            return False
        info("Anthropic API key found")

    from onboarding_agent.agents.intake_agent import IntakeAgent

    sample_email = {
        "message_id": "test_001",
        "from": "john.smith@novacrm.com",
        "subject": "New Deal Closed - Acme Corp",
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

    agent = IntakeAgent()

    info("Sending sample email to LLM for parsing...")
    deal = agent.parse_email(sample_email)

    if deal is None:
        fail("Email parsing returned None")
        return False

    success("Email parsed successfully!")
    info(f"  Customer: {deal.customer_name}")
    info(f"  Email: {deal.customer_contact_email}")
    info(f"  AE: {deal.ae_name}")
    info(f"  Phone: {deal.ae_phone}")
    info(f"  SF Link: {deal.salesforce_opportunity_link}")

    # Test with malformed email
    info("\nTesting with malformed email (missing fields)...")
    bad_email = {
        "message_id": "test_002",
        "from": "someone@novacrm.com",
        "subject": "New Deal",
        "body": "Hey, we closed a deal. Details coming later.",
    }

    bad_result = agent.parse_email(bad_email)
    if bad_result is None:
        success("Malformed email correctly rejected!")
    else:
        warn("Malformed email was not rejected — check validation logic")

    return True


# ── Step 5: Test Slack (Mock) ───────────────────────────────────────

def step5_slack():
    header(5, "Slack Channel Creation")

    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        warn("SLACK_BOT_TOKEN not set — running with mock Slack client")
        info("For the demo, mock Slack is acceptable (as stated in the assessment)")
        info("In a real setup, create a Slack App at https://api.slack.com/apps")

        from unittest.mock import MagicMock
        from onboarding_agent.agents.communication_agent import CommunicationAgent
        from onboarding_agent.models import OnboardingProject, PlanTier, SlackChannel

        mock_slack = MagicMock()
        mock_slack.create_onboarding_channel.return_value = SlackChannel(
            channel_id="C_MOCK_123",
            channel_name="onb-acme-corp-enterprise",
            topic="Onboarding: Acme Corp | Plan: Enterprise | Duration: 30 days | CSM: Dedicated",
            welcome_message="Welcome to your onboarding channel, Acme Corp!",
            customer_name="Acme Corp",
            plan_tier=PlanTier.ENTERPRISE,
        )

        agent = CommunicationAgent(slack_client=mock_slack)
        project = OnboardingProject(
            project_id="proj_mock",
            project_name="Acme Corp - Onboarding",
            customer_name="Acme Corp",
            plan_tier=PlanTier.ENTERPRISE,
            template_name="Enterprise Onboarding - 30 Day",
            duration_days=30,
            csm_type="dedicated",
        )

        channel = agent.setup_customer_channel(project, "jane@acme.com")
        if channel:
            success(f"Mock channel created: #{channel.channel_name}")
            info(f"  Topic: {channel.topic}")
            return True
        fail("Mock channel creation failed")
        return False

    info("Slack token found — testing live channel creation...")
    from onboarding_agent.agents.communication_agent import CommunicationAgent
    from onboarding_agent.models import OnboardingProject, PlanTier

    agent = CommunicationAgent()
    project = OnboardingProject(
        project_id="proj_test",
        project_name="Test Corp - Onboarding",
        customer_name="Test Corp",
        plan_tier=PlanTier.ENTERPRISE,
        template_name="Enterprise Onboarding - 30 Day",
        duration_days=30,
        csm_type="dedicated",
    )

    try:
        channel = agent.setup_customer_channel(project, "test@testcorp.com")
        if channel:
            success(f"Channel created: #{channel.channel_name}")
            return True
    except Exception as e:
        fail(f"Slack error: {e}")

    return False


# ── Step 6: Full Happy Path ─────────────────────────────────────────

def step6_full_flow():
    header(6, "Full Happy Path (Simulated)")

    from unittest.mock import MagicMock
    from onboarding_agent.agents.intake_agent import IntakeAgent
    from onboarding_agent.agents.communication_agent import CommunicationAgent
    from onboarding_agent.models import (
        OnboardingProject, PlanTier, VoiceCallResult, SlackChannel,
    )
    from onboarding_agent.guardrails import get_audit_trail, clear_audit_trail, export_audit_trail

    clear_audit_trail()

    # Mock voice client (so we don't make a real call)
    voice_mock = MagicMock()
    voice_mock.call_ae_for_plan_confirmation.return_value = VoiceCallResult(
        call_id="test_call_001",
        status="completed",
        plan_tier=PlanTier.ENTERPRISE,
        confidence=0.95,
        transcript="Yes, Acme Corp is on the Enterprise plan. That's correct.",
        duration_seconds=42.0,
    )

    # Mock Rocketlane (or use real if API key is set)
    rocketlane_mock = MagicMock()
    rocketlane_mock.check_existing_projects.return_value = []
    rocketlane_mock.get_template_id.return_value = "tpl_001"
    rocketlane_mock.create_project.return_value = OnboardingProject(
        project_id="proj_001",
        project_name="Acme Corp - Onboarding",
        customer_name="Acme Corp",
        plan_tier=PlanTier.ENTERPRISE,
        template_name="Enterprise Onboarding - 30 Day",
        duration_days=30,
        csm_type="dedicated",
    )

    # Mock Slack
    slack_mock = MagicMock()
    slack_mock.create_onboarding_channel.return_value = SlackChannel(
        channel_id="C_MOCK",
        channel_name="onb-acme-corp-enterprise",
        topic="Onboarding: Acme Corp | Plan: Enterprise | Duration: 30 days",
        welcome_message="Welcome!",
        customer_name="Acme Corp",
        plan_tier=PlanTier.ENTERPRISE,
    )

    intake = IntakeAgent(
        voice_client=voice_mock,
        rocketlane_client=rocketlane_mock,
    )
    comms = CommunicationAgent(slack_client=slack_mock)

    sample_email = {
        "message_id": "flow_test_001",
        "from": "john.smith@novacrm.com",
        "subject": "New Deal Closed - Acme Corp",
        "body": (
            "Customer Name: Acme Corp\n"
            "Customer Contact: jane.doe@acmecorp.com\n"
            "AE: John Smith\n"
            "AE Phone: +1-555-123-4567\n"
            "Salesforce Opportunity: https://novacrm.salesforce.com/opportunity/006ABC\n"
        ),
    }

    info("Step 6.1 — Parsing email with LLM...")
    deal = intake.parse_email(sample_email)
    if deal:
        success(f"Parsed: {deal.customer_name} / {deal.ae_name}")
    else:
        fail("Email parsing failed")
        return False

    info("Step 6.2 — Voice call to confirm plan tier...")
    call_result = intake.confirm_plan_tier(deal)
    if call_result and call_result.plan_tier:
        success(f"Confirmed: {call_result.plan_tier.value} ({call_result.confidence:.0%})")
    else:
        fail("Voice confirmation failed")
        return False

    info("Step 6.3 — Creating Rocketlane project...")
    project = intake.create_project(deal, call_result.plan_tier)
    if project:
        success(f"Project: {project.project_name} (ID: {project.project_id})")
    else:
        fail("Project creation failed")
        return False

    info("Step 6.4 — Creating Slack channel...")
    channel = comms.setup_customer_channel(project, deal.customer_contact_email)
    if channel:
        success(f"Channel: #{channel.channel_name}")
    else:
        fail("Slack channel failed")

    # Audit trail
    trail = get_audit_trail()
    trail_path = export_audit_trail("logs/test_flow_audit.json")
    info(f"\nAudit trail: {len(trail)} entries exported to {trail_path}")
    for entry in trail:
        status = green("OK") if entry.success else red("FAIL")
        print(f"    {entry.timestamp.strftime('%H:%M:%S')} | {entry.agent_name:20s} | {entry.action:30s} | {status}")

    print(f"\n{green(bold('  FULL HAPPY PATH COMPLETE!'))}\n")
    return True


# ── Main ────────────────────────────────────────────────────────────

def step7_gmail():
    header(7, "Gmail IMAP Connection")

    address = os.getenv("GMAIL_ADDRESS", "")
    password = os.getenv("GMAIL_APP_PASSWORD", "")

    if not address or not password:
        fail("GMAIL_ADDRESS and GMAIL_APP_PASSWORD not set in .env")
        print(f"\n  Setup:")
        print(f"  1. Enable 2-Step Verification: https://myaccount.google.com/security")
        print(f"  2. Generate App Password: https://myaccount.google.com/apppasswords")
        print(f"  3. Add to .env:")
        print(f"     GMAIL_ADDRESS=your_email@gmail.com")
        print(f"     GMAIL_APP_PASSWORD=your_16_char_app_password\n")
        return False

    info(f"Gmail address: {address}")
    info("Connecting via IMAP...")

    from onboarding_agent.integrations.gmail_client import GmailClient

    try:
        client = GmailClient()
        emails = client.fetch_unread_deal_emails()

        if emails:
            success(f"Found {len(emails)} unread deal email(s)!")
            for e in emails:
                info(f"  Subject: {e.get('subject', 'N/A')}")
                info(f"  From: {e.get('from', 'N/A')}")
                body_preview = e.get('body', '')[:150].replace('\n', ' ')
                info(f"  Body: {body_preview}...")
                print()
        else:
            warn("No unread deal emails found.")
            info("Send yourself an email with subject containing 'new deal', 'closed won', or 'new customer'")
            info("Then run this step again.")

        client.disconnect()
        success("Gmail IMAP connection works!")
        return True

    except Exception as e:
        fail(f"Gmail connection failed: {e}")
        info("Check your GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env")
        return False


STEPS = {
    "1": ("Rocketlane API Connection", step1_rocketlane_connection),
    "2": ("Rocketlane Project Creation", step2_rocketlane_create_project),
    "3": ("Voice AI Call", step3_voice_call),
    "4": ("Email Parsing (LLM)", step4_email_parsing),
    "5": ("Slack Channel", step5_slack),
    "6": ("Full Happy Path", step6_full_flow),
    "7": ("Gmail IMAP Connection", step7_gmail),
}

def main():
    parser = argparse.ArgumentParser(description="Test the onboarding agent flow step by step")
    parser.add_argument("--step", default="all", help="Step number (1-6) or 'all'")
    args = parser.parse_args()

    print(bold("\n  NovaCRM Onboarding Agent — Step-by-Step Test Runner\n"))
    print("  Available steps:")
    for num, (name, _) in STEPS.items():
        print(f"    {num}. {name}")
    print()

    if args.step == "all":
        for num, (_, fn) in STEPS.items():
            fn()
    elif args.step in STEPS:
        STEPS[args.step][1]()
    else:
        print(red(f"  Unknown step: {args.step}"))
        print(f"  Use --step 1 through --step 6, or --step all")
        sys.exit(1)

if __name__ == "__main__":
    main()
