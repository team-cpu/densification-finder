import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

import email_delivery as mail


def test_environment_reuses_normiq_names_without_exposing_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-secret")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "scope@example.com")
    monkeypatch.setenv("RESEND_FROM_NAME", "Scope")
    config = mail.ResendConfig.from_environment()
    assert config.sender == '"Scope" <scope@example.com>'
    assert "test-secret" not in repr(config)


def test_missing_sender_fails_closed(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "test-secret")
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    monkeypatch.delenv("EMAIL_FROM", raising=False)
    with pytest.raises(mail.EmailDeliveryError):
        mail.ResendConfig.from_environment()


def _send(**overrides):
    values = dict(recipient="user@example.com", subject="Scope Einladung", text="Hallo",
                  idempotency_key="invite/123", config=mail.ResendConfig("test-secret", "scope@example.com"))
    return mail.send_email(**(values | overrides))


def test_send_has_fixed_endpoint_and_idempotency(monkeypatch):
    opener = Mock()
    opener.open.return_value = io.BytesIO(b'{"id":"message-123"}')
    monkeypatch.setattr(mail, "build_opener", lambda handler: opener)
    assert _send() == "message-123"
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.resend.com/emails"
    assert request.get_header("Idempotency-key") == "invite/123"
    assert json.loads(request.data)["to"] == ["user@example.com"]
    assert opener.open.call_args.kwargs["timeout"] == 15


def test_provider_error_is_redacted_and_not_retried(monkeypatch):
    opener = Mock()
    opener.open.side_effect = HTTPError("https://api.resend.com/emails", 429, "test-secret", {}, None)
    monkeypatch.setattr(mail, "build_opener", lambda handler: opener)
    with pytest.raises(mail.EmailDeliveryError) as caught:
        _send()
    assert "test-secret" not in str(caught.value)
    assert opener.open.call_count == 1


@pytest.mark.parametrize("overrides", [
    {"recipient": "a@example.com\r\nBcc:x@example.com"},
    {"subject": "Hello\nBcc:x@example.com"},
    {"idempotency_key": ""}, {"text": " "},
])
def test_invalid_input_never_reaches_network(monkeypatch, overrides):
    opener = Mock()
    monkeypatch.setattr(mail, "build_opener", opener)
    with pytest.raises(ValueError):
        _send(**overrides)
    opener.assert_not_called()


def test_redirects_cannot_forward_credentials():
    assert mail._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example") is None
