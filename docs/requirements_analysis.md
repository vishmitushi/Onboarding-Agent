 

# Requirements & Workflow Analysis

## Customer Onboarding Automation — NovaCRM

---

## 1. Current Workflow Analysis

### Process Flow (As-Is)

```
Deal Closes → AE emails CS inbox → Manual Asana project creation → 
Slack channel creation → Kickoff call → 4-phase onboarding → 
Handoff summary → Transfer to support
```

### Identified Pain Points

| # | Pain Point                                      | Impact                                     | Root Cause                                             |
| - | ----------------------------------------------- | ------------------------------------------ | ------------------------------------------------------ |
| 1 | Manual project creation from email              | Delays, human error                        | No automation between deal close and project setup     |
| 2 | Template mix-ups (Enterprise vs Growth)         | Wrong timelines, wrong CSM assignments     | Plan tier not included in AE's email; relies on memory |
| 3 | Missed escalations on blocked tasks             | Customer dissatisfaction, SLA breaches     | Manual tracking with no automated alerting             |
| 4 | Data migration marked done without verification | Ugly escalations, trust erosion            | No validation checkpoint in the process                |
| 5 | Manual Slack channel setup                      | Delayed communication, inconsistent naming | Repetitive manual work                                 |

### Two Customer Profiles

| Attribute           | Enterprise                                            | Growth              |
| ------------------- | ----------------------------------------------------- | ------------------- |
| Onboarding duration | 30 days                                               | 14 days             |
| CSM assignment      | Dedicated                                             | Pooled              |
| Phases              | Kickoff → Data Migration → Configuration → Go-Live | Same                |
| Tasks               | ~15 across 4 phases                                   | ~15 across 4 phases |

---

## 2. Proposed Agentic Workflow

### Framework: LangGraph (by LangChain)

The solution uses **LangGraph** — a graph-based agentic framework for building stateful, multi-step AI workflows. Each step in the onboarding process is a **node** in the graph, and every guardrail decision point is a **conditional edge** that routes the workflow based on validation results.

This is a deliberate architectural choice:

- **Explicit control flow** — the graph structure makes the workflow visible and auditable, unlike imperative if-else chains buried in code.
- **Built-in retry loops** — the voice call retry is a graph cycle (`call_ae ↔ validate_voice`), not manual loop logic.
- **Separation of concerns** — each node owns one step; routing logic is separate from business logic.
- **State management** — `OnboardingState` (a TypedDict) flows through the graph, so every node sees the full context and writes only the keys it owns.

### Architecture Overview

```
                         ┌─────────────────────┐
                         │   Gmail Polling      │
                         │   (Orchestrator)     │
                         └──────────┬───────────┘
                                    │ new deal email
                                    ▼
                    ┌───────────────────────────────────┐
                    │        LangGraph Workflow          │
                    │        (StateGraph)                │
                    │                                   │
                    │   ┌─────────────┐                 │
                    │   │ parse_email  │ ← Claude LLM   │
                    │   └──────┬──────┘                 │
                    │          │                         │
                    │   ┌──────▼──────┐                 │
                    │   │validate_email│ ← Guardrail    │
                    │   └──────┬──────┘                 │
                    │          │                         │
                    │   ┌──────▼──────┐                 │
                    │   │  call_ae    │ ← Bland.ai      │
                    │   └──────┬──────┘                 │
                    │          │        ▲ retry          │
                    │   ┌──────▼──────┐ │               │
                    │   │validate_voice│─┘ ← Guardrail  │
                    │   └──────┬──────┘                 │
                    │          │                         │
                    │   ┌──────▼───────┐                │
                    │   │check_duplicate│ ← Guardrail   │
                    │   └──────┬───────┘                │
                    │          │                         │
                    │   ┌──────▼───────┐                │
                    │   │create_project│ ← Rocketlane   │
                    │   └──────┬───────┘                │
                    │          │                         │
                    │   ┌──────▼────────────┐           │
                    │   │create_slack_channel│ ← Slack   │
                    │   └──────────────────┘            │
                    │                                   │
                    │   Any failure → escalate node     │
                    └───────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────────┐
                    │      Rocketlane Automation         │
                    │  (configured in-platform)          │
                    │                                   │
                    │  Overdue 1 day → Notify PM        │
                    │  Overdue 4 days → Notify Owner    │
                    └───────────────────────────────────┘
```

