from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_REELS, MAX_CAPTION, MAX_REELS


class ReelsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=30)
    limit: int = Field(default=DEFAULT_REELS, ge=1, le=MAX_REELS)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        username = value.strip().lstrip("@")
        if not username:
            raise ValueError("Introduce una cuenta de Instagram.")
        if not all(character.isalnum() or character in "._" for character in username):
            raise ValueError("El nombre de usuario solo puede contener letras, números, puntos y guiones bajos.")
        return username


class PublishItem(BaseModel):
    shortcode: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    caption: str = Field(default="", max_length=MAX_CAPTION)


class PublishRequest(BaseModel):
    items: list[PublishItem] = Field(min_length=1, max_length=MAX_REELS)
    confirm_rights: bool = False


class InstagramSessionRequest(BaseModel):
    sessionid: str = Field(min_length=10, max_length=500)
    csrftoken: str | None = Field(default=None, max_length=500)


class RetryRequest(BaseModel):
    acknowledge_duplicate_risk: bool = False
