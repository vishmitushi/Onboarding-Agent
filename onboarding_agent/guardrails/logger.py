from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from onboarding_agent.config import settings
from onboarding_agent.models import AgentAction


def _setup_file_logger() -> logging.Logger:
    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("onboarding_agent")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    if not logger.handlers:
        fh = logging.FileHandler(log_dir / "agent_actions.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(ch)

    return logger


_logger = _setup_file_logger()

_audit_trail: list[AgentAction] = []


def log_action(
    agent_name: str,
    action: str,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    decision_rationale: str = "",
    success: bool = True,
    error_message: str = "",
) -> AgentAction:
    entry = AgentAction(
        timestamp=datetime.now(timezone.utc),
        agent_name=agent_name,
        action=action,
        inputs=inputs or {},
        outputs=outputs or {},
        decision_rationale=decision_rationale,
        success=success,
        error_message=error_message,
    )
    _audit_trail.append(entry)

    log_line = (
        f"[{entry.agent_name}] {entry.action} | "
        f"success={entry.success} | "
        f"rationale={entry.decision_rationale}"
    )
    if success:
        _logger.info(log_line)
    else:
        _logger.error(f"{log_line} | error={entry.error_message}")

    return entry


def get_audit_trail() -> list[AgentAction]:
    return list(_audit_trail)


def export_audit_trail(filepath: str | None = None) -> str:
    if filepath is None:
        filepath = os.path.join(settings.LOG_DIR, "audit_trail.json")
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)

    data = [entry.model_dump(mode="json") for entry in _audit_trail]
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return filepath


def clear_audit_trail() -> None:
    _audit_trail.clear()
