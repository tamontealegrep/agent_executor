import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Type

import httpx
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field, create_model


def load_tools_inventory(
    agent_settings: Dict[str, Any],
    tool_inventory_path: str,
    tool_config_by_name: Dict[str, Dict[str, Any]],
    tool_is_executable: Callable[[Dict[str, Any]], bool],
    logger: logging.Logger,
) -> List[Dict[str, Any]]:
    inventory_by_name: Dict[str, Dict[str, Any]] = {}
    if tool_inventory_path:
        tools_path = Path(tool_inventory_path)
        if tools_path.exists():
            raw = json.loads(tools_path.read_text(encoding="utf-8"))
            inventory_by_name = {tool["name"]: tool for tool in raw.get("tools", [])}
        else:
            logger.warning("tools inventory not found at %s", tools_path)

    executable_tools: List[Dict[str, Any]] = []
    for tool_name, tool_config in tool_config_by_name.items():
        merged = dict(inventory_by_name.get(tool_name, {}))
        merged.update(tool_config)
        merged.setdefault("name", tool_name)
        merged.setdefault("parameters", tool_config.get("parameters", []))
        merged.setdefault("webhook", tool_config.get("webhook", {}))
        merged["enabled"] = tool_config.get("enabled", True)
        merged["agent_config"] = tool_config
        if tool_is_executable(merged):
            executable_tools.append(merged)

    logger.info("Loaded %s executable tool(s) from agent.json", len(executable_tools))
    return executable_tools


def resolve_tool_webhook(
    tool: Dict[str, Any],
    get_local_app_base_url: Callable[[], str],
    tool_call_default_seconds: float,
) -> Optional[Dict[str, Any]]:
    tool_config = tool.get("agent_config") or {}
    route_mode = tool_config.get("route_mode")

    if route_mode == "builtin_ack":
        return None

    if route_mode == "local_proxy":
        local_path = (tool_config.get("local_path") or "").strip()
        if not local_path:
            return None
        timeout_ms = int(tool_call_default_seconds * 1000)
        inventory_webhook = tool.get("webhook") or {}
        return {
            "method": inventory_webhook.get("method") or "POST",
            "url": f"{get_local_app_base_url().rstrip('/')}{local_path}",
            "contentType": inventory_webhook.get("contentType") or "application/json",
            "authentication": inventory_webhook.get("authentication") or "none",
            "timeoutMs": inventory_webhook.get("timeoutMs") or timeout_ms,
        }

    webhook = dict(tool.get("webhook") or {})
    if webhook.get("url"):
        return webhook
    return None


