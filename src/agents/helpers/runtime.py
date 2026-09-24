import json
import logging
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from agents.helpers.history import ghl_history_to_langchain


def build_conversation_messages(system_prompt: str, history: List[Dict[str, Any]], current_message: str) -> List[Any]:
    messages: List[Any] = [SystemMessage(content=system_prompt)]
    messages.extend(ghl_history_to_langchain(history))

    if not any(
        str(current_message) == str(message.get("body"))
        for message in history
        if message.get("direction") == "inbound"
    ):
        messages.append(HumanMessage(content=str(current_message)))

    return messages


async def run_llm_tool_loop(
    llm: Any,
    conversation_messages: List[Any],
    exec_id: str,
    max_tool_iterations: int,
    tools_by_name: Dict[str, StructuredTool],
    logger: logging.Logger,
) -> str:
    """Runs the model/tool loop: invokes the LLM, executes any requested tool
    calls via their StructuredTool (which validates args against its Pydantic
    args_schema before anything runs), feeds the results back, and repeats
    until the model answers without requesting a tool (or max_tool_iterations
    is reached).
    """
    running_messages = list(conversation_messages)
    response_text = None

    for iteration in range(1, max_tool_iterations + 1):
        logger.info("[%s] Invoking LLM iteration %s...", exec_id, iteration)
        response = await llm.ainvoke(running_messages)
        running_messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []

        if not tool_calls:
            response_text = response.content if isinstance(response.content, str) else str(response.content)
            logger.info("[%s] LLM produced final response without additional tools.", exec_id)
            break

        logger.info("[%s] LLM requested %s tool call(s).", exec_id, len(tool_calls))
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")
            tool_args = tool_call.get("args", {}) or {}
            logger.info("[%s] Tool requested: %s args=%s", exec_id, tool_name, tool_args)

            tool = tools_by_name.get(tool_name)
            if tool is None:
                logger.warning("[%s] Model requested unknown tool '%s'.", exec_id, tool_name)
                tool_result: Dict[str, Any] = {"success": False, "errors": f"Tool '{tool_name}' is not configured."}
            else:
                try:
                    tool_result = await tool.ainvoke(tool_args)
                except ValidationError as exc:
                    logger.warning("[%s] Tool %s received invalid arguments: %s", exec_id, tool_name, exc)
                    tool_result = {"success": False, "errors": f"Invalid arguments for {tool_name}: {exc}"}

            running_messages.append(
                ToolMessage(
                    content=json.dumps(tool_result, ensure_ascii=True),
                    tool_call_id=tool_call["id"],
                )
            )

    if response_text is None:
        raise RuntimeError("The model did not produce a final response after the tool loop.")

    return response_text