### Agent 1 — Intake & Routing (Nodes: parse_email → validate_email → call_ae → validate_voice → check_duplicate → create_project)

**Trigger**: New unread email in Gmail matching deal notification pattern.

**Pipeline**:

1. **parse_email** — Claude LLM extracts structured fields (customer name, contact email, AE name, AE phone, Salesforce link) from the raw email body. Returns JSON.
2. **validate_email** — Pydantic + custom validators ensure all required fields are present and well-formed. Missing fields → conditional edge routes to `escalate` node, never guesses.
3. **call_ae** — Bland.ai Voice AI places a real outbound call to the AE's phone number. The voice agent asks the AE to confirm the plan tier (Enterprise or Growth). The response is transcribed.
4. **validate_voice** — Transcript is analyzed for plan tier keywords and confirmation phrases. A confidence score is computed. If confidence < 80%, the conditional edge routes back to `call_ae` (retry loop, up to 2 retries). After max retries → routes to `escalate`.
5. **check_duplicate** — Queries Rocketlane API for existing active projects matching the customer name. Duplicate found → routes to `escalate`.
6. **create_project** — Calls the Rocketlane API (`POST /projects`) with the correct template and timeline based on confirmed plan tier.

### Agent 2 — Communication (Node: create_slack_channel)

**Trigger**: Successful project creation (conditional edge from `create_project`).

**Actions**:

1. Create a Slack channel named `onb-{customer}-{tier}`.
2. Set the channel topic with customer name, plan tier, duration, and CSM type.
3. Post a personalized welcome message that varies based on plan tier — Enterprise customers are told about their dedicated CSM, Growth customers are told about pooled support.
4. Slack failure is non-blocking — the project is already created, failure is logged for manual follow-up.

### Escalation Node

**Triggered by** any failed guardrail: malformed email, ambiguous voice response, duplicate project, API failure.

**Action**: Logs the full context (what failed, why, all data collected so far) and marks the workflow as `escalated` for manual resolution by the CS Manager.

### Rocketlane Automation (In-Platform)

Configured directly within Rocketlane's automation engine (not a separate agent):

- **Rule 1**: When a task is overdue by 1 day → Send notification to the Project Manager.
- **Rule 2**: When a task is overdue by 4 days → Send notification to the Project Owner.

---

## 3. Guardrails

Each guardrail is implemented as a **conditional edge** in the LangGraph workflow — the graph's routing logic decides which node runs next based on the validation outcome.

| Guardrail               | Implementation                                         | Graph Behavior                                                  | Rationale                                               |
| ----------------------- | ------------------------------------------------------ | --------------------------------------------------------------- | ------------------------------------------------------- |
| Field validation        | Pydantic models + custom validators                    | `validate_email` → fail → `escalate`                      | Never guess missing customer details                    |
| Voice confirmation gate | Confidence score ≥ 80% required                       | `validate_voice` → fail → retry `call_ae` or `escalate` | Prevents template mix-ups — the #1 reported pain point |
| Retry + escalation      | Up to 2 voice call retries                             | Graph cycle: `call_ae` ↔ `validate_voice`, max 2 loops     | Graceful degradation without blocking indefinitely      |
| Duplicate detection     | Query Rocketlane for active projects                   | `check_duplicate` → dup found → `escalate`                | Prevents duplicate onboarding projects                  |
| Full audit trail        | Every node logs: timestamp, inputs, outputs, rationale | Runs in every node, exported as JSON                            | Traceability and debugging                              |
| LLM output validation   | JSON parsing with fallback                             | `parse_email` → fail → `escalate`                         | Protect against LLM hallucinations or format errors     |

---

## 4. Assumptions

1. AEs include their phone number in the email or it can be looked up from a directory. The current implementation expects it in the email body.
2. The Salesforce opportunity link is included in every deal notification email.
3. Bland.ai (or equivalent Voice AI) free tier is sufficient for the demo. In production, a paid plan would handle call volume.
4. Slack channel creation uses the Slack Bot API. For the demo, a mock implementation is acceptable (as noted in the assessment guidelines).
5. Rocketlane's API supports project creation with template sources. The implementation uses the documented API at `https://api.rocketlane.com/api/1.0` with `api-key` header authentication.
6. The 4 onboarding phases (Kickoff, Data Migration, Configuration, Go-Live) are the same for both Enterprise and Growth plans — only the timeline and CSM assignment differ.
7. A single AE sends each deal notification email. Multi-AE deals are out of scope.
8. The assessment mentions Priya's concern about unverified data migrations. While this is out of scope for the current implementation, the LangGraph architecture allows adding a verification node in a future iteration.

