from __future__ import annotations

import email
import imaplib
from email.header import decode_header
from typing import Any

from onboarding_agent.config import settings
from onboarding_agent.guardrails import log_action


class GmailClient:
    """Monitors a Gmail inbox for deal notification emails via IMAP.

    Setup (one-time):
      1. Enable 2-Step Verification on your Google account
      2. Go to https://myaccount.google.com/apppasswords
      3. Generate an App Password for "Mail"
      4. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env
    """

    def __init__(self) -> None:
        self._mail: imaplib.IMAP4_SSL | None = None

    def _connect(self) -> imaplib.IMAP4_SSL:
        if self._mail is None:
            self._mail = imaplib.IMAP4_SSL("imap.gmail.com")
            self._mail.login(settings.GMAIL_ADDRESS, settings.GMAIL_APP_PASSWORD)
        return self._mail

    def fetch_unread_deal_emails(self, label: str = "INBOX") -> list[dict[str, Any]]:
        """Fetch unread emails that look like deal notifications."""
        mail = self._connect()
        mail.select(label)

        search_criteria = '(UNSEEN SUBJECT "new deal" OR UNSEEN SUBJECT "closed won" OR UNSEEN SUBJECT "new customer")'
        _, message_ids = mail.search(None, search_criteria)

        parsed: list[dict[str, Any]] = []

        for msg_id in message_ids[0].split():
            if not msg_id:
                continue
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue

            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            parsed.append(self._extract_email_data(msg, msg_id.decode()))

        log_action(
            agent_name="GmailClient",
            action="fetch_unread_deal_emails",
            inputs={"label": label},
            outputs={"count": len(parsed)},
            decision_rationale="Polling inbox for new deal notifications via IMAP",
        )
        return parsed

    def _extract_email_data(self, msg: email.message.Message, msg_id: str) -> dict[str, Any]:
        subject = self._decode_header(msg.get("Subject", ""))
        from_addr = self._decode_header(msg.get("From", ""))
        date = msg.get("Date", "")
        body = self._get_body(msg)

        return {
            "message_id": msg_id,
            "from": from_addr,
            "subject": subject,
            "date": date,
            "body": body,
        }

    def _decode_header(self, header: str) -> str:
        decoded_parts = decode_header(header)
        parts = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                parts.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(part)
        return " ".join(parts)

    def _get_body(self, msg: email.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="replace")
        return ""

    def mark_as_read(self, message_id: str) -> None:
        mail = self._connect()
        mail.store(message_id, "+FLAGS", "\\Seen")

        log_action(
            agent_name="GmailClient",
            action="mark_as_read",
            inputs={"message_id": message_id},
            outputs={},
            decision_rationale="Marking processed email as read to avoid reprocessing",
        )

    def disconnect(self) -> None:
        if self._mail:
            try:
                self._mail.logout()
            except Exception:
                pass
            self._mail = None
