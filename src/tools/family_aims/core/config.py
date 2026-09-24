"""
Toda la configuración del agente vive aquí, leída una sola vez desde el .env
del root con prefijo FA_. Nada más en el proyecto debe llamar a os.getenv
directamente.

IVF y SU son calendarios independientes: cada uno con su propia autorización
OAuth (confirmado con el owner: no comparten refresh token) y su propia
política de agenda — de ahí que ToolConfig agrupe credenciales + agenda por
calendario, no solo las credenciales. Ese MISMO ToolConfig lo usan tanto
available-slots-* (consulta disponibilidad) como book-appointment-* (agenda
la cita) para ese calendario — son el mismo calendario de Google, dos tools
distintas que lo consultan.

El envío de correos de confirmación usa una cuenta de Gmail COMPARTIDA entre
IVF y SU (así estaba en los dos flujos de GHL: mismo credential id) — por
eso GmailConfig vive aparte, no dentro de cada ToolConfig.
"""

import os
import yaml
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

BUSINESS_TZ = "America/Bogota"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "slots.yaml"


@lru_cache
def load_slots_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"holidays": [], "policies": {}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class ToolConfig:
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    calendar_id: str
    duration_minutes: int
    gap_minutes: int
    days_ahead: int


def _load_tool_config(suffix: str) -> ToolConfig:
    return ToolConfig(
        google_client_id=os.getenv(f"FA_GOOGLE_CLIENT_ID_{suffix}", ""),
        google_client_secret=os.getenv(f"FA_GOOGLE_CLIENT_SECRET_{suffix}", ""),
        google_refresh_token=os.getenv(f"FA_GOOGLE_REFRESH_TOKEN_{suffix}", ""),
        calendar_id=os.getenv(f"FA_CALENDAR_ID_{suffix}", ""),
        duration_minutes=int(os.getenv(f"FA_DURATION_MINUTES_{suffix}", "30")),
        gap_minutes=int(os.getenv(f"FA_GAP_MINUTES_{suffix}", "0")),
        days_ahead=int(os.getenv(f"FA_DAYS_AHEAD_{suffix}", "14")),
    )


@dataclass
class GmailConfig:
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    sender_email: str


class Settings:
    def __init__(self) -> None:
        self.ivf = _load_tool_config("IVF")
        self.sur = _load_tool_config("SUR")
        self.gmail = GmailConfig(
            google_client_id=os.getenv("FA_GOOGLE_CLIENT_ID_GMAIL", ""),
            google_client_secret=os.getenv("FA_GOOGLE_CLIENT_SECRET_GMAIL", ""),
            google_refresh_token=os.getenv("FA_GOOGLE_REFRESH_TOKEN_GMAIL", ""),
            sender_email=os.getenv("FA_GMAIL_SENDER_EMAIL", ""),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
