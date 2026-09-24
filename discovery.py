"""
Descubre y carga los agentes (subproyectos en src/tools/) y expone la
configuración compartida leída del .env del root del proyecto.
"""

import importlib
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
BASE_DIR = ROOT_DIR / "src"
TOOLS_DIR = BASE_DIR / "tools"

load_dotenv(ROOT_DIR / ".env")


class HubSettings:
    def __init__(self) -> None:
        self.host = os.getenv("HOST", "127.0.0.1")
        self.port = int(os.getenv("PORT", "8010"))
        self.send_request_default_target = os.getenv("SEND_REQUEST_DEFAULT_TARGET", "render")
        self.ngrok_token = os.getenv("NGROK_TOKEN", "")
        self.ngrok_domain = os.getenv("NGROK_DOMAIN", "")
        self.ngrok_base_url = os.getenv("NGROK_BASE_URL", "")
        self.render_base_url = os.getenv("RENDER_BASE_URL", "https://ghl-agent-tools.onrender.com")


@lru_cache
def get_hub_settings() -> HubSettings:
    return HubSettings()


def discover_agents() -> Dict[str, Path]:
    """Subcarpetas en src/tools/ que tienen un main.py."""
    tools = {}
    if not TOOLS_DIR.exists():
        return tools
    for entry in sorted(TOOLS_DIR.iterdir()):
        if entry.is_dir() and (entry / "main.py").exists():
            tools[entry.name] = entry
    return tools


def agent_slug(agent_package: str) -> str:
    return agent_package


def load_agent_app(agent_package: str):
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    module = importlib.import_module(f"tools.{agent_package}.main")
    return module.app


def load_agent_router(agent_package: str):
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    module = importlib.import_module(f"tools.{agent_package}.api.v1.router")
    return module.api_router
