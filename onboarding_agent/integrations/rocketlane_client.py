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
        if not resp.ok:
            body = resp.text[:500] if resp.text else "(empty)"
            raise requests.HTTPError(
                f"{resp.status_code} {resp.reason} for {method} {endpoint}: {body}",
                response=resp,
            )
        return resp.json() if resp.content else {}

    def list_templates(self) -> list[dict[str, Any]]:
        endpoints = ["/templates", "/projects/templates", "/project-templates", "/templates/projects"]
        for ep in endpoints:
            try:
                data = self._request("GET", ep)
                templates = data if isinstance(data, list) else data.get("data", data.get("templates", data.get("results", [])))
                if templates:
                    log_action(
                        agent_name="RocketlaneClient",
                        action="templates_listed",
                        inputs={"endpoint": ep},
                        outputs={"count": len(templates), "names": [t.get("name", "") for t in templates]},
                        decision_rationale=f"Found {len(templates)} template(s) via {ep}",
                    )
                    return templates
            except requests.RequestException:
                continue
        raise requests.ConnectionError("No working templates endpoint found")

    def get_template_id(self, plan_tier: PlanTier) -> int | None:
        """Get the project template ID for a plan tier.

        Rocketlane's /templates API returns document templates, not project
        templates.  Project template IDs must be configured in .env (grab
        them from the Rocketlane portal URL).
        """
        env_id = (
            settings.ROCKETLANE_ENTERPRISE_TEMPLATE_ID if plan_tier == PlanTier.ENTERPRISE
            else settings.ROCKETLANE_GROWTH_TEMPLATE_ID
        )
        if env_id:
            log_action(
                agent_name="RocketlaneClient",
                action="template_resolved",
                inputs={"plan_tier": plan_tier.value, "template_id": env_id},
                outputs={},
                decision_rationale=f"Using project template ID {env_id} for {plan_tier.value}",
            )
            return int(env_id)

        log_action(
            agent_name="RocketlaneClient",
            action="template_not_configured",
            inputs={"plan_tier": plan_tier.value},
            outputs={},
            decision_rationale="No project template ID configured in .env; project will be created without a template",
        )
        return None

    def list_projects(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/projects")
        return data.get("data", data.get("projects", []))

    def create_project(
        self,
        customer_name: str,
        plan_tier: PlanTier,
        customer_email: str,
        template_id: int | str | None = None,
    ) -> OnboardingProject:
        config = PLAN_TIER_CONFIG[plan_tier]
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        payload: dict[str, Any] = {
            "projectName": f"{customer_name} - Onboarding",
            "startDate": start_date,
            "customer": {
                "companyName": customer_name,
            },
            "autoCreateCompany": True,
        }

        owner_email = settings.ESCALATION_EMAIL
        if owner_email and "@" in owner_email and "novacrm.com" not in owner_email:
            payload["owner"] = {"emailId": owner_email}
            payload["autoAssignOwner"] = True

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
            project_id = data.get("projectId", data.get("id", ""))
            print(f"  [DEBUG] Create project response keys: {list(data.keys())}")
            print(f"  [DEBUG] Extracted project_id: {project_id} (type: {type(project_id).__name__})")

            if template_id is not None:
                print(f"  [DEBUG] Importing template {template_id} into project {project_id}...")
                try:
                    self.import_template(str(project_id), int(template_id))
                    log_action(
                        agent_name="RocketlaneClient",
                        action="template_imported",
                        inputs={"project_id": str(project_id), "template_id": template_id},
                        outputs={},
                        decision_rationale=f"Imported template {template_id} into project {project_id}",
                    )
                except requests.RequestException as e:
                    log_action(
                        agent_name="RocketlaneClient",
                        action="template_import_failed",
                        inputs={"project_id": str(project_id), "template_id": template_id},
                        outputs={},
                        success=False,
                        error_message=str(e),
                        decision_rationale="Template import failed; project created without template",
                    )

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

    def import_template(self, project_id: str, template_id: int) -> dict[str, Any]:
        """Import a template into an existing project."""
        start_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        payload = {
            "startDate": start_date,
            "templateId": int(template_id),
        }
        return self._request("POST", f"/projects/{project_id}/import-template", payload)

    def check_existing_projects(self, customer_name: str) -> list[dict[str, Any]]:
        try:
            projects = self.list_projects()
        except requests.RequestException:
            log_action(
                agent_name="RocketlaneClient",
                action="duplicate_check_skipped",
                inputs={"customer_name": customer_name},
                outputs={},
                decision_rationale="Projects API unavailable; skipping duplicate check",
            )
            return []
        return [
            p
            for p in projects
            if customer_name.lower() in p.get("name", "").lower()
        ]

    def list_tasks(self, project_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/projects/{project_id}/tasks")
        return data.get("data", data.get("tasks", []))

    def get_overdue_tasks(self, project_id: str) -> list[dict[str, Any]]:
        """Fetch tasks and filter for overdue ones with days overdue count."""
        tasks = self.list_tasks(project_id)
        today = datetime.now(timezone.utc).date()
        overdue = []

        for task in tasks:
            due_str = task.get("dueDate") or task.get("endDate") or ""
            status = (task.get("status") or "").lower()
            if not due_str or status in ("completed", "done", "closed"):
                continue

            try:
                due_date = datetime.fromisoformat(due_str.replace("Z", "+00:00")).date()
            except (ValueError, TypeError):
                continue

            days_overdue = (today - due_date).days
            if days_overdue > 0:
                task["_days_overdue"] = days_overdue
                overdue.append(task)

        return overdue

    def check_overdue_escalations(self, project_id: str) -> dict[str, list[dict]]:
        """Check for overdue tasks and categorize by escalation level.

        Returns:
            {"notify_pm": [...], "notify_owner": [...]}
            - notify_pm: tasks overdue by 1+ days (PM notification)
            - notify_owner: tasks overdue by 4+ days (Owner escalation)
        """
        overdue = self.get_overdue_tasks(project_id)

        result: dict[str, list[dict]] = {"notify_pm": [], "notify_owner": []}
        for task in overdue:
            days = task["_days_overdue"]
            if days >= 4:
                result["notify_owner"].append(task)
            elif days >= 1:
                result["notify_pm"].append(task)

        log_action(
            agent_name="RocketlaneClient",
            action="overdue_check",
            inputs={"project_id": project_id},
            outputs={
                "total_overdue": len(overdue),
                "notify_pm": len(result["notify_pm"]),
                "notify_owner": len(result["notify_owner"]),
            },
            decision_rationale=(
                f"Found {len(overdue)} overdue task(s): "
                f"{len(result['notify_pm'])} for PM, {len(result['notify_owner'])} for Owner"
            ),
        )
        return result
