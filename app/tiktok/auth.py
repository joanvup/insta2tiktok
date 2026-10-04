from __future__ import annotations

import json
import logging
import secrets
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from ..config.settings import Settings, get_settings

log = logging.getLogger("insta2tiktok.tiktok.auth")

BASE_URL = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = f"{BASE_URL}/oauth/token/"


class _CallbackHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self) -> None:  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)

        received_state = qs.get("state", [""])[0]
        expected_state = getattr(self.server, "expected_state", "")
        if expected_state and received_state != expected_state:
            log.error("State CSRF no coincide: esperado=%s recibido=%s", expected_state, received_state)
            self.server.auth_code = None  # type: ignore[attr-defined]
            self.server.auth_error = "state_mismatch"  # type: ignore[attr-defined]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Error: state no coincide</h2></body></html>")
            return

        if self.path.endswith("/known_hosts"):  # chrome probes
            self.send_response(204)
            self.end_headers()
            return

        if "code" in qs:
            self.server.auth_code = qs["code"][0]  # type: ignore[attr-defined]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Autorizacion exitosa</h2>"
                b"<p>Puedes cerrar esta ventana y volver a la terminal.</p></body></html>"
            )
        else:
            self.server.auth_code = None  # type: ignore[attr-defined]
            self.server.auth_error = qs.get("error", ["unknown"])[0]  # type: ignore[attr-defined]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Error de autorizacion</h2></body></html>")

    def log_message(self, format, *args) -> None:  # noqa: A002
        log.debug(format, *args)


class _AuthServer(HTTPServer):
    auth_code: Optional[str] = None
    auth_error: Optional[str] = None
    expected_state: str = ""


class TikTokAuth:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._token_path = self._settings.TIKTOK_TOKEN_PATH

    def _save_token(self, data: dict) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        log.info("Token guardado en %s", self._token_path)

    def _load_token(self) -> Optional[dict]:
        if not self._token_path.exists():
            return None
        try:
            return json.loads(self._token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("No se pudo leer token existente: %s", exc)
            return None

    def authorize(self, manual: bool = False) -> dict:
        """Inicia el flujo OAuth2. Si manual=True, pide el código por consola
        (útil en servidores headless sin navegador/callback).
        """
        state = secrets.token_urlsafe(16)

        if self._settings.TIKTOK_REDIRECT_URI.startswith("http://"):
            log.warning(
                "TIKTOK_REDIRECT_URI usa http:// (%s). TikTok exige HTTPS. "
                "Para pruebas locales usa ngrok: 'ngrok http %s' y registra "
                "la URL https resultante (ver README sección Redirect URI).",
                self._settings.TIKTOK_REDIRECT_URI,
                self._settings.TIKTOK_CALLBACK_PORT,
            )

        scopes = ",".join(s.strip() for s in self._settings.TIKTOK_SCOPES.split(",") if s.strip())
        auth_url = (
            f"{AUTH_URL}"
            f"?client_key={self._settings.TIKTOK_CLIENT_KEY}"
            f"&response_type=code"
            f"&scope={scopes}"
            f"&redirect_uri={self._settings.TIKTOK_REDIRECT_URI}"
            f"&state={state}"
        )

        if manual:
            print(
                "\nAbre esta URL en el navegador:\n"
                f"{auth_url}\n\n"
                "Despues de autorizar, TikTok te redirigira a una URL como:\n"
                f"  {self._settings.TIKTOK_REDIRECT_URI}?code=XXXX&state=XXXX\n"
                "Pega el valor de 'code' a continuacion.\n"
            )
            code = input("code = ").strip()

        else:
            print(f"\nSi el navegador no se abre, visita manualmente:\n{auth_url}\n")
            webbrowser.open(auth_url)

            host = self._settings.TIKTOK_CALLBACK_HOST
            port = self._settings.TIKTOK_CALLBACK_PORT

            with _AuthServer((host, port), _CallbackHandler) as httpd:
                httpd.expected_state = state
                log.info(
                    "Esperando callback OAuth en %s:%d (redirect_uri es %s)...",
                    host,
                    port,
                    self._settings.TIKTOK_REDIRECT_URI,
                )
                print(f"Esperando autorización en http://{host}:{port}/ ...")
                httpd.handle_request()

                if not httpd.auth_code:
                    raise RuntimeError(f"OAuth fallido: {httpd.auth_error}")

                code = httpd.auth_code

        log.info("Codigo de autorizacion recibido, intercambiando por token...")

        resp = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self._settings.TIKTOK_CLIENT_KEY,
                "client_secret": self._settings.TIKTOK_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self._settings.TIKTOK_REDIRECT_URI,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(f"Respuesta inesperada de TikTok: {data}")

        token = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "open_id": data.get("open_id", ""),
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
            ).isoformat(),
            "refresh_expires_at": (
                datetime.now(timezone.utc)
                + timedelta(seconds=data.get("refresh_expires_in", 86400 * 365))
            ).isoformat(),
        }
        self._save_token(token)
        return token

    def get_access_token(self) -> str:
        token = self._load_token()
        if not token:
            raise RuntimeError(
                "No hay token de TikTok. Ejecuta 'python -m app auth' primero."
            )

        expires_at = datetime.fromisoformat(token["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if datetime.now(timezone.utc) >= expires_at - timedelta(minutes=5):
            log.info("Token expirado o proximo a expirar, refrescando...")
            token = self._refresh_token(token)

        return token["access_token"]

    def _refresh_token(self, token: dict) -> dict:
        resp = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self._settings.TIKTOK_CLIENT_KEY,
                "client_secret": self._settings.TIKTOK_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "access_token" not in data:
            raise RuntimeError(
                f"Error al refrescar token. Ejecuta 'python -m app auth' de nuevo. Respuesta: {data}"
            )

        token["access_token"] = data["access_token"]
        token["refresh_token"] = data.get("refresh_token", token["refresh_token"])
        token["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
        ).isoformat()
        self._save_token(token)
        log.info("Token refrescado exitosamente")
        return token

    def get_open_id(self) -> str:
        token = self._load_token()
        if not token or "open_id" not in token:
            raise RuntimeError("No hay open_id. Ejecuta 'python -m app auth' primero.")
        return token["open_id"]
