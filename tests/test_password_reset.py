import logging

import pytest

from app import create_app
from config import Settings
from mailer import MailConfigError, MailSendError

API_KEY = "clave-ficticia-de-pruebas"
RECIPIENT = "persona.prueba@example.com"
RESET_URL = "http://localhost:3000/recuperar?token=secreto-prueba"


class FakeMailer:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def send(self, message):
        if self.error:
            raise self.error
        self.sent.append(message)


@pytest.fixture
def settings():
    return Settings(
        api_key=API_KEY,
        sender="envio@example.com",
        rate_per_recipient=3,
        rate_per_ip=100,
        rate_window_seconds=600,
    )


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def client(settings, mailer):
    return create_app(settings, mailer).test_client()


def payload(**updates):
    body = {
        "email": RECIPIENT,
        "reset_url": RESET_URL,
        "expires_minutes": 15,
    }
    body.update(updates)
    return body


def post(client, body=None, key=API_KEY):
    headers = {"X-Service-Key": key} if key is not None else {}
    return client.post(
        "/api/password-reset/send",
        json=body if body is not None else payload(),
        headers=headers,
    )


@pytest.mark.parametrize("key", [None, "", "otra-clave", API_KEY + "x"])
def test_requires_valid_service_key(client, mailer, key):
    response = post(client, key=key)

    assert response.status_code == 401
    assert mailer.sent == []


def test_unconfigured_service_key_refuses_to_send(mailer):
    client = create_app(Settings(api_key="", sender="envio@example.com"), mailer).test_client()

    response = post(client, key="")

    assert response.status_code == 500
    assert mailer.sent == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": RECIPIENT, "reset_url": RESET_URL},
        {**payload(), "extra": "campo"},
        payload(email="correo-invalido"),
        payload(email="a" * 250 + "@example.com"),
        payload(email=42),
        payload(reset_url="/recuperar?token=abc"),
        payload(reset_url="ftp://example.com/recuperar?token=abc"),
        payload(reset_url="http://example.com/recuperar?token=abc"),
        payload(reset_url="https:///recuperar?token=abc"),
        payload(reset_url="https://usuario:clave@example.com/recuperar?token=abc"),
        payload(reset_url="https://example.com:99999/recuperar?token=abc"),
        payload(reset_url="https://example.com/recuperar?token=abc\r\nBcc:x"),
        payload(reset_url=42),
        payload(expires_minutes=0),
        payload(expires_minutes=31),
        payload(expires_minutes="15"),
        payload(expires_minutes=True),
        payload(expires_minutes=15.5),
    ],
)
def test_invalid_payload_is_rejected(client, mailer, body):
    response = post(client, body)

    assert response.status_code == 400
    assert mailer.sent == []


def test_non_json_body_is_rejected(client, mailer):
    response = client.post(
        "/api/password-reset/send",
        data="no json",
        headers={"X-Service-Key": API_KEY},
    )

    assert response.status_code == 400
    assert mailer.sent == []


@pytest.mark.parametrize(
    "reset_url",
    [
        RESET_URL,
        "http://127.0.0.1:3000/recuperar?token=abc",
        "https://cuenta.example.com/recuperar?token=abc",
    ],
)
def test_sends_password_reset_email(client, mailer, reset_url):
    response = post(client, payload(reset_url=reset_url))

    assert response.status_code == 200
    assert response.get_json() == {"message": "Correo enviado."}
    assert len(mailer.sent) == 1
    message = mailer.sent[0]
    assert message["To"] == RECIPIENT
    assert message["From"] == "envio@example.com"
    assert "LigaHub" in message["Subject"]

    text = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    for content in (text, html):
        assert reset_url in content
        assert "15 minutos" in content
        assert "una vez" in content
        assert "Restablece tu contraseña" in content


def test_password_reset_email_has_html_and_text_alternatives(client, mailer):
    post(client)

    message = mailer.sent[0]
    assert message.get_content_type() == "multipart/alternative"
    assert message.get_body(preferencelist=("plain",)) is not None
    assert message.get_body(preferencelist=("html",)) is not None
    assert "cid:logo" in message.get_body(preferencelist=("html",)).get_content()


def test_recipient_rate_limit(client, mailer):
    statuses = [post(client).status_code for _ in range(5)]

    assert statuses == [200, 200, 200, 429, 429]
    assert len(mailer.sent) == 3


def test_ip_rate_limit_applies_before_authentication(mailer):
    settings = Settings(api_key=API_KEY, sender="envio@example.com", rate_per_ip=2)
    client = create_app(settings, mailer).test_client()

    statuses = [post(client, key="mala").status_code for _ in range(4)]

    assert statuses == [401, 401, 429, 429]
    assert mailer.sent == []


@pytest.mark.parametrize(
    ("provider_error", "status"),
    [
        (MailSendError("HttpError"), 502),
        (MailConfigError("detalle interno"), 500),
    ],
)
def test_provider_errors_return_safe_responses(settings, provider_error, status):
    client = create_app(settings, FakeMailer(error=provider_error)).test_client()

    response = post(client)

    assert response.status_code == status
    assert "detalle interno" not in response.get_data(as_text=True)
    assert "HttpError" not in response.get_data(as_text=True)


def test_logs_mask_recipient_and_never_include_reset_url_or_token(settings, caplog):
    caplog.set_level(logging.DEBUG)
    success = create_app(settings, FakeMailer()).test_client()
    failure = create_app(
        settings,
        FakeMailer(error=MailSendError(f"fallo con {RESET_URL} y {RECIPIENT}")),
    ).test_client()

    post(success)
    post(failure)

    assert caplog.text
    assert "pe***@example.com" in caplog.text
    assert RECIPIENT not in caplog.text
    assert RESET_URL not in caplog.text
    assert "secreto-prueba" not in caplog.text
