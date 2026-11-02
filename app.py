"""Microservicio de correo de LigaHub (Flask + Gmail API).

Solo entrega mensajes: ms-security genera y valida códigos y tokens; este
servicio no es una autoridad de autenticación.
"""
import hmac
import ipaddress
import logging
import re
import sys
from typing import Optional
from urllib.parse import urlsplit

from flask import Flask, jsonify, request

from config import Settings
from mailer import (
    GmailMailer,
    MailConfigError,
    MailSendError,
    build_password_reset_message,
    build_two_factor_message,
    load_logo,
)
from ratelimit import RateLimiter

EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+"
)
CODE_PATTERN = re.compile(r"\d{6}")
ALLOWED_FIELDS = {"email", "code", "expires_minutes"}
PASSWORD_RESET_FIELDS = {"email", "reset_url", "expires_minutes"}
MAX_EMAIL_LENGTH = 254


def mask_email(address: str) -> str:
    """ab***@dominio: suficiente para trazar sin guardar el correo completo."""
    local, _, domain = address.partition("@")
    return f"{local[:2]}***@{domain}"


def is_accepted_reset_url(value: str) -> bool:
    """Acepta HTTPS y HTTP solo para destinos de loopback de desarrollo local."""
    if not value or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value) or "\\" in value:
        return False

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port  # fuerza la validación del formato y rango del puerto
    except ValueError:
        return False

    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False

    if parsed.scheme == "https":
        return True
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def create_app(settings: Optional[Settings] = None, mailer=None) -> Flask:
    settings = settings or Settings.from_env()
    mailer = mailer or GmailMailer(settings.credentials_dir)
    limiter = RateLimiter()
    logo = load_logo()

    app = Flask(__name__)
    app.logger.setLevel(logging.INFO)

    def error(message: str, status: int):
        return jsonify(error=message), status

    @app.post("/api/2fa/send")
    def send_2fa_code():
        if not settings.api_key or not settings.sender:
            app.logger.error("Servicio sin EMAIL_SERVICE_API_KEY o GMAIL_SENDER configurados")
            return error("El servicio de correo no está configurado.", 500)

        client_ip = request.remote_addr or "desconocida"
        if not limiter.allow(f"ip:{client_ip}", settings.rate_per_ip, settings.rate_window_seconds):
            return error("Demasiadas solicitudes.", 429)

        provided = request.headers.get("X-Service-Key", "")
        if not hmac.compare_digest(provided.encode("utf-8"), settings.api_key.encode("utf-8")):
            app.logger.warning("Solicitud rechazada: clave de servicio inválida")
            return error("No autorizado.", 401)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != ALLOWED_FIELDS:
            return error("Cuerpo inválido.", 400)

        recipient = payload["email"]
        code = payload["code"]
        minutes = payload["expires_minutes"]

        if (
            not isinstance(recipient, str)
            or len(recipient) > MAX_EMAIL_LENGTH
            or not EMAIL_PATTERN.fullmatch(recipient)
            or not isinstance(code, str)
            or not CODE_PATTERN.fullmatch(code)
            or not isinstance(minutes, int)
            or isinstance(minutes, bool)
            or not 1 <= minutes <= 30
        ):
            return error("Datos inválidos.", 400)

        if not limiter.allow(
            f"to:{recipient.lower()}", settings.rate_per_recipient, settings.rate_window_seconds
        ):
            return error("Demasiadas solicitudes.", 429)

        try:
            message = build_two_factor_message(
                to=recipient,
                sender=settings.sender,
                code=code,
                expires_minutes=minutes,
                logo_bytes=logo,
            )
            mailer.send(message)
        except MailConfigError as exc:
            app.logger.error("Gmail no configurado: %s", exc)
            return error("El servicio de correo no está configurado.", 500)
        except MailSendError as exc:
            # Solo el tipo de fallo: el detalle del proveedor puede incluir datos de la petición.
            app.logger.error("Gmail no pudo enviar el correo (%s)", exc)
            return error("No fue posible enviar el correo.", 502)

        app.logger.info("Correo 2FA enviado a %s", mask_email(recipient))
        return jsonify(message="Correo enviado."), 200

    @app.post("/api/password-reset/send")
    def send_password_reset():
        if not settings.api_key or not settings.sender:
            app.logger.error("Servicio sin EMAIL_SERVICE_API_KEY o GMAIL_SENDER configurados")
            return error("El servicio de correo no está configurado.", 500)

        client_ip = request.remote_addr or "desconocida"
        if not limiter.allow(f"ip:{client_ip}", settings.rate_per_ip, settings.rate_window_seconds):
            return error("Demasiadas solicitudes.", 429)

        provided = request.headers.get("X-Service-Key", "")
        if not hmac.compare_digest(provided.encode("utf-8"), settings.api_key.encode("utf-8")):
            app.logger.warning("Solicitud rechazada: clave de servicio inválida")
            return error("No autorizado.", 401)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) != PASSWORD_RESET_FIELDS:
            return error("Cuerpo inválido.", 400)

        recipient = payload["email"]
        reset_url = payload["reset_url"]
        minutes = payload["expires_minutes"]

        if (
            not isinstance(recipient, str)
            or len(recipient) > MAX_EMAIL_LENGTH
            or not EMAIL_PATTERN.fullmatch(recipient)
            or not isinstance(reset_url, str)
            or not is_accepted_reset_url(reset_url)
            or not isinstance(minutes, int)
            or isinstance(minutes, bool)
            or not 1 <= minutes <= 30
        ):
            return error("Datos inválidos.", 400)

        if not limiter.allow(
            f"to:{recipient.lower()}", settings.rate_per_recipient, settings.rate_window_seconds
        ):
            return error("Demasiadas solicitudes.", 429)

        try:
            message = build_password_reset_message(
                to=recipient,
                sender=settings.sender,
                reset_url=reset_url,
                expires_minutes=minutes,
                logo_bytes=logo,
            )
            mailer.send(message)
        except MailConfigError:
            app.logger.error("Gmail no está configurado para enviar el correo de recuperación")
            return error("El servicio de correo no está configurado.", 500)
        except MailSendError:
            app.logger.error("Gmail no pudo enviar el correo de recuperación")
            return error("No fue posible enviar el correo.", 502)

        app.logger.info("Correo de recuperación enviado a %s", mask_email(recipient))
        return jsonify(message="Correo enviado."), 200

    return app


def main(argv: list) -> None:
    settings = Settings.from_env()

    if "--authorize" in argv:
        GmailMailer(settings.credentials_dir).authorize_interactively()
        print("Gmail autorizado. Se guardó token.pickle en la carpeta de credenciales.")
        return

    create_app(settings).run(host=settings.host, port=settings.port, debug=False, threaded=True)


if __name__ == "__main__":
    main(sys.argv[1:])
