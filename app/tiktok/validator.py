from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger("insta2tiktok.tiktok.validator")

MIN_DIMENSION = 360
MAX_DIMENSION = 4096
MIN_FPS = 23.0
MAX_FPS = 60.0
MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024

SUPPORTED_FORMATS = {".mp4", ".mov", ".webm"}
SUPPORTED_CODECS = {"h264", "hevc", "h265", "vp8", "vp9"}

CODEC_LABELS = {
    "file_format_check_failed": "formato no soportado",
    "duration_check_failed": "duracion fuera de rango",
    "frame_rate_check_failed": "framerate fuera de rango (23-60 FPS)",
    "picture_size_check_failed": f"resolucion fuera de rango ({MIN_DIMENSION}-{MAX_DIMENSION} px)",
    "internal": "error interno de TikTok (reintentable)",
    "spam_risk_too_many_posts": "cuota diaria de publicaciones alcanzada",
}


class FFprobeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: Optional[float]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    codec: Optional[str]


@lru_cache(maxsize=1)
def ffprobe_path(configured: Optional[str] = None) -> str:
    candidate = configured or "ffprobe"
    found = shutil.which(candidate)
    if not found:
        raise FFprobeUnavailable(
            "ffprobe no encontrado. Instala FFmpeg o configura FFPROBE_PATH en .env. "
            "Sin ffprobe no se puede validar duracion/resolucion/FPS antes de subir."
        )
    return found


def probe(path: Path, ffprobe_bin: Optional[str] = None) -> MediaInfo:
    binary = ffprobe_path(ffprobe_bin)

    try:
        proc = subprocess.run(
            [
                binary,
                "-v", "error",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                "-select_streams", "v:0",
                str(path),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise FFprobeUnavailable(f"ffprobe tardo demasiado en {path.name}")

    if proc.returncode != 0:
        raise FFprobeUnavailable(
            f"ffprobe fallo con {path.name}: {proc.stderr.decode('utf-8', 'replace')[:200]}"
        )

    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise FFprobeUnavailable(f"No se pudo parsear la salida de ffprobe: {exc}")

    streams = data.get("streams") or []
    if not streams:
        return MediaInfo(None, None, None, None, None)

    stream = streams[0]
    fmt = data.get("format") or {}

    duration: Optional[float] = None
    for source in (stream.get("duration"), fmt.get("duration")):
        try:
            if source is not None:
                duration = float(source)
                break
        except (TypeError, ValueError):
            continue

    return MediaInfo(
        duration=duration,
        width=stream.get("width"),
        height=stream.get("height"),
        fps=_parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        codec=stream.get("codec_name"),
    )


def _parse_fps(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            numerator = float(num)
            denominator = float(den)
        except ValueError:
            return None
        if denominator == 0:
            return None
        return numerator / denominator
    try:
        return float(raw)
    except ValueError:
        return None


def validate(
    path: Path,
    max_duration: int = 600,
    ffprobe_bin: Optional[str] = None,
) -> Optional[str]:
    """Valida el video contra las restricciones de TikTok.

    Devuelve None si es valido, o un mensaje con el motivo del rechazo.
    """
    if not path.exists():
        return f"Archivo no encontrado: {path}"

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        return f"Formato no soportado ({suffix}); TikTok acepta MP4, MOV y WEBM"

    size = path.stat().st_size
    if size > MAX_VIDEO_SIZE:
        return f"Video demasiado grande: {size / 1_048_576:.0f} MB (maximo 4096 MB)"
    if size == 0:
        return "El archivo de video esta vacio"

    try:
        info = probe(path, ffprobe_bin)
    except FFprobeUnavailable as exc:
        log.warning("No se pudo validar el video con ffprobe: %s", exc)
        return None

    if info.codec and info.codec.lower() not in SUPPORTED_CODECS:
        return (
            f"Codec no soportado ({info.codec}); "
            "TikTok acepta H.264, H.265, VP8 y VP9"
        )

    if info.width and info.height:
        if info.width < MIN_DIMENSION or info.height < MIN_DIMENSION:
            return f"Resolucion demasiado pequena: {info.width}x{info.height} (minimo {MIN_DIMENSION}x{MIN_DIMENSION})"
        if info.width > MAX_DIMENSION or info.height > MAX_DIMENSION:
            return f"Resolucion demasiado grande: {info.width}x{info.height} (maximo {MAX_DIMENSION}x{MAX_DIMENSION})"

    if info.fps is not None and (info.fps < MIN_FPS or info.fps > MAX_FPS):
        return f"Framerate fuera de rango: {info.fps:.2f} FPS (debe estar entre {MIN_FPS:.0f} y {MAX_FPS:.0f})"

    if info.duration is not None and info.duration > max_duration:
        return f"Duracion excedida: {info.duration:.1f}s (maximo permitido {max_duration}s para esta cuenta)"

    log.debug(
        "Validacion OK: %s dur=%.1fs %sx%s %.2ffps %s",
        path.name,
        info.duration or 0,
        info.width,
        info.height,
        info.fps or 0,
        info.codec,
    )
    return None