---

## 5. Technology Stack

| Component          | Technology                         | Rationale                                                                      |
| ------------------ | ---------------------------------- | ------------------------------------------------------------------------------ |
| Language           | Python 3.11+                       | Ecosystem, typing, async-ready                                                 |
| Agentic Framework  | **LangGraph** (by LangChain) | Graph-based workflow with conditional edges, retry loops, and state management |
| LLM                | Claude (Anthropic API)             | Reliable structured extraction from unstructured email text                    |
| Voice AI           | Bland.ai                           | Real outbound AI voice calls, free tier, transcription                         |
| Project Management | Rocketlane API                     | Per the assignment requirement                                                 |
| Communication      | Slack API (mock-compatible)        | Standard CS team communication                                                 |
| Data Validation    | Pydantic v2                        | Type-safe models with built-in validation                                      |
| Email              | Gmail API (OAuth2)                 | Real inbox monitoring                                                          |
| Testing            | pytest (45 tests)                  | Unit + integration + workflow tests with mocked external APIs                  |

---

## 6. Project Structure

```
onboarding_agent/
├── config.py                      ← Settings from .env
├── main.py                        ← LangGraph orchestrator + Gmail polling
├── graph/                         ← LangGraph workflow
│   ├── state.py                   ← OnboardingState TypedDict
│   ├── nodes.py                   ← 8 node functions + 5 routing functions
│   └── workflow.py                ← StateGraph construction + compilation
├── agents/
│   ├── intake_agent.py            ← Agent 1: email parsing, voice call, project creation
│   └── communication_agent.py     ← Agent 2: Slack channel setup
├── integrations/
│   ├── gmail_client.py            ← Gmail API (OAuth2)
│   ├── voice_client.py            ← Bland.ai voice calls
│   ├── rocketlane_client.py       ← Rocketlane API
│   └── slack_client.py            ← Slack Bot API
├── guardrails/
│   ├── logger.py                  ← Audit trail with JSON export
│   └── validators.py              ← Field, voice, and duplicate validators
├── models/
│   └── schemas.py                 ← Pydantic models + tier config
└── tests/                         ← 45 automated tests
    ├── test_intake_agent.py       ← 19 agent-level tests
    ├── test_communication_agent.py ← 4 communication tests
    ├── test_guardrails.py         ← 17 guardrail tests
    └── test_workflow.py           ← 9 LangGraph workflow tests
```

---

## 7. Test Coverage

| Category            | Count        | What's Tested                                                         |
| ------------------- | ------------ | --------------------------------------------------------------------- |
| Happy Path          | 6            | Full E2E for Enterprise + Growth through LangGraph and agent levels   |
| Validation          | 8            | Missing fields, invalid email, short phone, malformed LLM output      |
| Template Accuracy   | 2            | Enterprise → 30-day/dedicated, Growth → 14-day/pooled               |
| Voice Edge Cases    | 4            | AE no-answer, ambiguous response, retry logic, escalation             |
| Duplicate Detection | 4            | Active duplicate blocked, completed project allowed, case-insensitive |
| API Failures        | 2            | Rocketlane down, Slack failure (non-blocking)                         |
| Audit Trail         | 3            | Logging, JSON export, all steps logged in workflow                    |
| **Total**     | **45** |                                                                       |

---

## 8. Production Considerations

- **Idempotency**: Email message IDs are tracked to prevent reprocessing. Processed emails are marked as read.
- **Rate Limiting**: API calls to Rocketlane and Slack should respect rate limits with exponential backoff in production.
- **Secrets Management**: API keys stored in environment variables; `.env` file excluded from version control.
- **Monitoring**: The audit trail provides operational visibility. In production, integrate with Datadog or similar observability tools.
- **Scalability**: LangGraph supports async execution. The polling loop could be replaced with a Gmail push notification webhook for real-time processing.
- **Data Migration Verification**: Priya mentioned unverified data migrations causing escalations. A future enhancement could add a verification node to the LangGraph workflow that checks migration completeness before allowing the task to be marked done.
