import asyncio
import json
import logging

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from datetime import datetime, timedelta, timezone

from agents.family_aims_sam import derive_execution_id, endpoint
from agents.family_aims_sam import settings as sam_settings
from agents.family_aims_sam.models import SamRequest
from agents.helpers import ghl as shared_ghl
from agents.helpers import tools as shared_tools
from agents.helpers.history import apply_reset_marker, filter_recent_messages
from agents.helpers.runtime import build_conversation_messages, run_llm_tool_loop


def _tool_config_by_name(config):
    return {tool["name"]: tool for tool in config.get("tools", []) if tool.get("enabled", True)}


def _resolve_tool_webhook(tool):
    return shared_tools.resolve_tool_webhook(
        tool,
        get_local_app_base_url=lambda: "https://demo.ngrok-free.dev",
        tool_call_default_seconds=10,
    )


def _load_inventory(config):
    return shared_tools.load_tools_inventory(
        agent_settings=config,
        tool_inventory_path="",
        tool_config_by_name=_tool_config_by_name(config),
        tool_is_executable=lambda tool: shared_tools.tool_is_executable(tool, _resolve_tool_webhook),
        logger=logging.getLogger("agents.family_aims_sam.agent"),
    )


def test_wrapped_run_sam_agent_removes_execution_id_after_completion(monkeypatch):
    request = SamRequest(contactId="contact-1", locationId="location-1", message="Si")
    execution_id = derive_execution_id(request)

    async def _fake_run_sam_agent(_request):
        return None

    endpoint.processing_messages.clear()
    endpoint.processing_messages.add(execution_id)
    monkeypatch.setattr(endpoint, "run_sam_agent", _fake_run_sam_agent)

    asyncio.run(endpoint.wrapped_run_sam_agent(request))

    assert execution_id not in endpoint.processing_messages


def test_build_conversation_messages_appends_current_message_when_missing():
    history = [{"direction": "inbound", "body": "Hola"}]

    messages = build_conversation_messages("system prompt", history, "mensaje nuevo")

    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == "system prompt"
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "Hola"
    assert isinstance(messages[2], HumanMessage)
    assert messages[2].content == "mensaje nuevo"


_DEMO_TOOL = {
    "name": "demo_tool",
    "description": "Demo tool.",
    "parameters": [
        {
            "name": "mode",
            "type": "string",
            "required": True,
            "enum": ["fast", "safe"],
            "description": "Execution mode.",
        }
    ],
}


def test_run_llm_tool_loop_executes_tools_until_final_response():
    class _FakeResponse:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class _FakeLlm:
        def __init__(self):
            self.calls = []
            self.responses = iter(
                [
                    _FakeResponse(
                        content="",
                        tool_calls=[{"id": "tool-1", "name": "demo_tool", "args": {"mode": "safe"}}],
                    ),
                    _FakeResponse(content="final answer", tool_calls=[]),
                ]
            )

        async def ainvoke(self, messages):
            self.calls.append(list(messages))
            return next(self.responses)

    llm = _FakeLlm()
    tool_calls = []

    async def _fake_execute_tool(tool_name, args):
        tool_calls.append((tool_name, args))
        return {"success": True, "mode": args["mode"]}

    tools_by_name = {
        tool.name: tool for tool in shared_tools.build_structured_tools([_DEMO_TOOL], _fake_execute_tool)
    }

    result = asyncio.run(
        run_llm_tool_loop(
            llm=llm,
            conversation_messages=[SystemMessage(content="system prompt")],
            exec_id="exec-rt-1",
            max_tool_iterations=3,
            tools_by_name=tools_by_name,
            logger=logging.getLogger("agents.family_aims_sam.runtime"),
        )
    )

    assert result == "final answer"
    assert tool_calls == [("demo_tool", {"mode": "safe"})]
    assert any(isinstance(message, ToolMessage) for message in llm.calls[-1])


