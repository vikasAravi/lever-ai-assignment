"""How a server session is configured (tier1 spec §1 and §5, T1-1 and T1-5).

A session is (role, root entity), fixed when the server starts; no tool can change it.
Secrets come only from `.env`, never from the command line or a tool argument.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, SecretStr


class SessionConfig(BaseModel):
    role: str                                    # "onboarding" / "inventory"
    root_entity: str                             # full GLPI path, e.g. "Root entity > Acme"
    glpi_url: str = "http://localhost:8080"      # always the proxy, never GLPI directly (R1)
    brain_dir: Path = Path("brain")
    approvals_path: Optional[Path] = None        # onboarding only (A3.5)
    log_dir: Path = Path("runs/server")
    listen_host: str = "127.0.0.1"
    listen_port: int = 8701


class Credentials(BaseModel):
    """Loaded from .env at start-up. SecretStr keeps values out of repr() and logs."""

    app_token: SecretStr
    user_token: SecretStr

    @classmethod
    def from_env_file(cls, token_env: str, env_path: Path = Path(".env")) -> "Credentials":
        values: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("'\"")
        # Decision: T1-5 — the process environment may override .env (containers), never argv.
        app = os.environ.get("GLPI_APP_TOKEN") or values.get("GLPI_APP_TOKEN", "")
        user = os.environ.get(token_env) or values.get(token_env, "")
        if not app or not user:
            raise ValueError(f"GLPI_APP_TOKEN and {token_env} must be set in .env")
        return cls(app_token=SecretStr(app), user_token=SecretStr(user))
