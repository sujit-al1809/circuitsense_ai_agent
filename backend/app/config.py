"""Application configuration loaded from environment variables (and an optional .env file).

Nothing here hardcodes a Bedrock model ID: the model IDs must be supplied through
``BEDROCK_TEXT_MODEL_ID`` / ``BEDROCK_VISION_MODEL_ID`` when ``PROVIDER_MODE=bedrock``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

ProviderMode = Literal["demo", "bedrock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "CircuitSense"
    environment: str = "local"

    # "demo" = deterministic, rule-based agent and bundled sample data (no AWS calls).
    # "bedrock" = Amazon Bedrock Converse API for vision + reasoning.
    provider_mode: ProviderMode = "demo"

    # BEDROCK_REGION wins over AWS_REGION (some hosts such as Vercel reserve AWS_REGION).
    aws_region: str | None = Field(default=None, validation_alias=AliasChoices("BEDROCK_REGION", "AWS_REGION"))
    bedrock_text_model_id: str | None = None
    bedrock_vision_model_id: str | None = None
    bedrock_max_tokens: int = Field(default=4096, ge=256, le=64000)
    # Only sent when explicitly configured: several current models reject sampling params.
    bedrock_temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    bedrock_read_timeout_s: int = Field(default=120, ge=5, le=900)

    agent_max_iterations: int = Field(default=6, ge=1, le=20)
    agent_max_tool_calls_per_iteration: int = Field(default=4, ge=1, le=10)
    agent_max_tool_failures: int = Field(default=3, ge=1, le=10)
    agent_history_turns: int = Field(default=6, ge=0, le=50)

    max_upload_mb: float = Field(default=10.0, gt=0, le=50)
    max_pdf_pages: int = Field(default=30, ge=1, le=500)
    max_image_pixels: int = Field(default=40_000_000, ge=1_000_000)
    preview_max_dimension: int = Field(default=2000, ge=512, le=8000)
    model_image_max_dimension: int = Field(default=1568, ge=256, le=8000)

    session_max_count: int = Field(default=100, ge=1, le=10_000)

    s3_bucket: str | None = None
    s3_prefix: str = "circuitsense/uploads"

    data_dir: Path = REPO_ROOT / "data"
    datasheet_dir: Path | None = None
    retrieval_backend: Literal["keyword"] = "keyword"

    ngspice_path: str | None = None
    simulation_timeout_s: float = Field(default=10.0, gt=0, le=60)

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    frontend_dist: Path | None = None

    @field_validator("bedrock_text_model_id", "bedrock_vision_model_id", "aws_region", "s3_bucket", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def resolved_datasheet_dir(self) -> Path:
        return self.datasheet_dir or (self.data_dir / "sample_datasheets")

    @property
    def sample_circuit_dir(self) -> Path:
        return self.data_dir / "sample_circuits"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_vision_model_id(self) -> str | None:
        return self.bedrock_vision_model_id or self.bedrock_text_model_id

    def provider_config_issues(self) -> list[str]:
        """Return blocking configuration problems for the selected provider."""
        issues: list[str] = []
        if self.provider_mode == "bedrock":
            if not self.aws_region:
                issues.append("AWS_REGION is not set.")
            if not self.bedrock_text_model_id:
                issues.append("BEDROCK_TEXT_MODEL_ID is not set.")
        return issues

    def provider_config_notes(self) -> list[str]:
        """Return non-blocking configuration notes for the selected provider."""
        notes: list[str] = []
        if self.provider_mode == "bedrock" and not self.bedrock_vision_model_id:
            notes.append(
                "BEDROCK_VISION_MODEL_ID is not set; the text model will be used for images "
                "and must accept image input."
            )
        return notes


BEDROCK_API_KEY_VAR = "AWS_BEARER_TOKEN_BEDROCK"


def apply_bedrock_api_key_from_dotenv(env_file: Path = BACKEND_DIR / ".env") -> str | None:
    """Export a Bedrock API key found in backend/.env so boto3 can use it.

    boto3 reads ``AWS_BEARER_TOKEN_BEDROCK`` from the process environment only. A key
    written in backend/.env deliberately overrides an older value inherited from the OS
    environment, so rotating the key in .env always takes effect. Returns where the key
    came from (".env", "environment" or None) – never the key itself.
    """
    import os

    from dotenv import dotenv_values

    value = None
    if env_file.is_file():
        values = dotenv_values(env_file)
        value = (values.get(BEDROCK_API_KEY_VAR) or values.get("BEDROCK_API_KEY") or "").strip() or None
    if not value and os.environ.get("BEDROCK_API_KEY"):
        # Alias for hosts where AWS_* variable names are reserved or awkward to set.
        value = os.environ["BEDROCK_API_KEY"].strip() or None
    if value:
        os.environ[BEDROCK_API_KEY_VAR] = value
        return ".env"
    return "environment" if os.environ.get(BEDROCK_API_KEY_VAR) else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
