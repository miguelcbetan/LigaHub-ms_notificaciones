import base64
import email
import logging
from email import policy

import pytest

from app import create_app
from config import Settings
from mailer import GmailMailer, MailConfigError, MailSendError

API_KEY = "clave-ficticia-de-pruebas"
CODE = "482915"
RECIPIENT = "persona.prueba@example.com"


class FakeMailer:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    def send(self, message):
        if self.error:
            raise self.error
        self.sent.append(message)


@pytest.fixture
def mailer():
    return FakeMailer()


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
def client(settings, mailer):
    return create_app(settings, mailer).test_client()


def post(client, body=None, key=API_KEY, **kwargs):
    headers = {"X-Service-Key": key} if key is not None else {}
    return client.post(
        "/api/2fa/send",
        json=body if body is not None else {"email": RECIPIENT, "code": CODE, "expires_minutes": 5},
        headers=headers,
        **kwargs,
    )


# --- envío correcto y plantillas (27, 28, 29) ---------------------------------------------------

def test_sends_verification_email(client, mailer):
    response = post(client)

    assert response.status_code == 200
    assert len(mailer.sent) == 1
    message = mailer.sent[0]
    assert message["To"] == RECIPIENT
    assert message["From"] == "envio@example.com"
    assert "LigaHub" in message["Subject"]
    assert CODE not in message["Subject"]


def test_html_template_is_branded_and_complete(client, mailer):
    post(client)
    html = mailer.sent[0].get_body(preferencelist=("html",)).get_content()

    assert CODE in html
    assert "Tu código de verificación" in html
    assert "Vence en 5 minutos" in html
    assert "¿No fuiste tú?" in html
    assert "© 2026 LigaHub" in html
    # identidad visual: azul, amarillo y rojo de la bandera
    for color in ("#003893", "#fcd116", "#ce1126"):
        assert color in html
    assert "cid:logo" in html
    assert 'name="viewport"' in html
    assert "{{" not in html and "{%" not in html


def test_logo_is_embedded_as_related_part(client, mailer):
    post(client)
    message = mailer.sent[0]

    assert message.get_content_type() == "multipart/alternative"
    html_part = message.get_payload()[1]
    assert html_part.get_content_type() == "multipart/related"
    images = [p for p in html_part.iter_parts() if p.get_content_type() == "image/png"]
    assert len(images) == 1
    assert images[0]["Content-ID"] == "<logo>"
    assert images[0].get_content()[:8] == b"\x89PNG\r\n\x1a\n"


def test_plain_text_alternative_is_available(client, mailer):
    post(client)
    text = mailer.sent[0].get_body(preferencelist=("plain",)).get_content()

    assert CODE in text
    assert "5 minutos" in text
    assert "¿No fuiste tú?" in text
    assert "LigaHub" in text


def test_message_serializes_as_valid_mime(client, mailer):
    post(client)
    parsed = email.message_from_bytes(mailer.sent[0].as_bytes(), policy=policy.default)

    assert parsed.get_body(preferencelist=("plain",)) is not None
    assert parsed.get_body(preferencelist=("html",)) is not None


# --- errores del proveedor (30) ------------------------------------------------------------------

def test_provider_error_returns_502_without_details(settings):
    mailer = FakeMailer(error=MailSendError("HttpError"))
    client = create_app(settings, mailer).test_client()

    response = post(client)

    assert response.status_code == 502
    assert response.get_json() == {"error": "No fue posible enviar el correo."}
    assert CODE not in response.get_data(as_text=True)


def test_missing_gmail_authorization_returns_500(settings):
    mailer = FakeMailer(error=MailConfigError("Falta token.pickle"))
    client = create_app(settings, mailer).test_client()

    response = post(client)

    assert response.status_code == 500
    assert "token.pickle" not in response.get_data(as_text=True)


def test_gmail_mailer_wraps_any_provider_failure_in_send_error(client):
    class BrokenMessages:
        def send(self, **kwargs):
            raise RuntimeError("detalle interno con " + CODE)

    class BrokenService:
        def users(self):
            return self

        def messages(self):
            return BrokenMessages()

    from mailer import build_two_factor_message

    mailer = GmailMailer("/no/existe", service_factory=lambda: BrokenService())
    message = build_two_factor_message(RECIPIENT, "envio@example.com", CODE, 5)

    with pytest.raises(MailSendError) as caught:
        mailer.send(message)
    assert CODE not in str(caught.value)  # solo el tipo de excepción, nunca su detalle