def test_run_llm_tool_loop_reports_invalid_tool_args_instead_of_crashing():
    class _FakeResponse:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class _FakeLlm:
        def __init__(self):
            self.responses = iter(
                [
                    _FakeResponse(
                        content="",
                        tool_calls=[{"id": "tool-1", "name": "demo_tool", "args": {"mode": "bogus"}}],
                    ),
                    _FakeResponse(content="pedile un modo valido", tool_calls=[]),
                ]
            )
            self.calls = []

        async def ainvoke(self, messages):
            self.calls.append(list(messages))
            return next(self.responses)

    llm = _FakeLlm()

    async def _fake_execute_tool(tool_name, args):
        raise AssertionError("the webhook should never be called for an invalid enum value")

    tools_by_name = {
        tool.name: tool for tool in shared_tools.build_structured_tools([_DEMO_TOOL], _fake_execute_tool)
    }

    result = asyncio.run(
        run_llm_tool_loop(
            llm=llm,
            conversation_messages=[SystemMessage(content="system prompt")],
            exec_id="exec-rt-2",
            max_tool_iterations=3,
            tools_by_name=tools_by_name,
            logger=logging.getLogger("agents.family_aims_sam.runtime"),
        )
    )

    assert result == "pedile un modo valido"
    tool_message = next(message for message in llm.calls[-1] if isinstance(message, ToolMessage))
    reported = json.loads(tool_message.content)
    assert reported["success"] is False
    assert "demo_tool" in reported["errors"]


def test_langchain_tool_schema_comes_from_agent_json():
    config = {
        "tools": [
            {
                "name": "demo_tool",
                "enabled": True,
                "route_mode": "local_proxy",
                "local_path": "/demo",
                "description": "Demo tool from agent json.",
                "triggerCondition": "Use this for demo flows.",
                "notes": ["Run this before making eligibility decisions."],
                "inputs": [
                    {
                        "name": "mode",
                        "required": True,
                        "description": "Mode expected by the downstream endpoint.",
                    }
                ],
                "outputs": [
                    {
                        "name": "success",
                        "required": True,
                        "description": "Whether the tool completed successfully.",
                    }
                ],
                "parameters": [
                    {
                        "name": "mode",
                        "type": "string",
                        "location": "body",
                        "required": True,
                        "description": "Execution mode.",
                        "enum": ["fast", "safe"],
                        "examples": ["safe"],
                    }
                ],
            }
        ],
    }

    inventory = _load_inventory(config)
    schemas = shared_tools.build_langchain_tool_schemas(inventory)

    assert len(schemas) == 1
    function_schema = schemas[0]["function"]
    assert function_schema["name"] == "demo_tool"
    assert function_schema["description"].count("Demo tool from agent json.") == 1
    assert "Use this for demo flows." in function_schema["description"]
    assert "Run this before making eligibility decisions." in function_schema["description"]
    assert "input mode (required): Mode expected by the downstream endpoint." in function_schema["description"]
    assert "output success (required): Whether the tool completed successfully." in function_schema["description"]
    mode_schema = function_schema["parameters"]["properties"]["mode"]
    assert mode_schema["enum"] == ["fast", "safe"]
    assert mode_schema["examples"] == ["safe"]
    assert function_schema["parameters"]["required"] == ["mode"]


def test_canonical_tool_schema_has_no_openai_envelope():
    config = {
        "tools": [
            {
                "name": "demo_tool",
                "enabled": True,
                "route_mode": "local_proxy",
                "local_path": "/demo",
                "description": "Demo tool from agent json.",
                "parameters": [
                    {
                        "name": "mode",
                        "type": "string",
                        "location": "body",
                        "required": True,
                        "description": "Execution mode.",
                        "enum": ["fast", "safe"],
                    }
                ],
            }
        ],
    }

    inventory = _load_inventory(config)
    canonical_schemas = shared_tools.build_canonical_tool_schemas(inventory)

    assert canonical_schemas == [
        {
            "name": "demo_tool",
            "description": canonical_schemas[0]["description"],
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Execution mode.",
                        "enum": ["fast", "safe"],
                    }
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
        }
    ]

    openai_schemas = shared_tools.convert_to_openai_tools(canonical_schemas)
    assert openai_schemas == shared_tools.build_langchain_tool_schemas(inventory)


