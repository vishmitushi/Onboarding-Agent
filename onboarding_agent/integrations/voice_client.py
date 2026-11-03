from __future__ import annotations

import time

import requests

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action
from onboarding_agent.models import PlanTier, VoiceCallResult


class BlandVoiceClient:
    """Voice AI client for plan tier confirmation calls.

    Supports two modes:
      - "vapi_inbound"  → AE calls the Vapi number, AI asks for plan tier
      - "vapi_outbound" → System calls the AE via Vapi (requires Twilio number)
      - "bland"         → System calls AE via Bland.ai (US/Canada only)
    """

    def __init__(self) -> None:
        self.provider = settings.VOICE_PROVIDER
        if self.provider in ("vapi", "vapi_inbound", "vapi_outbound"):
            self.api_key = settings.VAPI_API_KEY
            self.base_url = "https://api.vapi.ai"
            self.headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        else:
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
        if self.provider in ("vapi_inbound",):
            return self._vapi_inbound(ae_phone, ae_name, customer_name)
        if self.provider in ("vapi", "vapi_outbound"):
            return self._vapi_outbound(ae_phone, ae_name, customer_name)
        return self._call_bland(ae_phone, ae_name, customer_name)

    def _build_system_prompt(self, ae_name: str, customer_name: str) -> str:
        return (
            f"You are an onboarding assistant at NovaCRM. "
            f"You are speaking with {ae_name}, an Account Executive. "
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

    # ── Vapi Inbound (AE calls our number) ─────────────────────────────

    def _vapi_inbound(self, ae_phone: str, ae_name: str, customer_name: str) -> VoiceCallResult:
        """Wait for AE to call our Vapi number, then poll for result."""
        prompt = self._build_system_prompt(ae_name, customer_name)
        vapi_number = settings.VAPI_PHONE_NUMBER

        # Update the phone number's assistant configuration
        try:
            assistant_config = {
                "firstMessage": (
                    f"Hi! This is NovaCRM's onboarding assistant. "
                    f"I understand you're calling about the new deal for {customer_name}. "
                    f"Could you confirm whether they're on the Enterprise plan or the Growth plan?"
                ),
                "model": {
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "messages": [{"role": "system", "content": prompt}],
                },
                "voice": {
                    "provider": "vapi",
                    "voiceId": "Elliot",
                },
            }

            resp = requests.patch(
                f"{self.base_url}/phone-number/{settings.VAPI_PHONE_ID}",
                json={"assistantId": None, "assistant": assistant_config},
                headers=self.headers,
                timeout=15,
            )

            if resp.status_code not in (200, 201):
                print(f"  Vapi setup error: {resp.text[:300]}")

            log_action(
                agent_name="VoiceClient",
                action="inbound_setup",
                inputs={"ae_name": ae_name, "customer_name": customer_name, "vapi_number": vapi_number},
                outputs={"status": resp.status_code, "response": resp.text[:200]},
                decision_rationale=f"Configured Vapi number for inbound call from AE",
            )

        except Exception as e:
            log_action(
                agent_name="VoiceClient",
                action="inbound_setup_failed",
                inputs={},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="Failed to configure Vapi assistant",
            )

        print(f"\n  ╔══════════════════════════════════════════════════════════╗")
        print(f"  ║  ACTION REQUIRED: Call the Vapi number now!              ║")
        print(f"  ║  Number: {vapi_number:<47s} ║")
        print(f"  ║  The AI will ask about the plan tier for {customer_name:<14s} ║")
        print(f"  ║  Say 'Enterprise' or 'Growth' when asked.               ║")
        print(f"  ╚══════════════════════════════════════════════════════════╝\n")

        # Poll for incoming calls on this phone number
        return self._poll_vapi_inbound(customer_name)

    def _poll_vapi_inbound(self, customer_name: str, max_wait: int = 180, interval: int = 5) -> VoiceCallResult:
        """Poll Vapi for completed calls on our phone number."""
        elapsed = 0
        seen_calls = set()

        # Get initial call list to know what's already there
        try:
            resp = requests.get(
                f"{self.base_url}/call",
                headers=self.headers,
                params={"limit": 5},
                timeout=15,
            )
            if resp.status_code == 200:
                for c in resp.json():
                    seen_calls.add(c.get("id", ""))
        except Exception:
            pass

        print(f"  Waiting for your call (timeout: {max_wait}s)...")

        while elapsed < max_wait:
            time.sleep(interval)
            elapsed += interval

            try:
                resp = requests.get(
                    f"{self.base_url}/call",
                    headers=self.headers,
                    params={"limit": 5},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue

                calls = resp.json()
                for call in calls:
                    call_id = call.get("id", "")
                    if call_id in seen_calls:
                        continue

                    status = call.get("status", "")
                    if status == "ended":
                        print(f"  Call detected! Processing transcript...")
                        return self._parse_call_result(call_id, call)
                    elif status in ("ringing", "in-progress", "queued"):
                        print(f"  Call in progress... waiting for it to finish.")

            except Exception:
                pass

            remaining = max_wait - elapsed
            if remaining > 0 and elapsed % 15 == 0:
                print(f"  Still waiting... ({remaining}s remaining)")

        return VoiceCallResult(call_id="", status="timeout",
                               transcript="No call received within timeout period")

    # ── Vapi Outbound (system calls AE) ────────────────────────────────

    def _vapi_outbound(self, ae_phone: str, ae_name: str, customer_name: str) -> VoiceCallResult:
        prompt = self._build_system_prompt(ae_name, customer_name)

        payload = {
            "assistant": {
                "firstMessage": (
                    f"Hi {ae_name}, this is NovaCRM's onboarding assistant. "
                    f"I'm calling about the new deal that just closed for {customer_name}. "
                    f"Could you confirm whether they're on the Enterprise plan or the Growth plan?"
                ),
                "model": {
                    "provider": "openai",
                    "model": "gpt-4.1",
                    "messages": [{"role": "system", "content": prompt}],
                },
                "voice": {
                    "provider": "vapi",
                    "voiceId": "Elliot",
                },
            },
            "phoneNumberId": settings.VAPI_PHONE_ID,
            "customer": {
                "number": ae_phone,
            },
        }

        log_action(
            agent_name="VoiceClient",
            action="initiate_call",
            inputs={"ae_name": ae_name, "ae_phone": ae_phone, "provider": "vapi_outbound"},
            outputs={},
            decision_rationale="Calling AE via Vapi.ai to confirm plan tier",
        )

        try:
            response = requests.post(
                f"{self.base_url}/call/phone",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            if response.status_code not in (200, 201):
                error_detail = response.text[:500]
                print(f"  Vapi.ai error: {error_detail}")
                response.raise_for_status()

            call_data = response.json()
            call_id = call_data.get("id", "")

            return self._poll_vapi_outbound(call_id)

        except requests.RequestException as e:
            log_action(
                agent_name="VoiceClient",
                action="call_failed",
                inputs={"ae_phone": ae_phone},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="Vapi.ai API call failed",
            )
            return VoiceCallResult(call_id="", status="failed", transcript=str(e))

    def _poll_vapi_outbound(self, call_id: str, max_wait: int = 180, interval: int = 5) -> VoiceCallResult:
        elapsed = 0
        while elapsed < max_wait:
            try:
                resp = requests.get(
                    f"{self.base_url}/call/{call_id}",
                    headers=self.headers,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status", "")
                if status == "ended":
                    return self._parse_call_result(call_id, data)
                if status == "failed":
                    return VoiceCallResult(call_id=call_id, status="failed")
            except Exception:
                pass

            time.sleep(interval)
            elapsed += interval

        return VoiceCallResult(call_id=call_id, status="timeout")

    # ── Bland.ai (fallback) ────────────────────────────────────────────

    def _call_bland(self, ae_phone: str, ae_name: str, customer_name: str) -> VoiceCallResult:
        prompt = self._build_system_prompt(ae_name, customer_name)

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
            "language": "en",
        }

        log_action(
            agent_name="VoiceClient",
            action="initiate_call",
            inputs={"ae_name": ae_name, "ae_phone": ae_phone, "provider": "bland"},
            outputs={},
            decision_rationale="Calling AE via Bland.ai to confirm plan tier",
        )

        try:
            response = requests.post(
                f"{self.base_url}/calls",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            if response.status_code != 200:
                print(f"  Bland.ai error: {response.text[:500]}")
            response.raise_for_status()

            call_data = response.json()
            call_id = call_data.get("call_id", "")
            return self._poll_bland(call_id)

        except requests.RequestException as e:
            log_action(
                agent_name="VoiceClient",
                action="call_failed",
                inputs={"ae_phone": ae_phone},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="Bland.ai API call failed",
            )
            return VoiceCallResult(call_id="", status="failed", transcript=str(e))

    def _poll_bland(self, call_id: str, max_wait: int = 180, interval: int = 5) -> VoiceCallResult:
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
                    return VoiceCallResult(call_id=call_id, status=data["status"])
            except Exception:
                pass

            time.sleep(interval)
            elapsed += interval

        return VoiceCallResult(call_id=call_id, status="timeout")

    # ── Shared transcript parsing ──────────────────────────────────────

    def _parse_call_result(self, call_id: str, data: dict) -> VoiceCallResult:
        transcript = (
            data.get("concatenated_transcript", "")
            or data.get("transcript", "")
            or data.get("artifact", {}).get("transcript", "")
            or ""
        )
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
            confidence = 0.3

        confirmed_phrases = ["yes", "correct", "that's right", "confirmed", "affirmative"]
        if any(p in transcript_lower for p in confirmed_phrases):
            confidence = min(confidence + 0.2, 1.0)

        duration = data.get("call_length", 0.0) or data.get("duration", 0.0) or 0.0

        log_action(
            agent_name="VoiceClient",
            action="call_completed",
            inputs={"call_id": call_id},
            outputs={
                "plan_tier": plan_tier.value if plan_tier else None,
                "confidence": confidence,
                "transcript_length": len(transcript),
            },
            decision_rationale=f"Call completed — plan: {plan_tier}, confidence: {confidence:.0%}",
        )

        return VoiceCallResult(
            call_id=call_id,
            status="completed",
            plan_tier=plan_tier,
            confidence=confidence,
            transcript=transcript,
            duration_seconds=duration,
        )
