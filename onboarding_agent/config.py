from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Gmail
    GMAIL_CREDENTIALS_FILE: str = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
    GMAIL_TOKEN_FILE: str = os.getenv("GMAIL_TOKEN_FILE", "token.json")
    GMAIL_SCOPES: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]
    GMAIL_POLL_INTERVAL_SECONDS: int = int(os.getenv("GMAIL_POLL_INTERVAL_SECONDS", "30"))
    GMAIL_LABEL: str = os.getenv("GMAIL_LABEL", "INBOX")

    # Voice AI (Bland.ai)
    BLAND_API_KEY: str = os.getenv("BLAND_API_KEY", "")
    BLAND_API_URL: str = "https://api.bland.ai/v1"
    VOICE_CALL_MAX_RETRIES: int = int(os.getenv("VOICE_CALL_MAX_RETRIES", "2"))
    VOICE_CALL_TIMEOUT_SECONDS: int = int(os.getenv("VOICE_CALL_TIMEOUT_SECONDS", "120"))

    # Rocketlane
    ROCKETLANE_API_KEY: str = os.getenv("ROCKETLANE_API_KEY", "")
    ROCKETLANE_API_URL: str = os.getenv(
        "ROCKETLANE_API_URL", "https://api.rocketlane.com/api/1.0"
    )
    ROCKETLANE_VERIFY_SSL: bool = os.getenv("ROCKETLANE_VERIFY_SSL", "true").lower() != "false"

    # Slack
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")

    # LLM Provider — "groq" (free, recommended) or "anthropic"
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")

    # Groq (free tier — uses Llama 3.3 70B)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Anthropic (Claude) — alternative
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Logging
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Escalation
    ESCALATION_EMAIL: str = os.getenv("ESCALATION_EMAIL", "cs-manager@novacrm.com")


settings = Settings()