def test_build_tool_args_model_enforces_enum_and_optional_default():
    tool = {
        "name": "demo_tool",
        "parameters": [
            {
                "name": "mode",
                "type": "string",
                "required": True,
                "enum": ["fast", "safe"],
                "description": "Execution mode.",
            },
            {
                "name": "note",
                "type": "string",
                "required": False,
                "description": "Optional note.",
            },
        ],
    }

    args_model = shared_tools.build_tool_args_model(tool)

    with pytest.raises(ValidationError):
        args_model(mode="bogus")

    instance = args_model(mode="safe")
    assert instance.mode == "safe"
    assert instance.note is None


def test_build_structured_tools_validates_and_dispatches_by_name():
    tool = {
        "name": "demo_tool",
        "description": "Demo tool.",
        "parameters": [
            {
                "name": "mode",
                "type": "string",
                "required": True,
                "enum": ["fast", "safe"],
                "description": "Execution mode.",
            }
        ],
    }

    calls = []

    async def _execute_tool(tool_name, args):
        calls.append((tool_name, args))
        return {"success": True, "echo": args}

    structured_tools = shared_tools.build_structured_tools([tool], _execute_tool)
    assert len(structured_tools) == 1
    assert structured_tools[0].name == "demo_tool"

    result = asyncio.run(structured_tools[0].ainvoke({"mode": "safe"}))

    assert result == {"success": True, "echo": {"mode": "safe"}}
    assert calls == [("demo_tool", {"mode": "safe"})]


def test_get_local_app_base_url_prefers_ngrok_base_url_from_config(monkeypatch):
    config = {
        "runtime": {
            "tool_base_url_env": "SAM_TOOL_BASE_URL",
            "public_base_url_env": "NGROK_BASE_URL",
            "default_host_env": "HOST",
            "default_port_env": "PORT",
            "default_host": "127.0.0.1",
            "default_port": "8010",
        }
    }

    monkeypatch.setattr(sam_settings, "get_agent_definition", lambda: config)
    monkeypatch.delenv("SAM_TOOL_BASE_URL", raising=False)
    monkeypatch.setenv("NGROK_BASE_URL", "https://demo.ngrok-free.dev/")

    assert sam_settings.get_local_app_base_url() == "https://demo.ngrok-free.dev"


def test_get_local_app_base_url_prefers_explicit_tool_base_url_over_ngrok(monkeypatch):
    config = {
        "runtime": {
            "tool_base_url_env": "SAM_TOOL_BASE_URL",
            "public_base_url_env": "NGROK_BASE_URL",
            "default_host_env": "HOST",
            "default_port_env": "PORT",
            "default_host": "127.0.0.1",
            "default_port": "8010",
        }
    }

    monkeypatch.setattr(sam_settings, "get_agent_definition", lambda: config)
    monkeypatch.setenv("SAM_TOOL_BASE_URL", "https://tools.example.com/")
    monkeypatch.setenv("NGROK_BASE_URL", "https://demo.ngrok-free.dev/")

    assert sam_settings.get_local_app_base_url() == "https://tools.example.com"


def test_log_tool_catalog_includes_resolved_webhook(caplog):
    config = {
        "tools": [
            {
                "name": "demo_tool",
                "enabled": True,
                "route_mode": "local_proxy",
                "local_path": "/demo",
                "description": "Demo tool from agent json.",
                "parameters": [
                    {
                        "name": "mode",
                        "type": "string",
                        "location": "body",
                        "required": True,
                        "description": "Execution mode.",
                    }
                ],
            }
        ],
    }

    inventory = _load_inventory(config)
    schemas = shared_tools.build_langchain_tool_schemas(inventory)

    with caplog.at_level(logging.INFO, logger="agents.family_aims_sam.agent"):
        shared_tools.log_tool_catalog(
            "exec-1",
            schemas,
            shared_tools.tool_registry_by_name(inventory),
            _resolve_tool_webhook,
            logging.getLogger("agents.family_aims_sam.agent"),
        )

    assert "webhook=https://demo.ngrok-free.dev/demo" in caplog.text


