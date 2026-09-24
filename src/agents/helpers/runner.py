import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from agents.helpers.ghl import GhlClientConfig, fetch_messages_async, search_conversation_async, send_ghl_message_async
from agents.helpers.ghl_request import GhlAgentRequest
from agents.helpers.history import DEFAULT_RESET_MARKER, apply_reset_marker
from agents.helpers.prompts import prepare_system_prompt
from agents.helpers.runtime import build_conversation_messages, run_llm_tool_loop
from agents.helpers.webhooks import send_webhook_response

# TODO(tool-choice-auditor): removed 2026-09-15 as part of the canonical-schema
# refactor (agent.json no longer has a per-tool "critical" flag). If a model
# keeps answering with confirmations/data it never actually fetched, replicate
# this instead of hand-rolling something new. The previous implementation
# (see git history around b5d5555/adc4ccc) worked like this:
#   - agent.json tools could carry "critical": true (booking, availability,
#     visa checks, etc. - anything whose output the model must not fabricate).
#   - After a turn where the model answered without calling any tool, a
#     second LLM call ("auditor") got the conversation + the model's draft
#     text + the name/description of only the critical tools, and had to
#     answer with either NONE or the exact name of the tool that should have
#     run first.
#   - If the auditor named a tool, the loop forced it via
#     llm.bind(tool_choice={"type": "function", "function": {"name": ...}}),
#     re-invoked the model, and used that forced turn's result instead of the
#     unaudited draft.
#   - Audited at most once per turn (cost control), gated by an env flag
#     (agent.json: model.audit_tool_usage / model.audit_tool_usage_env).
# Known limitations before removal, still true if this comes back: it forces
# *which* tool runs but not that its arguments are correct; it only catches
# one missed tool per turn; it adds 1-3 extra OpenAI calls on any tool-free
# turn. See TODO.md for the full write-up.


def derive_execution_id(request: GhlAgentRequest, prefix: str) -> str:
    if request.messageId:
        return request.messageId
    if request.conversation_id:
        return request.conversation_id

    fingerprint_source = f"{request.contact_id}|{request.location_id or ''}|{request.message or ''}"
    fingerprint = hashlib.sha1(fingerprint_source.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{fingerprint}"


@dataclass
class GhlAgentContext:
    """Everything a specific agent (model, tools, prompt) plugs into the shared GHL flow."""

    ghl_client_config: GhlClientConfig
    model_settings: Dict[str, Any]
    webhook_timeout_seconds: float
    logger: logging.Logger
    load_system_prompt: Callable[[], str]
    build_tool_schemas: Callable[[], List[Dict[str, Any]]]
    log_tool_catalog: Callable[[str, List[Dict[str, Any]]], None]
    execute_custom_tool: Callable[[str, Dict[str, Any], str], Awaitable[Dict[str, Any]]]
    build_structured_tools: Callable[
        [Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]]], List[StructuredTool]
    ]


async def run_ghl_agent(request: GhlAgentRequest, exec_id: str, context: GhlAgentContext) -> None:
    """Shared GHL webhook -> LLM -> GHL reply flow reused by every agent.

    Resolves the conversation, fetches history, prepares the prompt, runs the
    LangChain tool loop and sends the response back to GHL (plus an optional
    webhook notification). Only the model/tooling/prompt come from `context`;
    everything else about the GHL payload and messaging flow is identical
    across agents.
    """
    logger = context.logger
    try:
        raw_system_prompt = context.load_system_prompt()
        logger.info(f"[{exec_id}] System prompt loaded.")

        conv_id = request.conversation_id
        if not conv_id:
            logger.info(f"[{exec_id}] Conversation ID missing, searching...")
            conv_id = await search_conversation_async(
                request.contact_id, request.location_id, context.ghl_client_config, logger
            )
            if not conv_id:
                raise ValueError("Could not find conversation for this contact.")
            logger.info(f"[{exec_id}] Found conversation: {conv_id}")

        logger.info(f"[{exec_id}] Fetching GHL history...")
        history = await fetch_messages_async(conv_id, context.ghl_client_config)
        history = apply_reset_marker(history, DEFAULT_RESET_MARKER)
        logger.info(f"[{exec_id}] Fetched {len(history)} messages (after recency/reset filters).")

        current_message = request.message
        if isinstance(current_message, str) and current_message.strip().startswith(DEFAULT_RESET_MARKER):
            current_message = current_message.strip()[len(DEFAULT_RESET_MARKER):].strip()

        if not current_message and history:
            inbound_msgs = [m for m in history if m.get("direction") == "inbound"]
            if inbound_msgs:
                current_message = inbound_msgs[-1].get("body")
                logger.info("[%s] Using last inbound message from history.", exec_id)

        if not current_message:
            raise ValueError("No message to process.")

        system_prompt = prepare_system_prompt(raw_system_prompt, request)
        logger.info(f"[{exec_id}] System prompt prepared.")

        logger.info(f"[{exec_id}] Configuring LangChain...")
        tool_schemas = context.build_tool_schemas()
        context.log_tool_catalog(exec_id, tool_schemas)

        model_settings = context.model_settings
        base_llm = ChatOpenAI(
            model=model_settings.get("name", "gpt-4o-mini"),
            temperature=model_settings.get("temperature", 0),
            api_key=os.getenv(model_settings.get("api_key_env", "OPENAI_API_KEY")),
        )
        llm = base_llm.bind_tools(tool_schemas)

        lc_messages = build_conversation_messages(system_prompt, history, str(current_message))
        logger.info(f"[{exec_id}] LangChain configured with {len(lc_messages)} messages.")

        async def _execute_tool_for_turn(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            return await context.execute_custom_tool(tool_name, args, exec_id)

        structured_tools = context.build_structured_tools(_execute_tool_for_turn)
        tools_by_name = {tool.name: tool for tool in structured_tools}

        response_text = await run_llm_tool_loop(
            llm=llm,
            conversation_messages=lc_messages,
            exec_id=exec_id,
            max_tool_iterations=int(model_settings.get("max_tool_iterations", 5)),
            tools_by_name=tools_by_name,
            logger=logger,
        )
        logger.info(f"[{exec_id}] LLM response received.")

        ghl_send_result = await send_ghl_message_async(
            contact_id=request.contact_id,
            message=response_text,
            channel=request.channel,
            exec_id=exec_id,
            config=context.ghl_client_config,
            location_id=request.location_id,
            reply_message_id=request.messageId,
            logger=logger,
        )
        logger.info("[%s] GHL send result keys: %s", exec_id, sorted(ghl_send_result.keys()))

        if request.webhook_url:
            logger.info(f"[{exec_id}] Sending response to webhook: {request.webhook_url}")
            await send_webhook_response(
                request.webhook_url,
                {
                    "messageId": exec_id,
                    "conversation_id": conv_id,
                    "response": response_text,
                    "ghl_send_result": ghl_send_result,
                    "status": "completed",
                },
                timeout_seconds=context.webhook_timeout_seconds,
                logger=logger,
            )
        else:
            logger.info("[%s] Agent finished successfully after LLM flow.", exec_id)

    except Exception as e:
        logger.error(f"[{exec_id}] Error in agent execution: {e}", exc_info=True)
        if request.webhook_url:
            await send_webhook_response(
                request.webhook_url,
                {
                    "messageId": exec_id,
                    "conversation_id": request.conversation_id or "unknown",
                    "error": str(e),
                    "status": "failed",
                },
                timeout_seconds=context.webhook_timeout_seconds,
                logger=logger,
            )
