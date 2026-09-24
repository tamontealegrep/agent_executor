"""
Toda la configuración del agente vive aquí, leída una sola vez desde el .env
del root con prefijo NFS_. Nada más en el proyecto debe llamar a os.getenv
directamente.
"""

import os
import yaml
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

BUSINESS_TZ = "America/Bogota"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "slots.yaml"


@lru_cache
def load_slots_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"holidays": [], "windows": {}}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class GmailConfig:
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    sender_email: str


class Settings:
    def __init__(self) -> None:
        self.google_client_id = os.getenv("NFS_GOOGLE_CLIENT_ID", "")
        self.google_client_secret = os.getenv("NFS_GOOGLE_CLIENT_SECRET", "")
        self.google_refresh_token = os.getenv("NFS_GOOGLE_REFRESH_TOKEN", "")
        self.calendar_id = os.getenv("NFS_CALENDAR_ID", "primary")

        self.duration_minutes = int(os.getenv("NFS_DURATION_MINUTES", "30"))
        self.gap_minutes = int(os.getenv("NFS_GAP_MINUTES", "5"))
        self.days_ahead = int(os.getenv("NFS_DAYS_AHEAD", "14"))

        # book-appointment: comparte las credenciales de Calendar de arriba
        # (decisión del owner, 2026-08-26 — un solo calendario para todo en
        # este agente), pero necesita su propia identidad de Gmail (nueva,
        # este agente nunca había enviado correo) y la dirección interna del
        # contact center a la que se notifica cada cita agendada.
        self.contact_center_email = os.getenv("NFS_CONTACT_CENTER_EMAIL", "")
        self.gmail = GmailConfig(
            google_client_id=os.getenv("NFS_GOOGLE_CLIENT_ID_GMAIL", ""),
            google_client_secret=os.getenv("NFS_GOOGLE_CLIENT_SECRET_GMAIL", ""),
            google_refresh_token=os.getenv("NFS_GOOGLE_REFRESH_TOKEN_GMAIL", ""),
            sender_email=os.getenv("NFS_GMAIL_SENDER_EMAIL", ""),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
