from __future__ import annotations

import http.server
import os
import threading
import time
import webbrowser

import requests

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action
from onboarding_agent.models import PlanTier, VoiceCallResult


class BlandVoiceClient:
    """Voice AI client for plan tier confirmation calls.

    Supports multiple modes:
      - "vapi_web"      → AE talks via browser (no phone needed, works internationally)
      - "vapi_inbound"  → AE calls the Vapi number, AI asks for plan tier
      - "vapi_outbound" → System calls the AE via Vapi (requires Twilio number)
      - "bland"         → System calls AE via Bland.ai (US/Canada only)
    """

    def __init__(self) -> None:
        self.provider = settings.VOICE_PROVIDER
        if self.provider in ("vapi", "vapi_web", "vapi_inbound", "vapi_outbound"):
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
        if self.provider in ("vapi", "vapi_web"):
            return self._vapi_web(ae_phone, ae_name, customer_name)
        if self.provider in ("vapi_inbound",):
            return self._vapi_inbound(ae_phone, ae_name, customer_name)
        if self.provider in ("vapi_outbound",):
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

    # ── Vapi Web Call (browser-based, no phone needed) ──────────────────

    def _vapi_web(self, ae_phone: str, ae_name: str, customer_name: str) -> VoiceCallResult:
        """Create assistant, open browser page for web-based voice call, poll for result."""
        prompt = self._build_system_prompt(ae_name, customer_name)

        # Step 1: Create a dedicated assistant
        assistant_payload = {
            "name": f"NovaCRM Onboarding - {customer_name}",
            "firstMessage": (
                f"Hi! This is NovaCRM's onboarding assistant. "
                f"I understand we're discussing the new deal for {customer_name}. "
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

        try:
            resp = requests.post(
                f"{self.base_url}/assistant",
                json=assistant_payload,
                headers=self.headers,
                timeout=15,
            )
            if resp.status_code not in (200, 201):
                print(f"  Vapi assistant creation error: {resp.text[:300]}")
                resp.raise_for_status()

            assistant_data = resp.json()
            assistant_id = assistant_data.get("id", "")
            print(f"  Created Vapi assistant: {assistant_id}")

        except Exception as e:
            log_action(
                agent_name="VoiceClient",
                action="web_call_setup_failed",
                inputs={},
                outputs={},
                success=False,
                error_message=str(e),
                decision_rationale="Failed to create Vapi assistant for web call",
            )
            return VoiceCallResult(call_id="", status="failed", transcript=str(e))

        # Step 2: Generate and open the web call HTML page
        public_key = settings.VAPI_PUBLIC_KEY
        if not public_key:
            print("  ERROR: VAPI_PUBLIC_KEY not set in .env")
            print("  Get it from: https://dashboard.vapi.ai → Account → Public Key")
            return VoiceCallResult(call_id="", status="failed",
                                   transcript="VAPI_PUBLIC_KEY not configured")

        html_path = self._generate_web_call_page(public_key, assistant_id, customer_name)

        # Serve via localhost (browsers block microphone on file:// URLs)
        web_dir = os.path.dirname(html_path)
        port = self._start_local_server(web_dir)

        print(f"\n  ╔══════════════════════════════════════════════════════════╗")
        print(f"  ║  ACTION REQUIRED: Web call page opened in browser!      ║")
        print(f"  ║  Click the GREEN phone button (bottom-right corner).    ║")
        print(f"  ║  Allow microphone access when prompted.                 ║")
        print(f"  ║  Say 'Enterprise' or 'Growth' when asked.              ║")
        print(f"  ║  Click the RED button to end the call.                  ║")
        print(f"  ╚══════════════════════════════════════════════════════════╝\n")

        webbrowser.open(f"http://localhost:{port}/vapi_call.html")

        log_action(
            agent_name="VoiceClient",
            action="web_call_started",
            inputs={"ae_name": ae_name, "customer_name": customer_name},
            outputs={"assistant_id": assistant_id},
            decision_rationale="Opened browser for web-based voice call",
        )

        # Step 3: Poll for the call result
        return self._poll_vapi_web_call(assistant_id, customer_name)

    def _generate_web_call_page(self, public_key: str, assistant_id: str, customer_name: str) -> str:
        """Generate a local HTML page styled like a phone dialing screen."""
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NovaCRM — Calling AE</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: linear-gradient(135deg, #0c0c1d 0%, #1a1a3e 50%, #0c0c1d 100%);
         color: #fff; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; }}

  .phone-screen {{ width: 380px; text-align: center; padding: 40px 20px; }}

  /* Avatar ring */
  .avatar-ring {{ width: 120px; height: 120px; border-radius: 50%;
                  margin: 0 auto 24px; position: relative; }}
  .avatar-ring::before {{ content: ''; position: absolute; inset: -4px;
                          border-radius: 50%; border: 3px solid rgba(34,197,94,0.4);
                          animation: ring-pulse 2s ease-out infinite; }}
  .avatar {{ width: 120px; height: 120px; border-radius: 50%;
             background: linear-gradient(135deg, #22c55e, #16a34a);
             display: flex; align-items: center; justify-content: center;
             font-size: 3rem; color: white; }}
  .ringing .avatar-ring::before {{ border-color: rgba(34,197,94,0.6); }}
  .connected .avatar {{ background: linear-gradient(135deg, #3b82f6, #1d4ed8); }}
  .connected .avatar-ring::before {{ border-color: rgba(59,130,246,0.4); }}
  .ended .avatar {{ background: linear-gradient(135deg, #6b7280, #374151); }}
  .ended .avatar-ring::before {{ animation: none; border-color: transparent; }}

  @keyframes ring-pulse {{
    0% {{ transform: scale(1); opacity: 1; }}
    100% {{ transform: scale(1.3); opacity: 0; }}
  }}

  .caller-name {{ font-size: 1.6rem; font-weight: 600; margin-bottom: 4px; }}
  .caller-label {{ font-size: 0.9rem; color: rgba(255,255,255,0.5); margin-bottom: 8px; }}

  #callStatus {{ font-size: 1rem; color: rgba(255,255,255,0.7); margin-bottom: 32px;
                 min-height: 24px; }}
  #callStatus.active {{ color: #4ade80; }}

  #timer {{ font-size: 2.2rem; font-weight: 300; font-variant-numeric: tabular-nums;
            letter-spacing: 2px; margin-bottom: 40px; min-height: 40px;
            color: rgba(255,255,255,0.9); }}

  .btn-row {{ display: flex; justify-content: center; gap: 32px; margin-bottom: 24px; }}

  .call-btn {{ width: 72px; height: 72px; border-radius: 50%; border: none;
               cursor: pointer; display: flex; align-items: center; justify-content: center;
               transition: transform 0.15s, box-shadow 0.15s; }}
  .call-btn:hover {{ transform: scale(1.08); }}
  .call-btn:active {{ transform: scale(0.95); }}
  .call-btn svg {{ width: 32px; height: 32px; fill: white; }}

  #answerBtn {{ background: linear-gradient(135deg, #22c55e, #16a34a);
                box-shadow: 0 0 30px rgba(34,197,94,0.4); }}
  #hangupBtn {{ background: linear-gradient(135deg, #ef4444, #dc2626);
                box-shadow: 0 0 30px rgba(239,68,68,0.3); display: none; }}

  .hint {{ font-size: 0.8rem; color: rgba(255,255,255,0.35); margin-top: 16px; }}
  .hint strong {{ color: rgba(255,255,255,0.55); }}

  /* Dots animation for "Dialing..." */
  .dots::after {{ content: ''; animation: dots 1.5s steps(4, end) infinite; }}
  @keyframes dots {{
    0% {{ content: ''; }} 25% {{ content: '.'; }} 50% {{ content: '..'; }} 75% {{ content: '...'; }}
  }}
</style>
</head>
<body>
<div class="phone-screen" id="phoneScreen">
  <div class="avatar-ring">
    <div class="avatar">
      <svg viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="1.5" width="56" height="56">
        <path d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.5 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75a17.933 17.933 0 01-7.5-1.632z"/>
      </svg>
    </div>
  </div>

  <div class="caller-name">{customer_name}</div>
  <div class="caller-label">NovaCRM Onboarding · Plan Confirmation</div>

  <div id="callStatus" class="dots">Initializing</div>
  <div id="timer"></div>

  <div class="btn-row">
    <button class="call-btn" id="answerBtn" onclick="answerCall()" title="Answer">
      <svg viewBox="0 0 24 24"><path d="M6.62 10.79a15.053 15.053 0 006.59 6.59l2.2-2.2a1 1 0 01 1.02-.24c1.12.37 2.33.57 3.57.57a1 1 0 011 1V20a1 1 0 01-1 1A17 17 0 013 4a1 1 0 011-1h3.5a1 1 0 011 1c0 1.25.2 2.45.57 3.57a1 1 0 01-.25 1.02l-2.2 2.2z"/></svg>
    </button>
    <button class="call-btn" id="hangupBtn" onclick="hangupCall()" title="Hang up">
      <svg viewBox="0 0 24 24"><path d="M3.68 16.07l3.92-3.11V9.59c2.87-1.04 5.97-1.04 8.84 0v3.37l3.91 3.11c.56.45.19 1.38-.53 1.38H4.21c-.72 0-1.09-.93-.53-1.38z"/></svg>
    </button>
  </div>

  <div class="hint">
    Say <strong>"Enterprise"</strong> or <strong>"Growth"</strong> when asked about the plan tier
  </div>
</div>

<script>
  const ASSISTANT_ID = '{assistant_id}';
  const PUBLIC_KEY = '{public_key}';

  let vapiInstance = null;
  let timerInterval = null;
  let callSeconds = 0;

  const phoneScreen = document.getElementById('phoneScreen');
  const statusEl = document.getElementById('callStatus');
  const timerEl = document.getElementById('timer');
  const answerBtn = document.getElementById('answerBtn');
  const hangupBtn = document.getElementById('hangupBtn');

  // Load SDK on page load
  (function (d, t) {{
    var g = d.createElement(t), s = d.getElementsByTagName(t)[0];
    g.src = 'https://cdn.jsdelivr.net/gh/VapiAI/html-script-tag@latest/dist/assets/index.js';
    g.defer = true;
    g.async = true;
    s.parentNode.insertBefore(g, s);
    g.onload = function () {{
      // Hide the default Vapi widget button
      var style = d.createElement('style');
      style.textContent = '#vapi-support-btn, [id^="vapi"] {{ display:none!important; }}';
      d.head.appendChild(style);

      statusEl.textContent = 'Dialing';
      statusEl.className = 'dots';
      phoneScreen.className = 'phone-screen ringing';

      // SDK is ready — show ringing state, user clicks Answer
      setTimeout(function() {{
        statusEl.textContent = 'Incoming call — tap Answer';
        statusEl.className = '';
      }}, 2000);
    }};
    g.onerror = function() {{
      statusEl.textContent = 'Failed to load voice SDK';
      statusEl.className = '';
    }};
  }})(document, 'script');

  function formatTime(sec) {{
    var m = Math.floor(sec / 60).toString().padStart(2, '0');
    var s = (sec % 60).toString().padStart(2, '0');
    return m + ':' + s;
  }}

  function startTimer() {{
    callSeconds = 0;
    timerEl.textContent = '00:00';
    timerInterval = setInterval(function() {{
      callSeconds++;
      timerEl.textContent = formatTime(callSeconds);
    }}, 1000);
  }}

  function stopTimer() {{
    if (timerInterval) clearInterval(timerInterval);
  }}

  function answerCall() {{
    statusEl.textContent = 'Connecting';
    statusEl.className = 'dots';
    answerBtn.style.display = 'none';

    // Use vapiSDK internal to start call
    try {{
      window.vapiSDK.run({{
        apiKey: PUBLIC_KEY,
        assistant: ASSISTANT_ID,
        config: {{ position: 'bottom-right', offset: '-9999px', width: '1px', height: '1px' }}
      }});

      // Click the hidden Vapi button to start the call
      setTimeout(function() {{
        var vapiBtn = document.querySelector('#vapi-support-btn, [id^="vapi"] button, [class*="vapi"] button');
        if (vapiBtn) {{
          vapiBtn.click();
        }} else {{
          // Fallback: look harder
          var allBtns = document.querySelectorAll('button');
          for (var i = allBtns.length - 1; i >= 0; i--) {{
            if (allBtns[i] !== answerBtn && allBtns[i] !== hangupBtn) {{
              allBtns[i].click();
              break;
            }}
          }}
        }}
        phoneScreen.className = 'phone-screen connected';
        statusEl.textContent = 'Connected';
        statusEl.className = 'active';
        hangupBtn.style.display = 'flex';
        startTimer();
      }}, 1500);

    }} catch (err) {{
      statusEl.textContent = 'Error: ' + err.message;
      statusEl.className = '';
      answerBtn.style.display = 'flex';
    }}
  }}

  function hangupCall() {{
    // Click the Vapi button again to end
    var vapiBtn = document.querySelector('#vapi-support-btn, [id^="vapi"] button, [class*="vapi"] button');
    if (vapiBtn) vapiBtn.click();
    else {{
      var allBtns = document.querySelectorAll('button');
      for (var i = allBtns.length - 1; i >= 0; i--) {{
        if (allBtns[i] !== answerBtn && allBtns[i] !== hangupBtn) {{
          allBtns[i].click();
          break;
        }}
      }}
    }}

    stopTimer();
    phoneScreen.className = 'phone-screen ended';
    statusEl.textContent = 'Call Ended · ' + formatTime(callSeconds);
    statusEl.className = '';
    hangupBtn.style.display = 'none';
  }}
</script>
</body>
</html>"""

        page_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web")
        os.makedirs(page_dir, exist_ok=True)
        page_path = os.path.join(page_dir, "vapi_call.html")
        with open(page_path, "w", encoding="utf-8") as f:
            f.write(html)
        return os.path.abspath(page_path)

    def _poll_vapi_web_call(self, assistant_id: str, customer_name: str,
                            max_wait: int = 180, interval: int = 5) -> VoiceCallResult:
        """Poll Vapi for a completed web call made to our assistant."""
        seen_calls: set[str] = set()

        # Snapshot existing calls
        try:
            resp = requests.get(
                f"{self.base_url}/call",
                headers=self.headers,
                params={"limit": 10, "assistantId": assistant_id},
                timeout=15,
            )
            if resp.status_code == 200:
                for c in resp.json():
                    seen_calls.add(c.get("id", ""))
        except Exception:
            pass

        print(f"  Waiting for your web call (timeout: {max_wait}s)...")
        elapsed = 0

        while elapsed < max_wait:
            time.sleep(interval)
            elapsed += interval

            try:
                resp = requests.get(
                    f"{self.base_url}/call",
                    headers=self.headers,
                    params={"limit": 10, "assistantId": assistant_id},
                    timeout=15,
                )
                if resp.status_code != 200:
                    continue

                for call in resp.json():
                    call_id = call.get("id", "")
                    if call_id in seen_calls:
                        continue

                    status = call.get("status", "")
                    if status == "ended":
                        print(f"  Web call completed! Processing transcript...")
                        return self._parse_call_result(call_id, call)
                    elif status in ("ringing", "in-progress", "queued"):
                        print(f"  Call in progress... waiting for it to finish.")
            except Exception:
                pass

            remaining = max_wait - elapsed
            if remaining > 0 and elapsed % 15 == 0:
                print(f"  Still waiting... ({remaining}s remaining)")

        return VoiceCallResult(call_id="", status="timeout",
                               transcript="No web call completed within timeout period")

    def _start_local_server(self, directory: str) -> int:
        """Start a background HTTP server on localhost to serve the web call page."""
        handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
            *args, directory=directory, **kwargs
        )
        for port in range(8765, 8780):
            try:
                server = http.server.HTTPServer(("127.0.0.1", port), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                return port
            except OSError:
                continue
        raise RuntimeError("Could not find an open port for local server")

    # ── Vapi Inbound (AE calls our number) ─────────────────────────────

    def _vapi_inbound(self, ae_phone: str, ae_name: str, customer_name: str) -> VoiceCallResult:
        """Wait for AE to call our Vapi number, then poll for result."""
        prompt = self._build_system_prompt(ae_name, customer_name)
        vapi_number = settings.VAPI_PHONE_NUMBER

        # Step 1: Create a dedicated assistant via POST /assistant
        # Step 2: Assign it to the phone number via PATCH /phone-number/{id}
        try:
            assistant_payload = {
                "name": f"NovaCRM Onboarding - {customer_name}",
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

            create_resp = requests.post(
                f"{self.base_url}/assistant",
                json=assistant_payload,
                headers=self.headers,
                timeout=15,
            )

            if create_resp.status_code not in (200, 201):
                print(f"  Vapi assistant creation error: {create_resp.text[:300]}")
                create_resp.raise_for_status()

            assistant_id = create_resp.json().get("id", "")
            print(f"  Created Vapi assistant: {assistant_id}")

            resp = requests.patch(
                f"{self.base_url}/phone-number/{settings.VAPI_PHONE_ID}",
                json={"assistantId": assistant_id},
                headers=self.headers,
                timeout=15,
            )

            if resp.status_code not in (200, 201):
                print(f"  Vapi phone assignment error: {resp.text[:300]}")

            log_action(
                agent_name="VoiceClient",
                action="inbound_setup",
                inputs={"ae_name": ae_name, "customer_name": customer_name, "vapi_number": vapi_number},
                outputs={"assistant_id": assistant_id, "status": resp.status_code},
                decision_rationale=f"Created assistant {assistant_id} and assigned to phone number",
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
