from __future__ import annotations

import logging
import sys
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    INSTAGRAM_USERNAME: str = Field(description="Cuenta pública de Instagram a monitorear")
    INSTAGRAM_REELS_COUNT: int = Field(default=5, ge=1, le=50, description="Cantidad de Reels recientes a obtener")
    INSTAGRAM_SESSION_FILE: Optional[str] = Field(default=None, description="Ruta a archivo de sesión de instaloader (opcional)")
    INSTAGRAM_REQUEST_DELAY_MIN: float = Field(default=1.0, ge=0.5, description="Delay mínimo entre peticiones a IG (segundos)")
    INSTAGRAM_REQUEST_DELAY_MAX: float = Field(default=3.0, ge=1.0, description="Delay máximo entre peticiones a IG (segundos)")

    TIKTOK_CLIENT_KEY: str = Field(description="TikTok app client key")
    TIKTOK_CLIENT_SECRET: str = Field(description="TikTok app client secret")
    TIKTOK_REDIRECT_URI: str = Field(
        default="https://localhost:8080/callback",
        description="OAuth redirect URI completo (TikTok exige HTTPS)",
    )
    TIKTOK_SCOPES: str = Field(default="video.publish", description="Scopes separados por coma")
    TIKTOK_TOKEN_PATH: Path = Field(default=Path("tiktok_token.json"), description="Ruta al archivo de token")
    TIKTOK_CALLBACK_HOST: str = Field(default="127.0.0.1", description="Host local donde escucha el callback OAuth")
    TIKTOK_CALLBACK_PORT: int = Field(default=8080, ge=1, le=65535, description="Puerto local del callback OAuth")
    TIKTOK_PRIVACY: str = Field(
        default="SELF_ONLY",
        description="Nivel de privacidad: SELF_ONLY, MUTUAL_FOLLOW_FRIENDS, FOLLOWER_OF_CREATOR, PUBLIC_TO_EVERYONE",
    )
    TIKTOK_CAPTION_PREFIX: str = Field(default="", description="Prefijo para el caption en TikTok")
    TIKTOK_CAPTION_SUFFIX: str = Field(default="", description="Sufijo para el caption en TikTok")
    TIKTOK_CAPTION_HASHTAGS: str = Field(default="", description="Hashtags adicionales para TikTok (ej: #repost #viral)")
    TIKTOK_MAX_VIDEO_DURATION_SEC: int = Field(
        default=600,
        ge=60,
        description="Duración maxima de video enviada a la API (segundos) cuando creator_info no disponible",
    )
    FFPROBE_PATH: str = Field(default="ffprobe", description="Ruta al binario ffprobe (o 'ffprobe' en PATH)")

    DB_PATH: Path = Field(default=Path("reels.db"), description="Ruta a la base de datos SQLite")
    WORKDIR: Path = Field(default=Path("workdir"), description="Directorio de trabajo para videos descargados")
    LOG_LEVEL: str = Field(default="INFO", description="Nivel de logging: DEBUG, INFO, WARNING, ERROR")
    LOG_FILE: Path = Field(default=Path("insta2tiktok.log"), description="Archivo de log")
    LOG_MAX_BYTES: int = Field(default=5_000_000, description="Tamaño máximo del archivo de log antes de rotar")
    LOG_BACKUP_COUNT: int = Field(default=3, description="Cantidad de archivos de log de respaldo")

    MAX_DAILY_PUBLISHES: int = Field(default=5, ge=1, description="Máximo de publicaciones por día")
    PUBLISH_LIMIT_PER_RUN: int = Field(default=2, ge=1, description="Máximo de publicaciones por ejecución")
    MAX_RETRY_COUNT: int = Field(default=3, ge=1, description="Reintentos máximos para Reels fallidos")
    SCHEDULE_INTERVAL_MINUTES: int = Field(default=60, ge=5, description="Intervalo de ejecución programada (minutos)")
    VIDEO_RETENTION_HOURS: int = Field(default=48, ge=1, description="Horas antes de borrar videos ya publicados")

    ENABLE_PERCEPTUAL_HASH: bool = Field(default=False, description="Activar hash perceptual para dedup (requiere imagehash y av)")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("WORKDIR", mode="before")
    @classmethod
    def expand_workdir(cls, v: str | Path) -> Path:
        p = Path(v).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @field_validator("TIKTOK_TOKEN_PATH", "DB_PATH", "LOG_FILE", mode="before")
    @classmethod
    def resolve_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser().resolve()

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"LOG_LEVEL inválido: {v}")
        return v

    @field_validator("TIKTOK_PRIVACY", mode="before")
    @classmethod
    def validate_privacy(cls, v: str) -> str:
        allowed = {"SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"}
        if v not in allowed:
            raise ValueError(f"TIKTOK_PRIVACY debe ser uno de: {allowed}")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    if settings is None:
        settings = get_settings()

    logger = logging.getLogger("insta2tiktok")
    logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