def test_execute_custom_tool_logs_resolved_webhook_at_runtime(caplog, monkeypatch):
    config = {
        "tools": [
            {
                "name": "demo_tool",
                "enabled": True,
                "route_mode": "local_proxy",
                "local_path": "/demo",
                "description": "Demo tool from agent json.",
                "parameters": [
                    {
                        "name": "mode",
                        "type": "string",
                        "location": "body",
                        "required": True,
                        "description": "Execution mode.",
                    }
                ],
            }
        ],
    }

    class _FakeResponse:
        headers = {"content-type": "application/json"}
        is_error = False
        status_code = 200

        @staticmethod
        def json():
            return {"success": True}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, json):
            return _FakeResponse()

    monkeypatch.setattr(shared_tools.httpx, "AsyncClient", _FakeClient)
    inventory = _load_inventory(config)

    with caplog.at_level(logging.INFO, logger="agents.family_aims_sam.agent"):
        result = asyncio.run(
            shared_tools.execute_custom_tool(
                "demo_tool",
                {"mode": "safe"},
                "exec-2",
                shared_tools.tool_registry_by_name(inventory),
                _resolve_tool_webhook,
                logging.getLogger("agents.family_aims_sam.agent"),
            )
        )

    assert result == {"success": True}
    assert "Tool demo_tool resolved webhook: method=POST url=https://demo.ngrok-free.dev/demo timeout_seconds=10.0 payload={'mode': 'safe'}" in caplog.text


def test_execute_custom_tool_logs_check_visa_payload_at_runtime(caplog, monkeypatch):
    config = {
        "tools": [
            {
                "name": "check_visa",
                "enabled": True,
                "route_mode": "local_proxy",
                "local_path": "/family_aims/v1/check-visa",
                "description": "Visa lookup.",
                "parameters": [
                    {
                        "name": "nationalities",
                        "type": "string",
                        "location": "body",
                        "required": False,
                        "description": "Nationalities to verify.",
                        "examples": ["Spain, VEN"],
                    }
                ],
            }
        ],
    }

    class _FakeResponse:
        headers = {"content-type": "application/json"}
        is_error = False
        status_code = 200

        @staticmethod
        def json():
            return {"success": True}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, json):
            return _FakeResponse()

    monkeypatch.setattr(shared_tools.httpx, "AsyncClient", _FakeClient)
    inventory = _load_inventory(config)

    with caplog.at_level(logging.INFO, logger="agents.family_aims_sam.agent"):
        result = asyncio.run(
            shared_tools.execute_custom_tool(
                "check_visa",
                {"nationalities": "Spain, VEN"},
                "exec-3",
                shared_tools.tool_registry_by_name(inventory),
                _resolve_tool_webhook,
                logging.getLogger("agents.family_aims_sam.agent"),
            )
        )

    assert result == {"success": True}
    assert "Tool check_visa resolved webhook: method=POST url=https://demo.ngrok-free.dev/family_aims/v1/check-visa timeout_seconds=10.0 payload={'nationalities': 'Spain, VEN'}" in caplog.text


def _message(direction, body, days_ago=0):
    date_added = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return {"direction": direction, "body": body, "dateAdded": date_added}


def test_filter_recent_messages_drops_older_than_max_days():
    history = [
        _message("inbound", "hace 10 dias", days_ago=10),
        _message("outbound", "hace 3 dias", days_ago=3),
        _message("inbound", "hoy", days_ago=0),
    ]

    result = filter_recent_messages(history, max_days=7)

    assert [m["body"] for m in result] == ["hace 3 dias", "hoy"]


def test_filter_recent_messages_no_limit_when_max_days_not_positive():
    history = [_message("inbound", "hace 100 dias", days_ago=100)]

    assert filter_recent_messages(history, max_days=0) == history
    assert filter_recent_messages(history, max_days=None) == history


