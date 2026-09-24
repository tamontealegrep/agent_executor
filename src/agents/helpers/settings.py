import json
import os
from pathlib import Path
from typing import Any, Dict


def _resolve_path(package_dir: Path, path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        path = (package_dir / path).resolve()
    return str(path)


def load_agent_definition(config_path: Path) -> Dict[str, Any]:
    """Loads an agent.json and resolves its `paths` entries relative to its own directory."""
    package_dir = config_path.resolve().parent
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    paths = raw.setdefault("paths", {})
    if "system_prompt" in paths:
        paths["system_prompt"] = _resolve_path(package_dir, paths["system_prompt"])
    if "tool_inventory" in paths:
        paths["tool_inventory"] = _resolve_path(package_dir, paths["tool_inventory"])
    return raw


def resolve_local_app_base_url(agent_definition: Dict[str, Any]) -> str:
    runtime = agent_definition.get("runtime", {})
    explicit = os.getenv(runtime.get("tool_base_url_env", "AGENT_TOOL_BASE_URL"), "").strip()
    if explicit:
        return explicit.rstrip("/")

    public_base_url_env = runtime.get("public_base_url_env", "")
    if public_base_url_env:
        public_base_url = os.getenv(public_base_url_env, "").strip()
        if public_base_url:
            return public_base_url.rstrip("/")

    host = os.getenv(runtime.get("default_host_env", "HOST"), runtime.get("default_host", "127.0.0.1"))
    port = os.getenv(runtime.get("default_port_env", "PORT"), runtime.get("default_port", "8010"))

    return f"http://{host}:{port}"
