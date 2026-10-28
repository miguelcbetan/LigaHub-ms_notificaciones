"""Configuración del servicio, tomada de emails_google/.env y del entorno del proceso."""
import os
from dataclasses import dataclass
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")


def load_env_file(path: str = ENV_FILE) -> None:
    """Carga líneas CLAVE=VALOR de un archivo .env en os.environ.

    Una variable ya definida en el entorno del proceso tiene prioridad sobre el archivo.
    Se ignoran líneas vacías, comentarios (#) y valores vacíos: así una clave sin rellenar
    sigue contando como "no configurada" en vez de sobrescribir nada.
    """
    try:
        with open(path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    sender: str = ""
    host: str = "127.0.0.1"
    port: int = 5000
    credentials_dir: str = os.path.join(BASE_DIR, "credentials")
    rate_window_seconds: int = 600
    rate_per_recipient: int = 5
    rate_per_ip: int = 30

    @classmethod
    def from_env(cls, env_file: Optional[str] = ENV_FILE) -> "Settings":
        if env_file:
            load_env_file(env_file)

        credentials_dir = os.getenv("GMAIL_CREDENTIALS_DIR") or os.path.join(
            BASE_DIR, "credentials"
        )
        if not os.path.isabs(credentials_dir):
            credentials_dir = os.path.join(BASE_DIR, credentials_dir)

        return cls(
            api_key=os.getenv("EMAIL_SERVICE_API_KEY", ""),
            sender=os.getenv("GMAIL_SENDER", ""),
            host=os.getenv("EMAIL_SERVICE_HOST", "127.0.0.1"),
            port=int(os.getenv("EMAIL_SERVICE_PORT", "5000")),
            credentials_dir=credentials_dir,
            rate_window_seconds=int(os.getenv("EMAIL_RATE_WINDOW_SECONDS", "600")),
            rate_per_recipient=int(os.getenv("EMAIL_RATE_PER_RECIPIENT", "5")),
            rate_per_ip=int(os.getenv("EMAIL_RATE_PER_IP", "30")),
        )
