from __future__ import annotations

import time

import requests

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action
from onboarding_agent.models import PlanTier, VoiceCallResult


class BlandVoiceClient:
    """Client for Bland.ai Voice API to make outbound calls to AEs."""

    def __init__(self) -> None:
        self.api_key = settings.BLAND_API_KEY
        self.base_url = settings.BLAND_API_URL
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    def call_ae_for_plan_confirmation(
        self,
        ae_phone: str,
        ae_name: str,
        customer_name: str,
    ) -> VoiceCallResult:
        prompt = (
            f"You are calling {ae_name}, an Account Executive at NovaCRM. "
            f"A new deal has just closed for customer '{customer_name}'. "
            f"You need to confirm one thing: Is this customer on the Enterprise plan "
            f"or the Growth plan? "
            f"Ask clearly and wait for their response. "
            f"If they say Enterprise, confirm: 'Just to confirm, the customer "
            f"{customer_name} is on the Enterprise plan with a 30-day onboarding "
            f"and a dedicated CSM, correct?' "
            f"If they say Growth, confirm: 'Just to confirm, the customer "
            f"{customer_name} is on the Growth plan with a 14-day onboarding "
            f"and a pooled CSM, correct?' "
            f"If the response is unclear, ask them to clarify. "
            f"Thank them and end the call."
        )

        payload = {
            "phone_number": ae_phone,
            "task": prompt,
            "voice": "maya",
            "first_sentence": (
                f"Hi {ae_name}, this is NovaCRM's onboarding assistant. "
                f"I'm calling about the new deal that just closed for {customer_name}. "
                f"Could you confirm whether they're on the Enterprise plan or the Growth plan?"
            ),
            "wait_for_greeting": True,
            "record": True,
            "max_duration": 2,
            "model": "enhanced",
            "language": "en",
            "answered_by_enabled": True,
        }

        log_action(
            agent_name="VoiceClient",
            action="initiate_call",
            inputs={
                "ae_name": ae_name,
                "ae_phone": ae_phone,
                "customer_name": customer_name,
            },
            outputs={},
            decision_rationale="Calling AE to confirm plan tier before project creation",
        )

        try:
            response = requests.post(
                f"{self.base_url}/calls",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            response.raise_for_status()
            call_data = response.json()
            call_id = call_data.get("call_id", "")

            result = self._poll_call_status(call_id)

            log_action(
                agent_name="VoiceClient",
                action="call_completed",
                inputs={"call_id": call_id},
                outputs=result.model_dump(),
                decision_rationale=f"Call finished with status: {result.status}",
            )
            return result

        except requests.RequestException as e:
            log_action(
                agent_name="VoiceClient",
                action="call_failed",
                inputs={"ae_phone": ae_phone},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="API call to Bland.ai failed",
            )
            return VoiceCallResult(
                call_id="",
                status="failed",
                transcript=str(e),
            )

    def _poll_call_status(
        self, call_id: str, max_wait: int = 180, interval: int = 5
    ) -> VoiceCallResult:
        elapsed = 0
        while elapsed < max_wait:
            try:
                resp = requests.get(
                    f"{self.base_url}/calls/{call_id}",
                    headers=self.headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("completed", False) or data.get("status") == "completed":
                    return self._parse_call_result(call_id, data)

                if data.get("status") in ("failed", "no-answer", "busy"):
                    return VoiceCallResult(
                        call_id=call_id,
                        status=data["status"],
                    )

            except requests.RequestException:
                pass

            time.sleep(interval)
            elapsed += interval

        return VoiceCallResult(call_id=call_id, status="timeout")

    def _parse_call_result(self, call_id: str, data: dict) -> VoiceCallResult:
        transcript = data.get("concatenated_transcript", "") or data.get("transcript", "")
        transcript_lower = transcript.lower()

        plan_tier = None
        confidence = 0.0

        enterprise_signals = ["enterprise", "30-day", "30 day", "dedicated csm"]
        growth_signals = ["growth", "14-day", "14 day", "pooled csm"]

        enterprise_hits = sum(1 for s in enterprise_signals if s in transcript_lower)
        growth_hits = sum(1 for s in growth_signals if s in transcript_lower)

        if enterprise_hits > 0 and growth_hits == 0:
            plan_tier = PlanTier.ENTERPRISE
            confidence = min(0.5 + enterprise_hits * 0.2, 1.0)
        elif growth_hits > 0 and enterprise_hits == 0:
            plan_tier = PlanTier.GROWTH
            confidence = min(0.5 + growth_hits * 0.2, 1.0)
        elif enterprise_hits > 0 and growth_hits > 0:
            confidence = 0.3  # ambiguous

        confirmed_phrases = ["yes", "correct", "that's right", "confirmed", "affirmative"]
        if any(p in transcript_lower for p in confirmed_phrases):
            confidence = min(confidence + 0.2, 1.0)

        return VoiceCallResult(
            call_id=call_id,
            status="completed",
            plan_tier=plan_tier,
            confidence=confidence,
            transcript=transcript,
            duration_seconds=data.get("call_length", 0.0),
        )
