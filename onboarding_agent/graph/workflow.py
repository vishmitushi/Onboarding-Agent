"""LangGraph workflow definition for the onboarding automation.

Graph structure:
    START
      │
      ▼
    parse_email
      │
      ├── deal_data exists ──► validate_email
      │                            │
      │                            ├── valid ──► call_ae
      │                            │               │
      │                            │               ▼
      │                            │          validate_voice
      │                            │               │
      │                            │               ├── confirmed ──► check_duplicate
      │                            │               │                      │
      │                            │               │                      ├── no dup ──► create_project
      │                            │               │                      │                   │
      │                            │               │                      │                   ▼
      │                            │               │                      │            create_slack_channel
      │                            │               │                      │                   │
      │                            │               │                      │                   ▼
      │                            │               │                      │                  END
      │                            │               │                      │
      │                            │               │                      └── dup found ──► escalate ──► END
      │                            │               │
      │                            │               ├── retry ──► call_ae (loop)
      │                            │               │
      │                            │               └── max retries ──► escalate ──► END
      │                            │
      │                            └── invalid ──► escalate ──► END
      │
      └── parse failed ──► escalate ──► END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from onboarding_agent.graph.state import OnboardingState
from onboarding_agent.graph import nodes


def build_onboarding_graph() -> StateGraph:
    """Construct and compile the onboarding workflow graph."""

    graph = StateGraph(OnboardingState)

    # ── Add nodes ──
    graph.add_node("parse_email", nodes.parse_email)
    graph.add_node("validate_email", nodes.validate_email)
    graph.add_node("call_ae", nodes.call_ae)
    graph.add_node("validate_voice", nodes.validate_voice)
    graph.add_node("check_duplicate", nodes.check_duplicate)
    graph.add_node("create_project", nodes.create_project)
    graph.add_node("create_slack_channel", nodes.create_slack_channel)
    graph.add_node("escalate", nodes.escalate)

    # ── Entry point ──
    graph.add_edge(START, "parse_email")

    # ── Conditional edges (guardrail decision points) ──
    graph.add_conditional_edges(
        "parse_email",
        nodes.route_after_parse,
        {"validate_email": "validate_email", "escalate": "escalate"},
    )

    graph.add_conditional_edges(
        "validate_email",
        nodes.route_after_validation,
        {"call_ae": "call_ae", "escalate": "escalate"},
    )

    # call_ae always goes to validate_voice
    graph.add_edge("call_ae", "validate_voice")

    graph.add_conditional_edges(
        "validate_voice",
        nodes.route_after_voice_validation,
        {
            "check_duplicate": "check_duplicate",
            "call_ae": "call_ae",        # retry loop
            "escalate": "escalate",
        },
    )

    graph.add_conditional_edges(
        "check_duplicate",
        nodes.route_after_duplicate_check,
        {"create_project": "create_project", "escalate": "escalate"},
    )

    # create_project goes to Slack on success, escalate on failure
    graph.add_conditional_edges(
        "create_project",
        nodes.route_after_create_project,
        {"create_slack_channel": "create_slack_channel", "escalate": "escalate"},
    )

    # Terminal nodes
    graph.add_edge("create_slack_channel", END)
    graph.add_edge("escalate", END)

    return graph.compile()


# Pre-compiled graph instance
onboarding_workflow = build_onboarding_graph()
