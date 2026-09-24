import logging
import httpx
from functools import lru_cache
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.tools import StructuredTool

from agents.family_aims_sam.models import SamRequest
from agents.family_aims_sam.settings import get_agent_definition, get_local_app_base_url
from agents.helpers.ghl import build_ghl_client_config
from agents.helpers.prompts import load_system_prompt
from agents.helpers.runner import GhlAgentContext, derive_execution_id as _derive_execution_id, run_ghl_agent
from agents.helpers.tools import (
    build_langchain_tool_schemas as shared_build_langchain_tool_schemas,
    build_structured_tools as shared_build_structured_tools,
    execute_custom_tool as shared_execute_custom_tool,
    log_tool_catalog as shared_log_tool_catalog,
    load_tools_inventory as shared_load_tools_inventory,
    resolve_tool_webhook as shared_resolve_tool_webhook,
    tool_is_executable as shared_tool_is_executable,
    tool_registry_by_name as shared_tool_registry_by_name,
)

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "Eres Sam, un asistente virtual de Family Aims."


def _agent_settings() -> Dict[str, Any]:
    return get_agent_definition()


def _ghl_settings() -> Dict[str, Any]:
    return _agent_settings().get("ghl", {})


def _model_settings() -> Dict[str, Any]:
    return _agent_settings().get("model", {})


def _runtime_settings() -> Dict[str, Any]:
    return _agent_settings().get("runtime", {})


def _messaging_settings() -> Dict[str, Any]:
    return _agent_settings().get("messaging", {})


def _paths_settings() -> Dict[str, Any]:
    return _agent_settings().get("paths", {})


def _tool_config_by_name() -> Dict[str, Dict[str, Any]]:
    return {
        tool["name"]: tool
        for tool in _agent_settings().get("tools", [])
        if tool.get("enabled", True)
    }


def _timeout_value(name: str, default: float) -> float:
    return float(_runtime_settings().get("timeouts", {}).get(name, default))


def _ghl_client_config() -> Any:
    return build_ghl_client_config(_ghl_settings(), _runtime_settings(), _messaging_settings())


def _load_system_prompt() -> str:
    return load_system_prompt(_paths_settings().get("system_prompt", ""), DEFAULT_SYSTEM_PROMPT)


def derive_execution_id(request: SamRequest) -> str:
    return _derive_execution_id(request, prefix="sam")


@lru_cache
def _load_tools_inventory() -> List[Dict[str, Any]]:
    return shared_load_tools_inventory(
        agent_settings=_agent_settings(),
        tool_inventory_path=_paths_settings().get("tool_inventory", ""),
        tool_config_by_name=_tool_config_by_name(),
        tool_is_executable=_tool_is_executable,
        logger=logger,
    )


def _tool_is_executable(tool: Dict[str, Any]) -> bool:
    return shared_tool_is_executable(tool, _resolve_tool_webhook)


def _resolve_tool_webhook(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return shared_resolve_tool_webhook(
        tool,
        get_local_app_base_url=get_local_app_base_url,
        tool_call_default_seconds=_timeout_value("tool_call_default_seconds", 10),
    )


@lru_cache
def _build_langchain_tool_schemas() -> List[Dict[str, Any]]:
    return shared_build_langchain_tool_schemas(_load_tools_inventory())


def _tool_registry_by_name() -> Dict[str, Dict[str, Any]]:
    return shared_tool_registry_by_name(_load_tools_inventory())


async def _execute_custom_tool(tool_name: str, args: Dict[str, Any], exec_id: str) -> Dict[str, Any]:
    return await shared_execute_custom_tool(
        tool_name=tool_name,
        args=args,
        exec_id=exec_id,
        registry=_tool_registry_by_name(),
        resolve_tool_webhook=_resolve_tool_webhook,
        logger=logger,
    )


def _log_tool_catalog(exec_id: str, tools: List[Dict[str, Any]]) -> None:
    shared_log_tool_catalog(exec_id, tools, _tool_registry_by_name(), _resolve_tool_webhook, logger)


def _build_structured_tools(
    execute_tool: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]
) -> List[StructuredTool]:
    return shared_build_structured_tools(_load_tools_inventory(), execute_tool)


async def run_sam_agent(request: SamRequest) -> None:
    """
    Main agent runner: wires Sam's settings/tools/prompt into the shared GHL agent flow.
    """
    exec_id = derive_execution_id(request)
    context = GhlAgentContext(
        ghl_client_config=_ghl_client_config(),
        model_settings=_model_settings(),
        webhook_timeout_seconds=_timeout_value("webhook_seconds", 30.0),
        logger=logger,
        load_system_prompt=_load_system_prompt,
        build_tool_schemas=_build_langchain_tool_schemas,
        log_tool_catalog=_log_tool_catalog,
        execute_custom_tool=_execute_custom_tool,
        build_structured_tools=_build_structured_tools,
    )
    await run_ghl_agent(request, exec_id, context)
