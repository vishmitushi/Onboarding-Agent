"""Tests for guardrails — validators and audit logging."""
from __future__ import annotations

import json
import os
import tempfile

from onboarding_agent.guardrails import (
    validate_deal_email,
    validate_voice_call_result,
    validate_project_not_duplicate,
    log_action,
    get_audit_trail,
    export_audit_trail,
)
from onboarding_agent.models import PlanTier, VoiceCallResult


class TestDealEmailValidation:

    def test_valid_email_passes(self):
        data = {
            "customer_name": "Acme",
            "customer_contact_email": "a@b.com",
            "ae_name": "John",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://salesforce.com/opp/1",
        }
        result = validate_deal_email(data)
        assert result.is_valid
        assert not result.errors

    def test_missing_fields_fail(self):
        result = validate_deal_email({})
        assert not result.is_valid
        assert len(result.errors) == 5

    def test_partial_missing_fails(self):
        data = {
            "customer_name": "Acme",
            "customer_contact_email": "a@b.com",
        }
        result = validate_deal_email(data)
        assert not result.is_valid
        assert len(result.errors) == 3

    def test_invalid_email_fails(self):
        data = {
            "customer_name": "X",
            "customer_contact_email": "not-email",
            "ae_name": "Y",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://salesforce.com/opp/1",
        }
        result = validate_deal_email(data)
        assert not result.is_valid

    def test_short_phone_fails(self):
        data = {
            "customer_name": "X",
            "customer_contact_email": "a@b.com",
            "ae_name": "Y",
            "ae_phone": "123",
            "salesforce_opportunity_link": "https://salesforce.com/opp/1",
        }
        result = validate_deal_email(data)
        assert not result.is_valid

    def test_non_salesforce_link_warns(self):
        data = {
            "customer_name": "X",
            "customer_contact_email": "a@b.com",
            "ae_name": "Y",
            "ae_phone": "+15551234567",
            "salesforce_opportunity_link": "https://example.com/opp/1",
        }
        result = validate_deal_email(data)
        assert result.is_valid
        assert len(result.warnings) == 1


class TestVoiceCallValidation:

    def test_valid_call_passes(self):
        result = validate_voice_call_result(VoiceCallResult(
            call_id="c1", status="completed",
            plan_tier=PlanTier.ENTERPRISE, confidence=0.95,
            transcript="Enterprise confirmed", duration_seconds=30,
        ))
        assert result.is_valid

    def test_incomplete_call_fails(self):
        result = validate_voice_call_result(VoiceCallResult(
            call_id="c2", status="no_answer",
        ))
        assert not result.is_valid

    def test_no_plan_tier_fails(self):
        result = validate_voice_call_result(VoiceCallResult(
            call_id="c3", status="completed",
            plan_tier=None, confidence=0.0,
            transcript="I don't know", duration_seconds=20,
        ))
        assert not result.is_valid

    def test_low_confidence_fails(self):
        result = validate_voice_call_result(VoiceCallResult(
            call_id="c4", status="completed",
            plan_tier=PlanTier.GROWTH, confidence=0.5,
            transcript="Maybe growth?", duration_seconds=15,
        ))
        assert not result.is_valid

    def test_short_call_warns(self):
        result = validate_voice_call_result(VoiceCallResult(
            call_id="c5", status="completed",
            plan_tier=PlanTier.ENTERPRISE, confidence=0.90,
            transcript="Enterprise", duration_seconds=3,
        ))
        assert result.is_valid
        assert len(result.warnings) == 1


