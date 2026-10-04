from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth import TikTokAuth
from ..config.settings import Settings, get_settings

log = logging.getLogger("insta2tiktok.tiktok.client")

BASE_URL = "https://open.tiktokapis.com/v2"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MiB (máximo de TikTok por chunk)
MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024  # 4 GiB


class TikTokAPIError(Exception):
    def __init__(self, message: str, code: str = "", http_status: int = 0):
        self.code = code
        self.http_status = http_status
        super().__init__(message)


class TikTokClient:
    def __init__(self, auth: TikTokAuth | None = None, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._auth = auth or TikTokAuth(self._settings)
        self._session = self._create_session()

    def _create_session(self) -> requests.Session:
        s = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503],
            allowed_methods=["GET", "POST"],
        )
        s.mount("https://", HTTPAdapter(max_retries=retries))
        return s

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.get_access_token()}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _check_response(self, resp: requests.Response, context: str) -> dict:
        data = resp.json()
        error = data.get("error", {})
        if error.get("code") and error["code"] != "ok":
            code = error.get("code", "unknown")
            message = error.get("message", resp.text)
            log.error("%s falló: code=%s message=%s", context, code, message)
            raise TikTokAPIError(message, code=code, http_status=resp.status_code)
        return data

    def query_creator_info(self) -> Dict[str, Any]:
        log.info("Consultando información del creador...")
        resp = self._session.post(
            f"{BASE_URL}/post/publish/creator_info/query/",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        return self._check_response(resp, "creator_info")

    def init_upload(
        self,
        video_path: Path,
        caption: str,
        privacy_level: str,
    ) -> Dict[str, Any]:
        file_size = video_path.stat().st_size
        if file_size > MAX_VIDEO_SIZE:
            raise TikTokAPIError(f"Video demasiado grande: {file_size} bytes (máx {MAX_VIDEO_SIZE})")

        chunk_count = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE

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
                "video_size": file_size,
                "chunk_size": min(CHUNK_SIZE, file_size),
                "total_chunk_count": chunk_count,
            },
        }

        log.info(
            "Inicializando subida: %s (%.2f MB, %d chunks)",
            video_path.name,
            file_size / 1_048_576,
            chunk_count,
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

    def upload_video_chunks(self, upload_url: str, video_path: Path) -> None:
        file_size = video_path.stat().st_size

        with video_path.open("rb") as f:
            chunk_index = 0
            offset = 0
            while offset < file_size:
                chunk = f.read(CHUNK_SIZE)
                end = offset + len(chunk) - 1

                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{file_size}",
                }

                log.debug("Subiendo chunk %d: bytes %d-%d/%d", chunk_index, offset, end, file_size)

                resp = self._session.put(upload_url, data=chunk, headers=headers, timeout=120)
                resp.raise_for_status()

                offset += len(chunk)
                chunk_index += 1
                log.info(
                    "Chunk %d subido (%.1f%%)",
                    chunk_index,
                    (offset / file_size) * 100,
                )

    def check_publish_status(self, publish_id: str, max_polls: int = 10, interval: int = 5) -> Dict[str, Any]:
        for attempt in range(max_polls):
            log.debug("Consultando estado de publicación %s (intento %d/%d)", publish_id, attempt + 1, max_polls)

            resp = self._session.post(
                f"{BASE_URL}/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
                timeout=30,
            )
            resp.raise_for_status()
            data = self._check_response(resp, "publish_status")

            status = data.get("data", {}).get("status", "UNKNOWN")

            if status == "PUBLISH_COMPLETE":
                log.info("Publicación completada: %s", publish_id)
                return data.get("data", data)
            elif status in {"FAILED", "PUBLISH_FAILED"}:
                fail_reason = data.get("data", {}).get("fail_reason", "desconocido")
                raise TikTokAPIError(
                    f"Publicación fallida: {fail_reason}",
                    code=status,
                )

            log.info("Estado: %s — esperando %ds...", status, interval)
            time.sleep(interval)

        raise TikTokAPIError(
            f"Timeout: publicación {publish_id} no completó en {max_polls * interval}s",
            code="TIMEOUT",
        )
