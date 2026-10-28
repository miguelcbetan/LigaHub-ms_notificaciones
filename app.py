import base64
import os
import pickle
import re
import secrets
from email.mime.text import MIMEText
from typing import Any

from flask import Flask, jsonify, request
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_DIR = os.path.join(BASE_DIR, "credentials")
CLIENT_SECRET_FILE = os.path.join(CREDENTIALS_DIR, "credentials.json")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "token.pickle")
SENDER_EMAIL = os.getenv("GMAIL_SENDER", "miguel.cruz40196@ucaldas.edu.co")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__)


def authenticate_gmail() -> Any:
    """Obtiene credenciales Gmail y las guarda para futuras solicitudes."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(
                    f"No se encontró el archivo de credenciales: {CLIENT_SECRET_FILE}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        os.makedirs(CREDENTIALS_DIR, exist_ok=True)
        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)

    return creds


def create_message_html(to: str, code: str) -> dict[str, str]:
    html_content = f"""
    <html>
      <body>
        <h2>Código de autenticación 2FA</h2>
        <p>Tu código de autenticación es:</p>
        <p style="font-size: 28px; font-weight: bold; letter-spacing: 6px;">
          {code}
        </p>
        <p>Este código es válido únicamente durante el periodo definido por tu aplicación.</p>
      </body>
    </html>
    """
    message = MIMEText(html_content, "html", "utf-8")
    message["to"] = to
    message["from"] = SENDER_EMAIL
    message["subject"] = "Código de autenticación 2FA"
    raw_message = base64.urlsafe_b64encode(message.as_bytes())
    return {"raw": raw_message.decode("utf-8")}


def generate_2fa_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


@app.post("/api/2fa/send")
def send_2fa_code():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="El cuerpo debe ser un JSON válido."), 400

    recipient = payload.get("email")
    if not isinstance(recipient, str) or not EMAIL_PATTERN.fullmatch(recipient):
        return jsonify(error="El campo 'email' debe ser un correo válido."), 400

    try:
        service = build("gmail", "v1", credentials=authenticate_gmail())
        service.users().messages().send(
            userId="me", body=create_message_html(recipient, generate_2fa_code())
        ).execute()
    except (FileNotFoundError, OSError) as exc:
        app.logger.error("No fue posible configurar Gmail: %s", exc)
        return jsonify(error="El servicio de correo no está configurado."), 500
    except Exception:
        app.logger.exception("No fue posible enviar el código 2FA.")
        return jsonify(error="No fue posible enviar el correo."), 502

    return jsonify(message="Código 2FA enviado correctamente."), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
