from __future__ import annotations

import base64
import os
import re
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action


class GmailClient:
    SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

    def __init__(self) -> None:
        self._service = None

    def _authenticate(self) -> None:
        creds = None
        if os.path.exists(settings.GMAIL_TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(
                settings.GMAIL_TOKEN_FILE, self.SCOPES
            )
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    settings.GMAIL_CREDENTIALS_FILE, self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(settings.GMAIL_TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
        self._service = build("gmail", "v1", credentials=creds)

    @property
    def service(self):
        if self._service is None:
            self._authenticate()
        return self._service

    def fetch_unread_deal_emails(self, label: str = "INBOX") -> list[dict[str, Any]]:
        """Fetch unread emails that look like deal notifications."""
        query = "is:unread subject:(new deal OR closed won OR new customer)"
        results = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, labelIds=[label], maxResults=10)
            .execute()
        )
        messages = results.get("messages", [])
        parsed: list[dict[str, Any]] = []

        for msg_meta in messages:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=msg_meta["id"], format="full")
                .execute()
            )
            parsed.append(self._extract_email_data(msg))

        log_action(
            agent_name="GmailClient",
            action="fetch_unread_deal_emails",
            inputs={"query": query, "label": label},
            outputs={"count": len(parsed)},
            decision_rationale="Polling inbox for new deal notifications",
        )
        return parsed

    def _extract_email_data(self, message: dict) -> dict[str, Any]:
        headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
        body = self._get_body(message["payload"])
        return {
            "message_id": message["id"],
            "thread_id": message["threadId"],
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body": body,
        }

    def _get_body(self, payload: dict) -> str:
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")
        for part in payload.get("parts", []):
            if part["mimeType"] == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
        return ""

    def mark_as_read(self, message_id: str) -> None:
        self.service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()

        log_action(
            agent_name="GmailClient",
            action="mark_as_read",
            inputs={"message_id": message_id},
            outputs={},
            decision_rationale="Marking processed email as read to avoid reprocessing",
        )