def test_gmail_mailer_sends_base64_raw_message():
    captured = {}

    class Messages:
        def send(self, userId, body):
            captured.update(userId=userId, body=body)
            return self

        def execute(self):
            return {}

    class Service:
        def users(self):
            return self

        def messages(self):
            return Messages()

    from mailer import build_two_factor_message

    mailer = GmailMailer("/no/existe", service_factory=lambda: Service())
    mailer.send(build_two_factor_message(RECIPIENT, "envio@example.com", CODE, 5))

    assert captured["userId"] == "me"
    raw = base64.urlsafe_b64decode(captured["body"]["raw"])
    assert b"To: " + RECIPIENT.encode() in raw


# --- autenticación entre servicios (31) -----------------------------------------------------------

@pytest.mark.parametrize("key", [None, "", "otra-clave", API_KEY + "x"])
def test_requests_without_valid_service_key_are_rejected(client, mailer, key):
    response = post(client, key=key)

    assert response.status_code == 401
    assert mailer.sent == []


def test_service_without_configured_key_refuses_to_send(mailer):
    client = create_app(Settings(api_key="", sender="envio@example.com"), mailer).test_client()

    response = post(client, key="")

    assert response.status_code == 500
    assert mailer.sent == []


# --- validación de entradas -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": RECIPIENT, "code": CODE},  # falta expires_minutes
        {"email": RECIPIENT, "code": CODE, "expires_minutes": 5, "extra": "x"},
        {"email": "no-es-correo", "code": CODE, "expires_minutes": 5},
        {"email": "a@b", "code": CODE, "expires_minutes": 5},
        {"email": "a@example.com\r\nBcc: victima@example.com", "code": CODE, "expires_minutes": 5},
        {"email": "a b@example.com", "code": CODE, "expires_minutes": 5},
        {"email": "a" * 250 + "@example.com", "code": CODE, "expires_minutes": 5},
        {"email": 42, "code": CODE, "expires_minutes": 5},
        {"email": RECIPIENT, "code": "12345", "expires_minutes": 5},
        {"email": RECIPIENT, "code": "12345a", "expires_minutes": 5},
        {"email": RECIPIENT, "code": 123456, "expires_minutes": 5},
        {"email": RECIPIENT, "code": CODE, "expires_minutes": 0},
        {"email": RECIPIENT, "code": CODE, "expires_minutes": 31},
        {"email": RECIPIENT, "code": CODE, "expires_minutes": "5"},
        {"email": RECIPIENT, "code": CODE, "expires_minutes": True},
        {"email": RECIPIENT, "code": CODE, "expires_minutes": 5.5},
    ],
)
def test_invalid_payload_is_rejected(client, mailer, body):
    response = post(client, body=body)

    assert response.status_code == 400
    assert mailer.sent == []


def test_non_json_body_is_rejected(client, mailer):
    response = client.post(
        "/api/2fa/send", data="no json", headers={"X-Service-Key": API_KEY}
    )

    assert response.status_code == 400
    assert mailer.sent == []


# --- abuso: límite de envíos ----------------------------------------------------------------------

def test_recipient_rate_limit(client, mailer):
    statuses = [post(client).status_code for _ in range(5)]

    assert statuses == [200, 200, 200, 429, 429]
    assert len(mailer.sent) == 3


def test_ip_rate_limit(mailer):
    settings = Settings(api_key=API_KEY, sender="envio@example.com", rate_per_ip=2)
    client = create_app(settings, mailer).test_client()

    statuses = [post(client, key="mala").status_code for _ in range(4)]

    assert statuses == [401, 401, 429, 429]


# --- logs sin datos sensibles (32) ----------------------------------------------------------------

def test_logs_never_contain_code_key_or_full_address(settings, caplog):
    caplog.set_level(logging.DEBUG)
    ok = create_app(settings, FakeMailer()).test_client()
    failing = create_app(settings, FakeMailer(error=MailSendError("HttpError"))).test_client()
    misconfigured = create_app(settings, FakeMailer(error=MailConfigError("sin token"))).test_client()

    post(ok)
    post(failing)
    post(misconfigured)
    post(ok, key="clave-equivocada")
    post(ok, body={"email": RECIPIENT, "code": CODE, "expires_minutes": 99})

    assert caplog.text  # sí hay trazas de eventos
    assert CODE not in caplog.text
    assert API_KEY not in caplog.text
    assert "clave-equivocada" not in caplog.text
    assert RECIPIENT not in caplog.text
    assert "pe***@example.com" in caplog.text
