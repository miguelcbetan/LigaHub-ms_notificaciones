import os

import pytest

from config import Settings, load_env_file

KEYS = ["EMAIL_SERVICE_API_KEY", "GMAIL_SENDER", "EMAIL_SERVICE_PORT", "EMAIL_RATE_PER_IP"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
    # load_env_file escribe en os.environ: se limpia para no contaminar otras pruebas
    for key in KEYS:
        os.environ.pop(key, None)


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_settings_are_read_from_env_file(tmp_path):
    path = write_env(
        tmp_path,
        "# comentario\n"
        "EMAIL_SERVICE_API_KEY=clave-ficticia\n"
        'GMAIL_SENDER="envio@example.com"\n'
        "EMAIL_SERVICE_PORT = 5055\n",
    )

    settings = Settings.from_env(env_file=path)

    assert settings.api_key == "clave-ficticia"
    assert settings.sender == "envio@example.com"
    assert settings.port == 5055


def test_process_environment_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_SERVICE_API_KEY", "del-entorno")
    path = write_env(tmp_path, "EMAIL_SERVICE_API_KEY=del-archivo\n")

    assert Settings.from_env(env_file=path).api_key == "del-entorno"


def test_empty_values_count_as_not_configured(tmp_path):
    path = write_env(tmp_path, "EMAIL_SERVICE_API_KEY=\nGMAIL_SENDER=   \n")

    settings = Settings.from_env(env_file=path)

    assert settings.api_key == ""
    assert settings.sender == ""


def test_missing_file_is_not_an_error(tmp_path):
    load_env_file(str(tmp_path / "no-existe.env"))

    assert Settings.from_env(env_file=str(tmp_path / "no-existe.env")).api_key == ""


def test_file_with_bom_and_noise_is_tolerated(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"\xef\xbb\xbfEMAIL_SERVICE_API_KEY=clave\nlinea sin igual\n=sin-clave\n")

    assert Settings.from_env(env_file=str(path)).api_key == "clave"