class TestDuplicateProjectValidation:

    def test_no_duplicates_passes(self):
        result = validate_project_not_duplicate("NewCorp", [])
        assert result.is_valid

    def test_active_duplicate_fails(self):
        existing = [{"name": "NewCorp - Onboarding", "id": "p1", "status": "active"}]
        result = validate_project_not_duplicate("NewCorp", existing)
        assert not result.is_valid

    def test_completed_project_passes(self):
        existing = [{"name": "NewCorp - Onboarding", "id": "p1", "status": "completed"}]
        result = validate_project_not_duplicate("NewCorp", existing)
        assert result.is_valid

    def test_case_insensitive_match(self):
        existing = [{"name": "NEWCORP - Onboarding", "id": "p1", "status": "in_progress"}]
        result = validate_project_not_duplicate("newcorp", existing)
        assert not result.is_valid


class TestOverdueEscalation:
    """Tests for overdue task detection and escalation levels."""

    def _make_task(self, name: str, days_ago: int, status: str = "in_progress") -> dict:
        from datetime import datetime, timedelta, timezone
        due = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        return {"name": name, "dueDate": due, "status": status}

    def test_task_overdue_1_day_notifies_pm(self):
        from unittest.mock import MagicMock, patch
        client = MagicMock()
        from onboarding_agent.integrations.rocketlane_client import RocketlaneClient
        rc = RocketlaneClient.__new__(RocketlaneClient)
        rc.api_key = "test"
        rc.base_url = "http://test"
        rc.headers = {}

        tasks = [self._make_task("Kickoff Call", 2)]
        with patch.object(rc, "list_tasks", return_value=tasks):
            result = rc.check_overdue_escalations("proj_1")
        assert len(result["notify_pm"]) == 1
        assert result["notify_pm"][0]["name"] == "Kickoff Call"
        assert len(result["notify_owner"]) == 0

    def test_task_overdue_4_days_notifies_owner(self):
        from unittest.mock import patch
        from onboarding_agent.integrations.rocketlane_client import RocketlaneClient
        rc = RocketlaneClient.__new__(RocketlaneClient)
        rc.api_key = "test"
        rc.base_url = "http://test"
        rc.headers = {}

        tasks = [self._make_task("Data Migration", 5)]
        with patch.object(rc, "list_tasks", return_value=tasks):
            result = rc.check_overdue_escalations("proj_1")
        assert len(result["notify_owner"]) == 1
        assert result["notify_owner"][0]["name"] == "Data Migration"
        assert len(result["notify_pm"]) == 0

    def test_completed_task_not_flagged(self):
        from unittest.mock import patch
        from onboarding_agent.integrations.rocketlane_client import RocketlaneClient
        rc = RocketlaneClient.__new__(RocketlaneClient)
        rc.api_key = "test"
        rc.base_url = "http://test"
        rc.headers = {}

        tasks = [self._make_task("Done Task", 10, status="completed")]
        with patch.object(rc, "list_tasks", return_value=tasks):
            result = rc.check_overdue_escalations("proj_1")
        assert len(result["notify_pm"]) == 0
        assert len(result["notify_owner"]) == 0

    def test_mixed_overdue_levels(self):
        from unittest.mock import patch
        from onboarding_agent.integrations.rocketlane_client import RocketlaneClient
        rc = RocketlaneClient.__new__(RocketlaneClient)
        rc.api_key = "test"
        rc.base_url = "http://test"
        rc.headers = {}

        tasks = [
            self._make_task("Kickoff", 2),
            self._make_task("Config", 5),
            self._make_task("Go-Live", 0),  # not overdue
        ]
        with patch.object(rc, "list_tasks", return_value=tasks):
            result = rc.check_overdue_escalations("proj_1")
        assert len(result["notify_pm"]) == 1
        assert len(result["notify_owner"]) == 1


class TestAuditLogging:

    def test_log_action_records(self):
        log_action("TestAgent", "test_action", {"key": "val"}, {"out": 1})
        trail = get_audit_trail()
        assert len(trail) == 1
        assert trail[0].agent_name == "TestAgent"

    def test_export_creates_file(self):
        log_action("TestAgent", "export_test")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_audit_trail(path)
            with open(path) as f:
                data = json.load(f)
            assert len(data) == 1
        finally:
            os.unlink(path)