def tool_is_executable(tool: Dict[str, Any], resolve_tool_webhook: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> bool:
    route_mode = (tool.get("agent_config") or {}).get("route_mode")
    if route_mode == "builtin_ack":
        return True
    return resolve_tool_webhook(tool) is not None


def json_schema_type(param_type: str) -> str:
    normalized = (param_type or "string").lower()
    if normalized in {"number", "float", "double"}:
        return "number"
    if normalized in {"integer", "int"}:
        return "integer"
    if normalized in {"boolean", "bool"}:
        return "boolean"
    if normalized in {"array", "list"}:
        return "array"
    if normalized in {"object", "dict", "json"}:
        return "object"
    return "string"


def tool_description(tool: Dict[str, Any]) -> str:
    parts: List[str] = []
    seen = set()

    def add_part(value: Optional[str]) -> None:
        text = str(value or "").strip()
        if not text or text in seen:
            return
        seen.add(text)
        parts.append(text)

    def add_named_fields(items: Any, label: str) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            required = item.get("required")
            prefix = f"{label} {name}".strip()
            if description and name:
                suffix = " (required)" if required else ""
                add_part(f"{prefix}{suffix}: {description}")
            elif description:
                add_part(description)

    tool_config = tool.get("agent_config") or {}
    add_part(tool_config.get("description"))
    add_part(tool.get("description"))
    add_part(tool.get("note"))
    add_part(tool.get("triggerCondition"))
    for note in tool.get("notes", []):
        add_part(note)
    for parameter in tool.get("parameters", []):
        description = parameter.get("description") or ""
        if description:
            add_part(f"{parameter['name']}: {description}")
    add_named_fields(tool.get("inputs"), "input")
    add_named_fields(tool.get("outputs"), "output")
    return "\n".join(parts) or tool.get("name", "")


def _build_parameters_json_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for parameter in tool.get("parameters", []):
        schema: Dict[str, Any] = {
            "type": json_schema_type(parameter.get("type", "string")),
            "description": parameter.get("description") or f"Argumento {parameter.get('name')}",
        }
        if parameter.get("enum"):
            schema["enum"] = parameter["enum"]
        if parameter.get("examples"):
            schema["examples"] = parameter["examples"]
        if parameter.get("default") is not None:
            schema["default"] = parameter["default"]
        properties[parameter["name"]] = schema
        if parameter.get("required"):
            required.append(parameter["name"])

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def build_canonical_tool_schemas(tools_inventory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Builds tool schemas in LangChain's provider-agnostic shape: a plain
    {name, description, parameters} dict per tool (no OpenAI "type"/"function"
    envelope). This is the shape every LangChain chat model's bind_tools()
    understands natively, so it's the right thing to keep around/inspect if we
    ever swap providers - only the final wire-format conversion changes.
    """
    return [
        {
            "name": tool["name"],
            "description": tool_description(tool),
            "parameters": _build_parameters_json_schema(tool),
        }
        for tool in tools_inventory
    ]


def convert_to_openai_tools(canonical_schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Wraps canonical {name, description, parameters} schemas into OpenAI's
    tool-call wire format. Kept as its own step (instead of baking the OpenAI
    envelope into the canonical builder) so a future provider swap only needs
    a different conversion here, not a different schema builder.
    """
    return [convert_to_openai_tool(schema) for schema in canonical_schemas]


def build_langchain_tool_schemas(tools_inventory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convenience wrapper: canonical LangChain schemas, already converted to
    the OpenAI wire format used by ChatOpenAI.bind_tools() and by the logging
    helpers below.
    """
    return convert_to_openai_tools(build_canonical_tool_schemas(tools_inventory))


_PYTHON_TYPE_BY_JSON_TYPE: Dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _python_type_for_parameter(parameter: Dict[str, Any]) -> Any:
    enum_values = parameter.get("enum")
    if enum_values:
        return Literal[tuple(enum_values)]
    return _PYTHON_TYPE_BY_JSON_TYPE[json_schema_type(parameter.get("type", "string"))]


def build_tool_args_model(tool: Dict[str, Any]) -> Type[BaseModel]:
    """Dynamically builds the Pydantic model StructuredTool uses as args_schema,
    straight from a tool's `parameters` list - same source of truth as
    `_build_parameters_json_schema`, just expressed as Python types instead of
    a raw JSON schema dict. `required: false` becomes `Optional[...]` with a
    default; an `enum` becomes a `Literal[...]`, so Pydantic itself rejects a
    stray value the model might hallucinate instead of that only being caught
    downstream by the webhook.
    """
    fields: Dict[str, Any] = {}
    for parameter in tool.get("parameters", []):
        python_type = _python_type_for_parameter(parameter)
        field_kwargs: Dict[str, Any] = {
            "description": parameter.get("description") or f"Argumento {parameter.get('name')}",
        }
        if parameter.get("examples"):
            field_kwargs["examples"] = list(parameter["examples"])
        if not parameter.get("required"):
            python_type = Optional[python_type]
            field_kwargs["default"] = parameter.get("default")
        fields[parameter["name"]] = (python_type, Field(**field_kwargs))

    model_name = "".join(part.title() for part in tool["name"].split("_")) + "Args"
    return create_model(model_name, **fields)


def build_structured_tools(
    tools_inventory: List[Dict[str, Any]],
    execute_tool: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
) -> List[StructuredTool]:
    """Builds real, executable LangChain tools: one `StructuredTool` per entry,
    with the Pydantic `args_schema` from `build_tool_args_model` (so LangChain
    validates/coerces arguments before anything runs) and a coroutine that
    calls back into `execute_tool`. This is the shape LangGraph's prebuilt
    agents/`ToolNode` expect - pass this list straight to `create_react_agent`
    or `ToolNode` instead of the dict schemas from `build_langchain_tool_schemas`.

    `execute_tool` takes `(tool_name, args)` only; close over anything else the
    caller needs (exec_id, logger, registry...) - see `execute_custom_tool` in
    this module for the underlying implementation.
    """
    tools: List[StructuredTool] = []
    for tool in tools_inventory:
        tool_name = tool["name"]

        async def _run(_tool_name: str = tool_name, **kwargs: Any) -> Dict[str, Any]:
            return await execute_tool(_tool_name, kwargs)

        tools.append(
            StructuredTool.from_function(
                name=tool_name,
                description=tool_description(tool),
                args_schema=build_tool_args_model(tool),
                coroutine=_run,
            )
        )
    return tools


def tool_registry_by_name(tools_inventory: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {tool["name"]: tool for tool in tools_inventory}


def build_tool_request_payload(tool: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for parameter in tool.get("parameters", []):
        if parameter.get("location") != "body":
            continue
        name = parameter["name"]
        if name in args and args[name] is not None:
            if tool.get("name") == "callback" and name == "iana_timezone":
                payload["timezone"] = args[name]
            else:
                payload[name] = args[name]
    return payload


def tool_result_summary(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"success": bool(result.get("success"))}
    if result.get("reason"):
        summary["reason"] = result.get("reason")
    if result.get("errors"):
        summary["has_errors"] = True
    if tool_name == "get_available_slots":
        summary["available_slots_count"] = len(result.get("available_slots", []) or [])
        if result.get("iana_timezone"):
            summary["iana_timezone"] = result.get("iana_timezone")
    if tool_name == "book_appointment":
        if result.get("event_id"):
            summary["event_id"] = result.get("event_id")
        if result.get("event_link"):
            summary["has_event_link"] = True
        if result.get("meet_link"):
            summary["has_meet_link"] = True
    return summary


async def execute_custom_tool(
    tool_name: str,
    args: Dict[str, Any],
    exec_id: str,
    registry: Dict[str, Dict[str, Any]],
    resolve_tool_webhook: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    logger: logging.Logger,
) -> Dict[str, Any]:
    tool = registry.get(tool_name)
    if tool is None:
        return {"success": False, "errors": f"Tool '{tool_name}' is not configured."}

    if tool_name == "end_call":
        logger.info("[%s] end_call acknowledged locally.", exec_id)
        return {"success": True, "message": "Conversation end acknowledged."}

    webhook = resolve_tool_webhook(tool) or {}
    url = webhook.get("url")
    method = (webhook.get("method") or "POST").upper()
    timeout_seconds = max((webhook.get("timeoutMs") or 10000) / 1000.0, 1.0)
    payload = build_tool_request_payload(tool, args)

    logger.info("[%s] Executing tool %s -> %s", exec_id, tool_name, url)
    logger.info(
        "[%s] Tool %s resolved webhook: method=%s url=%s timeout_seconds=%s payload=%s",
        exec_id,
        tool_name,
        method,
        url,
        timeout_seconds,
        payload,
    )
    logger.info("[%s] Tool %s payload: %s", exec_id, tool_name, payload)

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.request(method, url, json=payload)
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                body = response.json()
            else:
                body = {"raw": response.text}

            if response.is_error:
                logger.error("[%s] Tool %s failed with HTTP %s: %s", exec_id, tool_name, response.status_code, body)
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "errors": body,
                }

            result_body = body if isinstance(body, dict) else {"result": body}
            logger.info("[%s] Tool %s succeeded summary=%s", exec_id, tool_name, tool_result_summary(tool_name, result_body))
            return result_body
    except Exception as exc:
        logger.error("[%s] Tool %s raised an exception: %s", exec_id, tool_name, exc, exc_info=True)
        return {"success": False, "errors": str(exc)}


def log_tool_catalog(
    exec_id: str,
    tools: List[Dict[str, Any]],
    registry: Dict[str, Dict[str, Any]],
    resolve_tool_webhook: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    logger: logging.Logger,
) -> None:
    logger.info("[%s] Loaded tool catalog for LangChain:", exec_id)
    for index, tool in enumerate(tools, start=1):
        fn = tool.get("function", {})
        configured_tool = registry.get(fn.get("name", ""), {})
        webhook = resolve_tool_webhook(configured_tool) or {}
        logger.info(
            "[%s]   %s. %s params=%s webhook=%s",
            exec_id,
            index,
            fn.get("name"),
            sorted((fn.get("parameters", {}).get("properties") or {}).keys()),
            webhook.get("url"),
        )
