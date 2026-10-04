from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth import TikTokAuth
from .chunking import ChunkPlan, content_type_for, iter_chunks
from ..config.settings import Settings, get_settings

log = logging.getLogger("insta2tiktok.tiktok.client")

BASE_URL = "https://open.tiktokapis.com/v2"

PENDING_STATUSES = {
    "PROCESSING_UPLOAD",
    "PROCESSING_DOWNLOAD",
    "SEND_TO_USER_INBOX",
}

FINAL_STATUSES = {
    "PUBLISH_COMPLETE",
    "FAILED",
}

NON_RETRYABLE_FAIL_REASONS = {
    "auth_removed",
    "spam_risk_user_banned_from_posting",
    "spam_risk_text",
    "spam_risk",
    "spam_risk_too_many_posts",
    "publish_cancelled",
}


class TikTokAPIError(Exception):
    def __init__(self, message: str, code: str = "", http_status: int = 0, retryable: bool = False):
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        super().__init__(message)


class TikTokClient:
    def __init__(self, auth: TikTokAuth | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._auth = auth or TikTokAuth(self._settings)
        self._session = self._create_session()
        self._creator_info: Optional[Dict[str, Any]] = None

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503],
            allowed_methods=["GET", "POST", "PUT"],
        )
        s.mount("https://", HTTPAdapter(max_retries=retries))
        return s

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.get_access_token()}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _check_response(self, resp: requests.Response, context: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise TikTokAPIError(
                f"{context}: respuesta no JSON (HTTP {resp.status_code}): {resp.text[:300]}",
                code="invalid_response",
                http_status=resp.status_code,
            )

        error = data.get("error", {})
        code = error.get("code", "")
        if code and code != "ok":
            message = error.get("message") or resp.text[:300]
            retryable = code in {"rate_limit_exceeded", "internal_error"}
            log.error("%s fallo: code=%s message=%s", context, code, message)
            raise TikTokAPIError(message, code=code, http_status=resp.status_code, retryable=retryable)

        return data

    def query_creator_info(self, force: bool = False) -> Dict[str, Any]:
        """Consulta y cachea la info del creador (1 por ejecucion)."""
        if self._creator_info is not None and not force:
            return self._creator_info

        log.info("Consultando informacion del creador...")
        resp = self._session.post(
            f"{BASE_URL}/post/publish/creator_info/query/",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = self._check_response(resp, "creator_info")
        self._creator_info = data.get("data", data)
        log.info(
            "Creador=%s opciones_privacy=%s duracion_max=%ss",
            self._creator_info.get("creator_username", "?"),
            self._creator_info.get("privacy_level_options", []),
            self._creator_info.get("max_video_post_duration_sec", "?"),
        )
        return self._creator_info

    def resolve_privacy_level(self, requested: str) -> str:
        """Ajusta el nivel de privacidad a las opciones reales del creador.

        TikTok devuelve privacy_level_option_mismatch si se manda un valor
        no admitido, asi que hay que respetarlo estrictamente.
        """
        try:
            options = self.query_creator_info().get("privacy_level_options") or []
        except TikTokAPIError as exc:
            log.warning("No se pudo consultar creator_info (%s); usando %s por defecto", exc, requested)
            return requested

        if not options:
            return requested

        if requested in options:
            return requested

        if "SELF_ONLY" in options:
            log.warning(
                "Nivel de privacidad %s no permitido para esta cuenta; usando SELF_ONLY",
                requested,
            )
            return "SELF_ONLY"

        fallback = options[0]
        log.warning("Nivel %s no permitido; usando %s", requested, fallback)
        return fallback

    def max_duration_seconds(self) -> int:
        try:
            value = self.query_creator_info().get("max_video_post_duration_sec")
            return int(value) if value else self._settings.TIKTOK_MAX_VIDEO_DURATION_SEC
        except (TikTokAPIError, TypeError, ValueError):
            return self._settings.TIKTOK_MAX_VIDEO_DURATION_SEC

    def init_upload(
        self,
        video_path: Path,
        caption: str,
        privacy_level: str,
        plan: ChunkPlan,
    ) -> Dict[str, Any]:
        payload = {
            "post_info": {
                "title": caption,
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": plan.total_size,
                "chunk_size": plan.chunk_size,
                "total_chunk_count": plan.total_chunk_count,
            },
        }

        log.info(
            "Inicializando subida: %s (%.2f MB, %d chunks de %.1f MB)",
            video_path.name,
            plan.total_size / 1_048_576,
            plan.total_chunk_count,
            plan.chunk_size / 1_048_576,
        )

        resp = self._session.post(
            f"{BASE_URL}/post/publish/video/init/",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = self._check_response(resp, "init_upload")
        return data.get("data", data)

    def upload_video_chunks(self, upload_url: str, video_path: Path, plan: ChunkPlan) -> None:
        content_type = content_type_for(video_path)
        offset = 0
        started = time.monotonic()

        for index, chunk in iter_chunks(video_path, plan):
            end = offset + len(chunk) - 1
            headers = {
                "Content-Type": content_type,
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{end}/{plan.total_size}",
            }

            log.debug(
                "Subiendo chunk %d/%d: bytes %d-%d/%d",
                index + 1,
                plan.total_chunk_count,
                offset,
                end,
                plan.total_size,
            )

            resp = self._session.put(upload_url, data=chunk, headers=headers, timeout=300)
            resp.raise_for_status()

            offset += len(chunk)
            log.info(
                "Chunk %d/%d subido (%.1f%%)",
                index + 1,
                plan.total_chunk_count,
                (offset / plan.total_size) * 100,
            )

        elapsed = time.monotonic() - started
        log.info("Subida completa: %s en %.1fs", video_path.name, elapsed)

    def check_publish_status(
        self,
        publish_id: str,
        max_polls: int = 12,
        interval: int = 5,
    ) -> Dict[str, Any]:
        for attempt in range(max_polls):
            log.debug("Consultando estado %s (intento %d/%d)", publish_id, attempt + 1, max_polls)

            resp = self._session.post(
                f"{BASE_URL}/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
                timeout=30,
            )
            resp.raise_for_status()
            data = self._check_response(resp, "publish_status")

            result = data.get("data", data)
            status = result.get("status", "UNKNOWN")

            if status == "PUBLISH_COMPLETE":
                log.info("Publicacion completada: %s", publish_id)
                return result

            if status == "FAILED":
                fail_reason = result.get("fail_reason", "desconocido")
                retryable = fail_reason not in NON_RETRYABLE_FAIL_REASONS
                if not retryable:
                    log.error("Fallo no reintentable: %s", fail_reason)
                raise TikTokAPIError(
                    f"Publicacion fallida ({fail_reason})",
                    code=fail_reason,
                    retryable=retryable,
                )

            if status not in PENDING_STATUSES:
                log.warning("Estado desconocido: %s", status)

            log.info("Estado: %s, esperando %ds...", status, interval)
            time.sleep(interval)

        raise TikTokAPIError(
            f"Timeout: la publicacion {publish_id} no completo en {max_polls * interval}s",
            code="TIMEOUT",
            retryable=True,
        )