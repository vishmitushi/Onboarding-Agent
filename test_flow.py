"""
Step-by-step test runner for the onboarding agent flow.

Run individual steps to test each integration:
    python test_flow.py --step 1     # Test Rocketlane API connection
    python test_flow.py --step 2     # Test Rocketlane project creation
    python test_flow.py --step 3     # Test Bland.ai voice call
    python test_flow.py --step 4     # Test email parsing (LLM)
    python test_flow.py --step 5     # Test Slack channel creation
    python test_flow.py --step 6     # Test full happy path (all integrations)
    python test_flow.py --step 7     # Test Gmail IMAP connection
    python test_flow.py --step 8     # Test overdue task escalation
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

    info("Resolving template ID for Enterprise plan...")
    template_id = client.get_template_id(PlanTier.ENTERPRISE)
    if template_id:
        success(f"Template ID: {template_id}")
    else:
        warn("No template ID configured. Set ROCKETLANE_ENTERPRISE_TEMPLATE_ID in .env")

    info("Creating test project: 'Test Corp - Onboarding' (Enterprise)...")
    try:
        project = client.create_project(
            customer_name="Test Corp",
            plan_tier=PlanTier.ENTERPRISE,
            customer_email="test@testcorp.com",
            template_id=template_id,
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

    if provider in ("vapi", "vapi_web", "vapi_inbound", "vapi_outbound"):
        api_key = os.getenv("VAPI_API_KEY", "")
        if not api_key:
            fail("VAPI_API_KEY not set in .env")
            return False
        info(f"Vapi.ai API key found: {api_key[:8]}...")
        if provider in ("vapi", "vapi_web"):
            public_key = os.getenv("VAPI_PUBLIC_KEY", "")
            if not public_key:
                fail("VAPI_PUBLIC_KEY not set in .env")
                info("Get it from: https://dashboard.vapi.ai → Account → Public Key")
                return False
            info(f"Vapi public key found: {public_key[:8]}...")
        else:
            phone_id = os.getenv("VAPI_PHONE_ID", "")
            if not phone_id:
                fail("VAPI_PHONE_ID not set in .env")
                return False
            info(f"Phone Number ID: {phone_id[:8]}...")
    else:
        api_key = os.getenv("BLAND_API_KEY", "")
        if not api_key:
            fail("BLAND_API_KEY not set in .env")
            return False
        info(f"Bland.ai API key found: {api_key[:8]}...")

    from onboarding_agent.integrations.voice_client import BlandVoiceClient
    client = BlandVoiceClient()

    if provider in ("vapi", "vapi_web"):
        # Web mode: browser-based call (no phone needed)
        info(f"Mode: Web Call — a browser page will open for voice chat")
        info(f"Allow microphone access and talk to the AI.\n")

        result = client.call_ae_for_plan_confirmation(
            ae_phone="web",
            ae_name="Test AE",
            customer_name="Acme Corp",
        )
    elif provider == "vapi_inbound":
        # Inbound mode: AE calls our Vapi number
        vapi_number = os.getenv("VAPI_PHONE_NUMBER", "")
        if not vapi_number:
            fail("VAPI_PHONE_NUMBER not set in .env")
            return False
        info(f"Mode: Inbound — you will call the Vapi number")
        info(f"The AI assistant will pick up and ask about the plan tier.\n")

        result = client.call_ae_for_plan_confirmation(
            ae_phone="inbound",
            ae_name="Test AE",
            customer_name="Acme Corp",
        )
    else:
        # Outbound mode: system calls the AE
        phone = input(f"  Enter your phone number to receive the test call (e.g. +919876543210): ").strip()
        if not phone:
            warn("No phone number provided, skipping live call test")
            return True

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


# ── Step 6: Full Happy Path (Direct Agent Calls, No LangGraph) ─────

def step6_full_flow():
    header(6, "Full Happy Path — Direct Agent Calls (no LangGraph)")

    from onboarding_agent.agents.intake_agent import IntakeAgent
    from onboarding_agent.agents.communication_agent import CommunicationAgent
    from onboarding_agent.guardrails import get_audit_trail, clear_audit_trail, export_audit_trail

    clear_audit_trail()

    intake = IntakeAgent()
    comms = CommunicationAgent()

    sample_email = {
        "message_id": "flow_test_001",
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

    info("Step 6.1 — Parsing email with Groq LLM...")
    deal = intake.parse_email(sample_email)
    if deal:
        success(f"Parsed: {deal.customer_name} / {deal.ae_name} / {deal.customer_contact_email}")
    else:
        fail("Email parsing failed")
        return False

    info("Step 6.2 — Voice call to confirm plan tier (Vapi web call)...")
    call_result = intake.confirm_plan_tier(deal)
    if call_result and call_result.plan_tier:
        success(f"Confirmed: {call_result.plan_tier.value} (confidence: {call_result.confidence:.0%})")
    else:
        fail("Voice confirmation failed or confidence too low")
        return False

    info(f"Step 6.3 — Creating Rocketlane project ({call_result.plan_tier.value})...")
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
        warn("Slack channel failed (non-blocking)")

    trail = get_audit_trail()
    trail_path = export_audit_trail("logs/test_flow_step6_audit.json")
    info(f"\nAudit trail: {len(trail)} entries → {trail_path}")
    for entry in trail:
        st = green("OK") if entry.success else red("FAIL")
        print(f"    {entry.timestamp.strftime('%H:%M:%S')} | {entry.agent_name:20s} | {entry.action:30s} | {st}")

    print(f"\n{green(bold('  STEP 6 COMPLETE'))}\n")
    return True


# ── Step 9: Full LangGraph Workflow + Gmail ───────────────────────────

def step9_langgraph_flow():
    header(9, "Full LangGraph Workflow + Gmail Trigger (LIVE)")

    from onboarding_agent.graph.workflow import onboarding_workflow
    from onboarding_agent.graph.nodes import set_agents
    from onboarding_agent.agents.intake_agent import IntakeAgent
    from onboarding_agent.agents.communication_agent import CommunicationAgent
    from onboarding_agent.guardrails import get_audit_trail, clear_audit_trail, export_audit_trail

    clear_audit_trail()
    set_agents(IntakeAgent(), CommunicationAgent())

    # ── Fetch real email from Gmail ──
    gmail_address = os.getenv("GMAIL_ADDRESS", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")

    raw_email = None
    if gmail_address and gmail_password:
        info("Checking Gmail for unread deal emails...")
        try:
            from onboarding_agent.integrations.gmail_client import GmailClient
            gmail = GmailClient()
            emails = gmail.fetch_unread_deal_emails()
            if emails:
                raw_email = emails[0]
                success(f"Using real email: \"{raw_email.get('subject', '')}\"")
                gmail.disconnect()
            else:
                warn("No unread deal emails found — using sample email instead")
                info("To use a real email: send yourself an email with subject containing 'new deal'")
                gmail.disconnect()
        except Exception as e:
            warn(f"Gmail connection failed: {e} — using sample email instead")

    if raw_email is None:
        raw_email = {
            "message_id": "sample_001",
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
        info("Using sample deal email")

    info("\nInvoking LangGraph workflow: onboarding_workflow.invoke()")
    info("Graph: parse_email → call_ae → validate_voice → check_duplicate → create_project → create_slack_channel")
    info("A browser page will open for the voice call — say 'Enterprise' or 'Growth'.\n")

    try:
        final_state = onboarding_workflow.invoke({"raw_email": raw_email})
    except Exception as e:
        fail(f"Workflow crashed: {e}")
        return False

    # ── Display results ──
    status = final_state.get("status", "unknown")
    error = final_state.get("error", "")

    print(f"\n{'='*60}")
    if status == "completed":
        print(green(bold("  LANGGRAPH WORKFLOW — COMPLETED SUCCESSFULLY")))
    elif status == "escalated":
        print(yellow(bold("  LANGGRAPH WORKFLOW — ESCALATED TO HUMAN")))
    else:
        print(red(bold(f"  LANGGRAPH WORKFLOW — STATUS: {status}")))
    print(f"{'='*60}\n")

    deal_data = final_state.get("deal_data")
    if deal_data:
        success(f"Email parsed: {deal_data['customer_name']} / {deal_data['ae_name']}")
    else:
        fail("Email parsing failed")

    plan_tier = final_state.get("plan_tier")
    voice_result = final_state.get("voice_result")
    if plan_tier:
        confidence = voice_result.get("confidence", 0) if voice_result else 0
        success(f"Plan confirmed: {plan_tier} (confidence: {confidence:.0%})")
    elif voice_result:
        warn(f"Voice call done but plan not confirmed (attempts: {final_state.get('voice_attempts', 0)})")

    project_data = final_state.get("project_data")
    if project_data:
        success(f"Project created: {project_data['project_name']} (ID: {project_data['project_id']})")
        info(f"  Plan: {project_data['plan_tier']} | Duration: {project_data['duration_days']} days | CSM: {project_data['csm_type']}")

    slack_data = final_state.get("slack_channel_data")
    if slack_data:
        success(f"Slack channel: #{slack_data['channel_name']}")

    if error:
        info(f"  Note: {error}")

    trail = get_audit_trail()
    trail_path = export_audit_trail("logs/test_flow_langgraph_audit.json")
    info(f"\nAudit trail: {len(trail)} entries → {trail_path}")
    for entry in trail:
        st = green("OK") if entry.success else red("FAIL")
        print(f"    {entry.timestamp.strftime('%H:%M:%S')} | {entry.agent_name:20s} | {entry.action:30s} | {st}")

    print()
    return status == "completed"


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


def step8_overdue_check():
    header(8, "Overdue Task Escalation Check")

    api_key = os.getenv("ROCKETLANE_API_KEY", "")
    if not api_key:
        fail("ROCKETLANE_API_KEY not set in .env")
        return False

    from onboarding_agent.integrations.rocketlane_client import RocketlaneClient

    client = RocketlaneClient()

    info("Fetching all projects from Rocketlane...")
    try:
        projects = client.list_projects()
        if not projects:
            warn("No projects found in Rocketlane — create one first (step 2 or step 6)")
            return True

        success(f"Found {len(projects)} project(s)")

        for proj in projects[:5]:
            project_id = proj.get("id", proj.get("projectId", ""))
            project_name = proj.get("projectName", proj.get("name", "Unknown"))
            info(f"\n  Checking: {project_name} (ID: {project_id})")

            try:
                escalations = client.check_overdue_escalations(str(project_id))
                pm_tasks = escalations["notify_pm"]
                owner_tasks = escalations["notify_owner"]

                if not pm_tasks and not owner_tasks:
                    success(f"  No overdue tasks!")
                else:
                    if owner_tasks:
                        for t in owner_tasks:
                            name = t.get("name", t.get("taskName", "Unnamed"))
                            days = t.get("_days_overdue", "?")
                            fail(f"  OWNER ESCALATION: {name} — {days} days overdue")
                    if pm_tasks:
                        for t in pm_tasks:
                            name = t.get("name", t.get("taskName", "Unnamed"))
                            days = t.get("_days_overdue", "?")
                            warn(f"  PM NOTIFICATION:  {name} — {days} days overdue")

                    # Post to Slack if token is set
                    slack_token = os.getenv("SLACK_BOT_TOKEN", "")
                    if slack_token and (pm_tasks or owner_tasks):
                        from onboarding_agent.integrations.slack_client import SlackClient
                        slack = SlackClient()
                        try:
                            slack.post_overdue_alert("general", escalations)
                            success(f"  Slack alert posted to #general")
                        except Exception as e:
                            warn(f"  Slack alert failed: {e}")

            except Exception as e:
                warn(f"  Could not check tasks: {e}")

        return True

    except Exception as e:
        fail(f"Failed to fetch projects: {e}")
        return False


STEPS = {
    "1": ("Rocketlane API Connection", step1_rocketlane_connection),
    "2": ("Rocketlane Project Creation", step2_rocketlane_create_project),
    "3": ("Voice AI Call", step3_voice_call),
    "4": ("Email Parsing (LLM)", step4_email_parsing),
    "5": ("Slack Channel", step5_slack),
    "6": ("Full Happy Path (Direct)", step6_full_flow),
    "7": ("Gmail IMAP Connection", step7_gmail),
    "8": ("Overdue Task Escalation", step8_overdue_check),
    "9": ("LangGraph Workflow + Gmail", step9_langgraph_flow),
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
            if num in ("8", "9"):
                continue
            fn()
    elif args.step in STEPS:
        STEPS[args.step][1]()
    else:
        print(red(f"  Unknown step: {args.step}"))
        print(f"  Use --step 1 through --step 6, or --step all")
        sys.exit(1)

if __name__ == "__main__":
    main()
