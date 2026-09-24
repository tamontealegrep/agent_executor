from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from agents.helpers.settings import load_agent_definition, resolve_local_app_base_url

PACKAGE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PACKAGE_DIR / "agent.json"


@lru_cache
def get_agent_definition() -> Dict[str, Any]:
    return load_agent_definition(CONFIG_PATH)


def get_local_app_base_url() -> str:
    return resolve_local_app_base_url(get_agent_definition())
