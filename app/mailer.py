"""Invitation email over plain SMTP — stdlib only, env-configured.

No provider SDK, no queue: Lumnia sends a handful of sign-in links, not
newsletters. Configuration is four env vars (SMTP_HOST, SMTP_PORT,
SMTP_USER, SMTP_PASSWORD, optional SMTP_FROM); with no SMTP_HOST the
feature is OFF and callers must fail honestly (the copy-link path in the
workbench always works without it).
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict, Optional


def smtp_config() -> Optional[Dict[str, Any]]:
    """The SMTP settings, or None when the feature is unconfigured."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    user = os.environ.get("SMTP_USER") or None
    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "password": os.environ.get("SMTP_PASSWORD") or None,
        "sender": os.environ.get("SMTP_FROM") or user or f"lumnia@{host}",
    }


def _deliver(msg: EmailMessage, cfg: Dict[str, Any]) -> None:
    """The socket seam — tests monkeypatch THIS; nothing above it fakes."""
    ctx = ssl.create_default_context()
    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20,
                              context=ctx) as s:
            if cfg["user"]:
                s.login(cfg["user"], cfg["password"] or "")
            s.send_message(msg)
        return
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as s:
        s.starttls(context=ctx)
        if cfg["user"]:
            s.login(cfg["user"], cfg["password"] or "")
        s.send_message(msg)


def send_invite(to: str, subject: str, body: str) -> None:
    """Send one plain-text message. Raises RuntimeError when unconfigured;
    SMTP errors propagate — the caller reports failure, never pretends."""
    cfg = smtp_config()
    if cfg is None:
        raise RuntimeError("SMTP is not configured.")
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    _deliver(msg, cfg)