def _ghl_client_config(history_max_days):
    return shared_ghl.GhlClientConfig(
        base_url="https://ghl.example.com",
        version="v3",
        token="token",
        message_history_page_size=100,
        conversation_search_limit=1,
        history_timeout_seconds=30,
        conversation_search_timeout_seconds=30,
        send_message_timeout_seconds=30,
        default_send_type="Live_Chat",
        channel_type_map={},
        history_max_days=history_max_days,
    )


def _page(messages, next_page, last_message_id):
    return {"messages": {"messages": messages, "nextPage": next_page, "lastMessageId": last_message_id}}


def _fake_ghl_pages_client(monkeypatch, pages):
    requested_urls = []

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            requested_urls.append(url)
            return _FakeResponse(pages[len(requested_urls) - 1])

    monkeypatch.setattr(shared_ghl.httpx, "AsyncClient", _FakeClient)
    return requested_urls


def test_fetch_messages_async_stops_paginating_once_past_history_window(monkeypatch):
    msg_a = _message("outbound", "a", days_ago=1)
    msg_a["id"] = "A"
    msg_b = _message("inbound", "b", days_ago=2)
    msg_b["id"] = "B"
    msg_c = _message("outbound", "c", days_ago=3)
    msg_c["id"] = "C"
    msg_d = _message("inbound", "d", days_ago=10)  # outside the 7-day window
    msg_d["id"] = "D"
    msg_e = _message("outbound", "e", days_ago=20)  # should never be fetched
    msg_e["id"] = "E"

    pages = [
        _page([msg_a, msg_b], next_page=True, last_message_id="B"),
        _page([msg_c, msg_d], next_page=True, last_message_id="D"),
        _page([msg_e], next_page=False, last_message_id=None),
    ]
    requested_urls = _fake_ghl_pages_client(monkeypatch, pages)

    result = asyncio.run(shared_ghl.fetch_messages_async("conv-1", _ghl_client_config(history_max_days=7)))

    assert len(requested_urls) == 2, "should stop after the page that first goes past the 7-day window"
    assert [m["id"] for m in result] == ["C", "B", "A"]


def test_fetch_messages_async_paginates_fully_when_no_history_limit(monkeypatch):
    msg_a = _message("outbound", "a", days_ago=1)
    msg_a["id"] = "A"
    msg_b = _message("inbound", "b", days_ago=100)
    msg_b["id"] = "B"

    pages = [
        _page([msg_a], next_page=True, last_message_id="A"),
        _page([msg_b], next_page=False, last_message_id=None),
    ]
    requested_urls = _fake_ghl_pages_client(monkeypatch, pages)

    result = asyncio.run(shared_ghl.fetch_messages_async("conv-1", _ghl_client_config(history_max_days=0)))

    assert len(requested_urls) == 2
    assert [m["id"] for m in result] == ["B", "A"]


def test_apply_reset_marker_discards_everything_before_last_marker():
    history = [
        _message("inbound", "hola", days_ago=5),
        _message("outbound", "hola, en que te ayudo", days_ago=5),
        _message("inbound", "</> hola de nuevo", days_ago=1),
        _message("outbound", "hola! empecemos de cero", days_ago=1),
        _message("inbound", "quiero agendar", days_ago=0),
    ]

    result = apply_reset_marker(history)

    assert [m["body"] for m in result] == ["hola de nuevo", "hola! empecemos de cero", "quiero agendar"]


def test_apply_reset_marker_uses_most_recent_marker():
    history = [
        _message("inbound", "</> primer intento", days_ago=5),
        _message("outbound", "respuesta 1", days_ago=5),
        _message("inbound", "</> segundo intento", days_ago=1),
        _message("outbound", "respuesta 2", days_ago=1),
    ]

    result = apply_reset_marker(history)

    assert result[0]["body"] == "segundo intento"


def test_apply_reset_marker_returns_history_unchanged_when_no_marker():
    history = [_message("inbound", "hola", days_ago=0)]

    assert apply_reset_marker(history) == history
