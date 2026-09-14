"""Resend transport. Callers own recipient authorization and durable retry state."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class EmailDeliveryError(RuntimeError):
    """Safe to display; never includes provider bodies or credentials."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ResendConfig:
    api_key: str = field(repr=False)
    sender: str

    @classmethod
    def from_environment(cls) -> ResendConfig:
        key = os.environ.get("RESEND_API_KEY", "").strip()
        sender = (os.environ.get("RESEND_FROM_EMAIL") or os.environ.get("EMAIL_FROM") or "").strip()
        name = os.environ.get("RESEND_FROM_NAME", "").strip()
        if not key or not sender:
            raise EmailDeliveryError("E-Mail-Versand ist noch nicht konfiguriert.")
        if any(char in key + sender + name for char in "\r\n"):
            raise EmailDeliveryError("Ungültige E-Mail-Konfiguration.")
        if name:
            if any(char in name for char in '<>"'):
                raise EmailDeliveryError("Ungültiger Absendername.")
            sender = f'"{name}" <{sender}>'
        return cls(key, sender)


def send_email(
    *, recipient: str, subject: str, text: str, idempotency_key: str,
    config: ResendConfig | None = None, html: str | None = None,
) -> str:
    """Send once and return provider id, never equating acceptance with delivery.

    Persist the same key AND payload before calling. On ambiguous failures, retry
    that exact request within Resend's 24-hour deduplication window; reconcile
    older requests manually. This transport intentionally does not auto-retry.
    """
    recipient = recipient.strip()
    if len(recipient) > 254 or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", recipient):
        raise ValueError("Ungültige Empfängeradresse.")
    if not subject.strip() or any(c in subject for c in "\r\n") or not text.strip():
        raise ValueError("Betreff und Nachricht sind erforderlich.")
    if not re.fullmatch(r"[A-Za-z0-9_./:-]{1,256}", idempotency_key):
        raise ValueError("Ungültiger Versand-Schlüssel.")
    config = config or ResendConfig.from_environment()
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": config.sender, "to": [recipient], "subject": subject, "text": text,
                         **({"html": html} if html is not None else {})}).encode(),
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
                 "Idempotency-Key": idempotency_key, "User-Agent": "Scope/1.0"},
        method="POST",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            result = json.loads(response.read(65536))
        provider_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError("Missing message id")
        return provider_id
    except (HTTPError, URLError, OSError, ValueError):
        raise EmailDeliveryError(
            "E-Mail-Versand konnte nicht bestätigt werden. Nicht erneut mit einem neuen Versand-Schlüssel senden."
        ) from None
