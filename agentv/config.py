from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_ROOT / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AgentV"
    log_level: str = "INFO"

    # identity
    owner_ids: str = ""

    # bot api
    bot_api_enabled: bool = True
    bot_token: str = ""
    bot_webhook_url: str = ""
    bot_webhook_secret: str = ""
    bot_username: str = ""

    # userbot
    userbot_enabled: bool = False
    tg_api_id: int = 0
    tg_api_hash: str = ""
    userbot_session: str = ""
    userbot_prefix: str = "."
    userbot_allowed_users: str = ""

    # storage
    database_path: str = "data/agentv.db"
    data_dir: str = "data"
    secret_key: str = ""

    # llm
    llm_base_url: str = "https://api.modelrouter.in/v1"
    llm_api_key: str = ""
    llm_model: str = "minimax-m3"
    llm_vision_model: str = ""
    llm_timeout: float = 120.0
    thinking_timeout_seconds: int = 60  # max pure-thinking time before we force an answer
    llm_max_retries: int = 3
    max_tool_steps: int = 8
    max_history: int = 20
    max_reply_chars: int = 12000
    rate_limit_per_min: int = 0  # per user per chat (owner exempt)
    auto_react: bool = True  # react to messages automatically by mood
    reasoning_default: str = "high"  # instant | low | medium | high | max
    tool_log_ttl: int = 10  # seconds after which the tool-log message deletes itself
    tool_log_details: bool = False  # include command/output inside the tool log

    # sandbox
    sandbox_enabled: bool = True
    sandbox_image: str = "agentv-runner:latest"
    sandbox_network: bool = True
    sandbox_memory: str = "1g"
    sandbox_storage: str = "4g"
    sandbox_cpus: float = 1.0
    sandbox_pids: int = 256
    sandbox_timeout: int = 25
    tool_timeout_seconds: int = 25  # hard cap per tool execution
    sandbox_max_concurrent: int = 4
    sandbox_volume: str = "agentv-workspace"
    workspace_dir: str = "/workspace"
    allow_user_shell: bool = False

    # tools / plugins
    brave_api_key: str = ""
    plugins_dir: str = "plugins"
    stream_edit_interval: float = 0.7

    # rendering (Bot API 10.x)
    rich_messages: str = "auto"  # auto | on | off
    draft_streaming: str = "off"  # off = single-message edit streaming (duplicate-free)
    show_thinking: bool = True

    # media limits (MB) — incoming files vs files the bot sends
    max_upload_mb: int = 10
    max_download_mb: int = 50

    # web
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    # ------------------------------------------------------------------
    @field_validator(
        "tg_api_id",
        "web_port",
        "llm_max_retries",
        "max_tool_steps",
        "max_history",
        "max_reply_chars",
        "sandbox_pids",
        "sandbox_timeout",
        "sandbox_max_concurrent",
        mode="before",
    )
    @classmethod
    def _empty_int(cls, value: object) -> object:
        return 0 if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("llm_timeout", "sandbox_cpus", "stream_edit_interval", mode="before")
    @classmethod
    def _empty_float(cls, value: object) -> object:
        return 0.0 if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("bot_api_enabled", "sandbox_enabled", "sandbox_network", "show_thinking", "tool_log_details", "auto_react", mode="before")
    @classmethod
    def _empty_bool_true(cls, value: object) -> object:
        return True if value is None or (isinstance(value, str) and not value.strip()) else value

    @field_validator("userbot_enabled", "allow_user_shell", mode="before")
    @classmethod
    def _empty_bool_false(cls, value: object) -> object:
        return False if value is None or (isinstance(value, str) and not value.strip()) else value

    @model_validator(mode="after")
    def _fallbacks(self) -> "Settings":
        # Compatibility with the classic ~/.modelrouter.env layout.
        if not self.llm_api_key:
            env_file = Path.home() / ".modelrouter.env"
            if env_file.exists():
                try:
                    for line in env_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip('"').strip("'")
                        if k == "MODELROUTER_API_KEY" and v:
                            self.llm_api_key = v
                        elif k == "LLM_MODEL" and v and self.llm_model == "minimax-m3":
                            self.llm_model = v
                        elif k == "LLM_BASE_URL" and v:
                            self.llm_base_url = v
                except OSError:
                    pass
        if not self.llm_api_key:
            self.llm_api_key = os.environ.get("MODELROUTER_API_KEY", "")
        return self

    # ------------------------------------------------------------------
    @property
    def owners(self) -> set[int]:
        out: set[int] = set()
        for part in self.owner_ids.replace(";", ",").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.add(int(part))
        return out

    @property
    def allowed_userbot_users(self) -> set[int]:
        out: set[int] = set()
        for part in self.userbot_allowed_users.replace(";", ",").split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.add(int(part))
        return out

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def db_path(self) -> Path:
        p = Path(self.database_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def uploads_path(self) -> Path:
        return self.data_path / "uploads"

    @property
    def exports_path(self) -> Path:
        return self.data_path / "exports"

    @property
    def runner_dir(self) -> Path:
        return PROJECT_ROOT / "runner"

    def ensure_dirs(self) -> None:
        for p in (self.data_path, self.db_path.parent, self.uploads_path, self.exports_path):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
