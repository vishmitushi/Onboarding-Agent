from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action
from onboarding_agent.models import OnboardingProject, PlanTier, PLAN_TIER_CONFIG


class RocketlaneClient:
    """Client for Rocketlane API — project and template management.

    API docs: https://developer.rocketlane.com/reference/create-project
    Auth: api-key header
    Base URL: https://api.rocketlane.com/api/1.0
    """

    def __init__(self) -> None:
        self.api_key = settings.ROCKETLANE_API_KEY
        self.base_url = settings.ROCKETLANE_API_URL
        self.headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self, method: str, endpoint: str, payload: dict | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        resp = requests.request(
            method, url, json=payload, headers=self.headers, timeout=30,
            verify=settings.ROCKETLANE_VERIFY_SSL,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def list_templates(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/projects/templates")
        return data.get("data", data.get("templates", []))

    def get_template_id(self, plan_tier: PlanTier) -> str | None:
        """Find the template matching the plan tier."""
        config = PLAN_TIER_CONFIG[plan_tier]
        template_name = config["template_name"]
        templates = self.list_templates()

        for tpl in templates:
            if template_name.lower() in tpl.get("name", "").lower():
                return tpl.get("id", tpl.get("templateId"))
        return None

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/projects")
        return data.get("data", data.get("projects", []))

    def create_project(
        self,
        customer_name: str,
        plan_tier: PlanTier,
        customer_email: str,
        template_id: str | None = None,
    ) -> OnboardingProject:
        config = PLAN_TIER_CONFIG[plan_tier]
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        payload: dict[str, Any] = {
            "projectName": f"{customer_name} - Onboarding",
            "startDate": start_date,
            "customer": {
                "companyName": customer_name,
            },
            "owner": {
                "emailId": settings.ESCALATION_EMAIL,
            },
            "assignProjectOwner": True,
        }

        if template_id:
            payload["sources"] = [{"id": template_id}]

        log_action(
            agent_name="RocketlaneClient",
            action="create_project",
            inputs={
                "customer_name": customer_name,
                "plan_tier": plan_tier.value,
                "template_id": template_id,
            },
            outputs={},
            decision_rationale=(
                f"Creating {plan_tier.value} onboarding project "
                f"with {config['duration_days']}-day timeline"
            ),
        )

        try:
            data = self._request("POST", "/projects", payload)
            project_id = data.get("id", data.get("projectId", ""))

            project = OnboardingProject(
                project_id=str(project_id),
                project_name=f"{customer_name} - Onboarding",
                customer_name=customer_name,
                plan_tier=plan_tier,
                template_name=config["template_name"],
                duration_days=config["duration_days"],
                csm_type=config["csm_type"],
            )

            log_action(
                agent_name="RocketlaneClient",
                action="project_created",
                inputs={"project_id": project.project_id},
                outputs=project.model_dump(mode="json"),
                decision_rationale="Project created successfully in Rocketlane",
            )
            return project

        except requests.RequestException as e:
            log_action(
                agent_name="RocketlaneClient",
                action="create_project_failed",
                inputs=payload,
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="Rocketlane API call failed",
            )
            raise

    def check_existing_projects(self, customer_name: str) -> list[dict[str, Any]]:
        projects = self.list_projects()
        return [
            p
            for p in projects
            if customer_name.lower() in p.get("name", "").lower()
        ]
