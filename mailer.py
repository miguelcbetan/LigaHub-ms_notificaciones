"""Construcción y envío de correos (Gmail API). Sin lógica de autenticación de usuarios."""
import base64
import os
import pickle
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Callable, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import BASE_DIR

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
LOGO_FILE = os.path.join(BASE_DIR, "assets", "logo.png")

# Colombia no usa horario de verano: UTC-5 fijo, sin depender de tzdata.
BOGOTA = timezone(timedelta(hours=-5))
MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_html_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)
_text_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=False,
)


class MailConfigError(Exception):
    """Gmail no está configurado o autorizado en este servidor."""


class MailSendError(Exception):
    """Gmail rechazó o no pudo atender el envío."""


def format_request_time(moment: Optional[datetime] = None) -> str:
    local = (moment or datetime.now(timezone.utc)).astimezone(BOGOTA)
    return (
        f"{local.day} de {MONTHS[local.month - 1]} de {local.year}, "
        f"{local:%H:%M} (hora de Colombia)"
    )


def build_two_factor_message(
    to: str,
    sender: str,
    code: str,
    expires_minutes: int,
    requested_at: Optional[datetime] = None,
    logo_bytes: Optional[bytes] = None,
) -> EmailMessage:
    """Correo multipart: texto plano + HTML (con logo embebido por CID)."""
    context = {
        "code": code,
        "expires_minutes": expires_minutes,
        "requested_at": format_request_time(requested_at),
        "has_logo": logo_bytes is not None,
    }

    message = EmailMessage()
    message["To"] = to
    message["From"] = sender
    message["Subject"] = "Tu código de verificación de LigaHub"

    message.set_content(_text_env.get_template("two_factor.txt").render(**context))
    message.add_alternative(
        _html_env.get_template("two_factor.html").render(**context),
        subtype="html",
    )
    if logo_bytes is not None:
        html_part = message.get_payload()[1]
        html_part.add_related(logo_bytes, "image", "png", cid="<logo>")

    return message


def load_logo() -> Optional[bytes]:
    try:
        with open(LOGO_FILE, "rb") as handle:
            return handle.read()
    except OSError:
        return None


class GmailMailer:
    """Envía mensajes con la API de Gmail usando credenciales OAuth guardadas."""

    def __init__(
        self,
        credentials_dir: str,
        service_factory: Optional[Callable[[], Any]] = None,
    ):
        self._credentials_dir = credentials_dir
        self._client_secret = os.path.join(credentials_dir, "credentials.json")
        self._token_file = os.path.join(credentials_dir, "token.pickle")
        self._service_factory = service_factory or self._default_service
        self._service = None
        self._lock = threading.Lock()

    def send(self, message: EmailMessage) -> None:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        # El cliente httplib2 de Google no es seguro entre hilos.
        with self._lock:
            try:
                if self._service is None:
                    self._service = self._service_factory()
                self._service.users().messages().send(
                    userId="me", body={"raw": raw}
                ).execute()
            except MailConfigError:
                raise
            except Exception as exc:  # noqa: BLE001 - el proveedor lanza tipos variados
                self._service = None  # reconstruir (p. ej. token renovado) en el siguiente intento
                raise MailSendError(type(exc).__name__) from None

    # -- credenciales -----------------------------------------------------
    def _default_service(self) -> Any:
        from googleapiclient.discovery import build

        return build("gmail", "v1", credentials=self._load_credentials(), cache_discovery=False)

    def _load_credentials(self) -> Any:
        from google.auth.transport.requests import Request

        if not os.path.exists(self._token_file):
            raise MailConfigError(
                "Falta token.pickle. Autoriza Gmail con:  python app.py --authorize"
            )
        # token.pickle lo genera este mismo servicio; no debe provenir de terceros.
        with open(self._token_file, "rb") as handle:
            creds = pickle.load(handle)

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_token(creds)
            else:
                raise MailConfigError(
                    "Las credenciales de Gmail no son válidas. Vuelve a autorizar con:  python app.py --authorize"
                )
        return creds

    def _save_token(self, creds: Any) -> None:
        os.makedirs(self._credentials_dir, exist_ok=True)
        with open(self._token_file, "wb") as handle:
            pickle.dump(creds, handle)

    def authorize_interactively(self) -> None:
        """Flujo OAuth de escritorio (abre el navegador). Solo desde la línea de comandos."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not os.path.exists(self._client_secret):
            raise MailConfigError(f"No se encontró {self._client_secret}")
        flow = InstalledAppFlow.from_client_secrets_file(self._client_secret, SCOPES)
        self._save_token(flow.run_local_server(port=0))
