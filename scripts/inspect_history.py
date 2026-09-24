"""Inspecciona el historial de conversacion que Sam usaria para un payload de GHL dado.

Corre el mismo pipeline que usa el agente en produccion (resolver conversation_id,
traer el historial real de GHL, aplicar la ventana de dias y el marcador de reset
"</>") sin invocar al LLM ni mandar nada a GHL ni ejecutar ninguna tool. Sirve para
ver exactamente que va a ver Sam antes de que responda, con el mismo payload que
llega a /family_aims/v1/sam.

Uso:
    python scripts/inspect_history.py --payload '{"contactId": "...", "locationId": "..."}'
    python scripts/inspect_history.py --payload-file payload.json
    python scripts/inspect_history.py --payload-file payload.json --show-messages
    cat payload.json | python scripts/inspect_history.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for candidate in (PROJECT_ROOT, SRC_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from discovery import get_hub_settings  # el import ya carga el .env del root
from agents.family_aims_sam import agent as sam_agent
from agents.family_aims_sam.models import SamRequest
from agents.helpers.ghl import fetch_messages_async, search_conversation_async
from agents.helpers.history import DEFAULT_RESET_MARKER, apply_reset_marker
from agents.helpers.prompts import prepare_system_prompt
from agents.helpers.runtime import build_conversation_messages

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Muestra el historial de conversacion que Sam usaria para un payload de GHL dado"
    )
    parser.add_argument("--payload", help="Payload JSON inline (el mismo que le llega a /sam)")
    parser.add_argument("--payload-file", help="Ruta a un archivo JSON con el payload")
    parser.add_argument(
        "--show-messages",
        action="store_true",
        help="Ademas del historial crudo, muestra los mensajes de LangChain (system/human/ai) que se le mandarian al LLM",
    )
    parser.add_argument("--json", action="store_true", help="Imprime el resultado como JSON en vez de tablas")
    return parser.parse_args()


def load_payload(args: argparse.Namespace) -> Dict[str, Any]:
    if args.payload and args.payload_file:
        raise ValueError("Usa solo una de estas opciones: --payload o --payload-file")

    if args.payload:
        raw = args.payload
    elif args.payload_file:
        raw = Path(args.payload_file).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError("No se recibio payload. Usa --payload, --payload-file, o mandalo por stdin.")

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("El payload debe ser un objeto JSON")
    return data


async def inspect(payload: Dict[str, Any]) -> Dict[str, Any]:
    request = SamRequest.model_validate(payload)
    ghl_client_config = sam_agent._ghl_client_config()

    conv_id = request.conversation_id
    resolved_by_search = False
    if not conv_id:
        resolved_by_search = True
        conv_id = await search_conversation_async(request.contact_id, request.location_id, ghl_client_config)
        if not conv_id:
            raise RuntimeError("No se encontro conversacion para este contact_id/location_id en GHL.")

    ghl_history = await fetch_messages_async(conv_id, ghl_client_config)
    history = apply_reset_marker(ghl_history, DEFAULT_RESET_MARKER)

    current_message = request.message
    if isinstance(current_message, str) and current_message.strip().startswith(DEFAULT_RESET_MARKER):
        current_message = current_message.strip()[len(DEFAULT_RESET_MARKER):].strip()
    if not current_message and history:
        inbound = [m for m in history if m.get("direction") == "inbound"]
        if inbound:
            current_message = inbound[-1].get("body")

    return {
        "conversation_id": conv_id,
        "resolved_by_search": resolved_by_search,
        "history_max_days": ghl_client_config.history_max_days,
        "ghl_message_count": len(ghl_history),
        "final_message_count": len(history),
        "reset_marker_applied": len(history) != len(ghl_history),
        "channel": request.channel,
        "contact": request.contact,
        "current_message": current_message,
        "history": history,
        "request": request,
    }


def render_summary(result: Dict[str, Any]) -> None:
    lines = [
        f"conversation_id: {result['conversation_id']}" + (" (resuelto por busqueda)" if result["resolved_by_search"] else ""),
        f"history_max_days: {result['history_max_days']}",
        f"mensajes de GHL (ya con ventana de dias aplicada): {result['ghl_message_count']}",
        f"mensajes finales para el LLM: {result['final_message_count']}",
    ]
    if result["reset_marker_applied"]:
        lines.append(f"[yellow]marcador '{DEFAULT_RESET_MARKER}' detectado — se descarto historial anterior[/yellow]")
    lines.append(f"canal: {result['channel']}")
    lines.append(f"contact: {result['contact']}")
    lines.append(f"mensaje actual resuelto: {result['current_message']!r}")
    console.print(Panel.fit("\n".join(lines), title="Resumen", border_style="cyan"))


def render_history_table(history: list) -> None:
    table = Table(title="Historial que usaria el agente")
    table.add_column("#", style="dim", width=4)
    table.add_column("Fecha", style="dim")
    table.add_column("Direccion")
    table.add_column("Mensaje")
    for index, message in enumerate(history, start=1):
        direction = message.get("direction", "?")
        style = "green" if direction == "inbound" else "magenta"
        table.add_row(
            str(index),
            str(message.get("dateAdded", "")),
            f"[{style}]{direction}[/{style}]",
            str(message.get("body", "")),
        )
    console.print(table)


def render_langchain_messages(result: Dict[str, Any]) -> None:
    raw_system_prompt = sam_agent._load_system_prompt()
    system_prompt = prepare_system_prompt(raw_system_prompt, result["request"])
    lc_messages = build_conversation_messages(system_prompt, result["history"], str(result["current_message"] or ""))

    console.print(
        Panel.fit(
            f"{len(system_prompt)} caracteres (placeholders {{{{contact.*}}}} ya resueltos)",
            title="System prompt",
            border_style="yellow",
        )
    )
    for index, message in enumerate(lc_messages[1:], start=1):
        console.print(Panel.fit(str(message.content), title=f"{index}. {message.__class__.__name__}", border_style="blue"))


def main() -> None:
    get_hub_settings()  # fuerza a que .env ya este cargado antes de leer GHL_TOKEN, etc.
    args = parse_args()

    try:
        payload = load_payload(args)
        result = asyncio.run(inspect(payload))
    except Exception as exc:
        console.print(Panel.fit(str(exc), title="Error", border_style="red"))
        raise SystemExit(1)

    if args.json:
        output = {key: value for key, value in result.items() if key != "request"}
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        return

    render_summary(result)
    render_history_table(result["history"])
    if args.show_messages:
        render_langchain_messages(result)


if __name__ == "__main__":
    main()
