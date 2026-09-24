"""
Hub interactivo para probar los tools de cualquier agente en src/.

Flujo: elegir agente (subcarpeta en src/tools/) -> elegir herramienta
(endpoint POST expuesto por ese agente) -> revisar/editar el payload por
defecto (derivado del schema Pydantic del endpoint) -> enviar la solicitud.

Uso:
    python scripts/send_request.py
    python scripts/send_request.py --target render --agent utils --tool time-now --payload '{"timezone":"America/Bogota"}'
    python scripts/send_request.py --target ngrok --agent utils --tool time-now --payload-file payload.json

Requiere que el servidor ya esté corriendo (python main.py) en el
host/puerto declarados en el .env del root.
"""

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import httpx
import questionary
from fastapi.routing import APIRoute
from pydantic import BaseModel
from rich.console import Console

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from discovery import agent_slug, discover_agents, get_hub_settings, load_agent_app

BASE_DIR = PROJECT_ROOT / "src"
TOOLS_DIR = BASE_DIR / "tools"

console = Console()

DEFAULT_REMOTE_BASE_URL = "https://ghl-agent-tools.onrender.com"
TARGET_LOCAL = "local"
TARGET_NGROK = "ngrok"
TARGET_RENDER = "render"
TARGET_CHOICES = [TARGET_LOCAL, TARGET_NGROK, TARGET_RENDER]

STYLE = questionary.Style([
    ("qmark", "fg:#00d7af bold"),
    ("question", "bold"),
    ("answer", "fg:#00d7af bold"),
    ("pointer", "fg:#00d7af bold"),
    ("highlighted", "fg:#00d7af bold"),
    ("selected", "fg:#00d7af bold"),
    ("separator", "fg:#6c6c6c"),
    ("instruction", "fg:#6c6c6c italic"),
])

SEND = "✓ Enviar tal cual"
RESET = "↺ Reset a valores por defecto"
BACK_TOOLS = "◀ Volver a herramientas"
BACK_AGENTS = "◀ Volver a agentes"
EXIT = "◀ Salir"


def _fresh_screen(breadcrumb: str = "") -> None:
    """Limpia la terminal y deja un encabezado fijo, para no terminar con un chorrero de scroll."""
    console.clear()
    console.print("[bold cyan]GHL Tools[/bold cyan] — hub interactivo")
    if breadcrumb:
        console.print(f"[dim]{breadcrumb}[/dim]")
    console.print()


class ToolInfo:
    def __init__(self, name: str, model: Type[BaseModel]):
        self.name = name
        self.model = model


def build_base_url(base_url: Optional[str], target: str = TARGET_LOCAL) -> str:
    if base_url:
        return base_url.rstrip("/")

    settings = get_hub_settings()

    if target == TARGET_RENDER:
        return settings.render_base_url.rstrip("/")

    if target == TARGET_NGROK:
        if settings.ngrok_base_url:
            return settings.ngrok_base_url.rstrip("/")
        if settings.ngrok_domain:
            return f"https://{settings.ngrok_domain}".rstrip("/")
        raise ValueError("Falta NGROK_BASE_URL o NGROK_DOMAIN en el .env")

    return f"http://{settings.host}:{settings.port}"


def normalize_target(target: Optional[str]) -> str:
    normalized = (target or "").strip().lower() or TARGET_RENDER
    if normalized not in TARGET_CHOICES:
        raise ValueError(f"Target inválido: '{target}'. Opciones: {', '.join(TARGET_CHOICES)}")
    return normalized


def build_tool_url(base_url: str, agent_package: str, tool_name: str) -> str:
    return f"{base_url}/{agent_slug(agent_package)}/v1/{tool_name}"


def normalize_agent_package(agent_input: str, available_agents: Dict[str, Path]) -> str:
    if agent_input in available_agents:
        return agent_input

    raise ValueError(
        f"Agente desconocido: '{agent_input}'. Opciones: {', '.join(available_agents.keys())}"
    )


def _find_request_model(endpoint) -> Optional[Type[BaseModel]]:
    """Busca, entre los parámetros del handler, el primero que sea un BaseModel de Pydantic."""
    for param in inspect.signature(endpoint).parameters.values():
        annotation = param.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    return None


def discover_tools(app) -> List[ToolInfo]:
    """Rutas POST del agente que reciben un body Pydantic (las herramientas invocables)."""
    tools = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "POST" not in route.methods:
            continue
        model = _find_request_model(route.endpoint)
        if model is None:
            continue
        name = route.path.rstrip("/").rsplit("/", 1)[-1]
        tools.append(ToolInfo(name=name, model=model))
    return tools


def default_payload(model: Type[BaseModel]) -> Dict[str, Any]:
    """Payload de prueba: usa el `examples` del schema si existe (valor de referencia
    para probar el tool), y si no cae al default real declarado (el que usa la API
    cuando el campo falta en una solicitud real)."""
    payload = {}
    for field_name, field in model.model_fields.items():
        if field.examples:
            payload[field_name] = field.examples[0]
        elif field.is_required():
            payload[field_name] = ""  # sin default ni ejemplo; el usuario debe completarlo
        elif field.default_factory is not None:
            payload[field_name] = field.default_factory()
        else:
            payload[field_name] = field.default
    return payload


def merge_payload(model: Type[BaseModel], payload_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = default_payload(model)
    if payload_data:
        payload.update(payload_data)
    return payload


def parse_payload_args(payload: Optional[str], payload_file: Optional[str]) -> Optional[Dict[str, Any]]:
    if payload and payload_file:
        raise ValueError("Usa solo una de estas opciones: --payload o --payload-file")

    raw_data = None
    if payload:
        raw_data = payload
    elif payload_file:
        raw_data = Path(payload_file).read_text(encoding="utf-8")

    if raw_data is None:
        return None

    data = json.loads(raw_data)
    if not isinstance(data, dict):
        raise ValueError("El payload debe ser un objeto JSON")
    return data


# ---------------------------------------------------------------------------
# Menús interactivos
# ---------------------------------------------------------------------------

def choose_agent(agent_names: List[str], notice: str = "") -> Optional[str]:
    _fresh_screen()
    if notice:
        console.print(f"[yellow]{notice}[/yellow]\n")
    choice = questionary.select(
        "Selecciona un agente:",
        choices=[*agent_names, questionary.Separator(), EXIT],
        style=STYLE,
    ).ask()
    return None if choice in (None, EXIT) else choice


def choose_target(default_target: str) -> Optional[str]:
    _fresh_screen()
    choice = questionary.select(
        "Selecciona el destino:",
        choices=[*TARGET_CHOICES, questionary.Separator(), EXIT],
        default=default_target,
        style=STYLE,
    ).ask()
    return None if choice in (None, EXIT) else choice


def choose_tool(target: str, agent_package: str, tool_names: List[str]) -> Optional[str]:
    _fresh_screen(f"Target: {target}  >  Agente: {agent_package}")
    choice = questionary.select(
        f"Herramientas de '{agent_package}':",
        choices=[*tool_names, questionary.Separator(), BACK_AGENTS],
        style=STYLE,
    ).ask()
    return None if choice in (None, BACK_AGENTS) else choice


def edit_payload(target: str, agent_package: str, tool_name: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Muestra el payload actual como lista seleccionable y permite editar campos hasta confirmar el envío."""
    original = dict(payload)
    current = dict(payload)

    while True:
        _fresh_screen(f"Target: {target}  >  Agente: {agent_package}  >  Herramienta: {tool_name}")
        field_choices = [questionary.Choice(title=f"{k} = {current[k]!r}", value=k) for k in current]
        choice = questionary.select(
            "Payload actual (elige un campo para editarlo, o una acción):",
            choices=[*field_choices, questionary.Separator(), SEND, RESET, BACK_TOOLS],
            style=STYLE,
        ).ask()

        if choice in (None, BACK_TOOLS):
            return None
        if choice == SEND:
            return current
        if choice == RESET:
            current = dict(original)
            continue

        current_value = current[choice]
        new_value = questionary.text(
            f"Nuevo valor para '{choice}' (vacío = null):",
            default="" if current_value is None else str(current_value),
            style=STYLE,
        ).ask()
        if new_value is None:
            continue
        current[choice] = new_value if new_value.strip() != "" else None


# ---------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------

def send_request(url: str, payload: Dict[str, Any]) -> int:
    console.print(f"\n[bold cyan]POST[/bold cyan] {url}")
    console.print("[dim]Body enviado:[/dim]")
    console.print_json(data=payload)

    try:
        response = httpx.post(url, json=payload, timeout=30)
    except httpx.ConnectError:
        console.print("[bold red]ERROR:[/bold red] no se pudo conectar al servidor.")
        console.print("¿Está corriendo el servidor (python main.py)?")
        return 1
    except Exception as e:
        console.print(f"[bold red]ERROR inesperado al hacer la solicitud:[/bold red] {e}")
        return 1

    status_color = "green" if response.status_code < 400 else "red"
    console.print(f"\n[bold {status_color}]Status code: {response.status_code}[/bold {status_color}]\n")

    try:
        data = response.json()
        console.print_json(data=data)
        if not data.get("success"):
            console.print(f"\n[bold yellow]AVISO:[/bold yellow] la API respondió success=false. Error: {data.get('errors')}")
    except ValueError:
        console.print("[red]La respuesta no es JSON válido. Contenido crudo:[/red]")
        console.print(response.text)

    return 0 if response.status_code < 400 else 1


# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------

def run_tool_loop(target: str, agent_package: str) -> None:
    try:
        app = load_agent_app(agent_package)
    except Exception as e:
        console.print(f"[bold red]ERROR:[/bold red] no se pudo cargar el agente '{agent_package}': {e}")
        return

    tools = discover_tools(app)
    if not tools:
        console.print(f"'{agent_package}' no expone herramientas POST con un modelo de request.")
        return

    tool_by_name = {t.name: t for t in tools}

    while True:
        tool_name = choose_tool(target, agent_package, list(tool_by_name.keys()))
        if tool_name is None:
            return
        tool = tool_by_name[tool_name]

        while True:
            payload = edit_payload(target, agent_package, tool.name, default_payload(tool.model))
            if payload is None:
                break
            url = build_tool_url(build_base_url(None, target), agent_package, tool.name)
            send_request(url, payload)

            again = questionary.confirm(
                "¿Enviar otra solicitud a esta misma herramienta?", default=False, style=STYLE
            ).ask()
            if not again:
                break


def print_agents_and_tools(agents: Dict[str, Path]) -> None:
    for agent_package in agents:
        app = load_agent_app(agent_package)
        tool_names = [tool.name for tool in discover_tools(app)]
        console.print(f"[bold cyan]{agent_package}[/bold cyan]")
        for tool_name in tool_names:
            console.print(f"  - {tool_name}")
        console.print()


def run_cli(args: argparse.Namespace) -> int:
    agents = discover_agents()
    if not agents:
        console.print(f"No se encontraron subproyectos en {TOOLS_DIR} con main.py")
        return 1

    if args.list:
        print_agents_and_tools(agents)
        return 0

    if not args.agent or not args.tool:
        console.print("[bold red]ERROR:[/bold red] en modo CLI debes indicar --agent y --tool")
        return 1

    try:
        target = normalize_target(args.target)
        agent_package = normalize_agent_package(args.agent, agents)
        app = load_agent_app(agent_package)
        tool_by_name = {tool.name: tool for tool in discover_tools(app)}
        tool = tool_by_name.get(args.tool)
        if tool is None:
            raise ValueError(
                f"Herramienta desconocida: '{args.tool}'. Opciones: {', '.join(tool_by_name.keys())}"
            )

        payload_data = parse_payload_args(args.payload, args.payload_file)
        payload = merge_payload(tool.model, payload_data)
        url = build_tool_url(build_base_url(args.base_url, target), agent_package, tool.name)
        return send_request(url, payload)
    except FileNotFoundError as e:
        console.print(f"[bold red]ERROR:[/bold red] no se encontró el archivo de payload: {e.filename}")
        return 1
    except json.JSONDecodeError as e:
        console.print(f"[bold red]ERROR:[/bold red] payload JSON inválido: {e}")
        return 1
    except ValueError as e:
        console.print(f"[bold red]ERROR:[/bold red] {e}")
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba endpoints POST de los agentes GHL")
    parser.add_argument("--base-url", help=f"URL base del servicio. Ejemplo: {DEFAULT_REMOTE_BASE_URL}")
    parser.add_argument(
        "--target",
        choices=[TARGET_LOCAL, TARGET_NGROK, TARGET_RENDER],
        default=TARGET_LOCAL,
        help="Atajo de destino usando variables del .env",
    )
    parser.add_argument("--agent", help="Slug del agente o nombre de paquete. Ejemplo: utils o tools.utils")
    parser.add_argument("--tool", help="Nombre del endpoint POST. Ejemplo: time-now")
    parser.add_argument("--payload", help="Payload JSON inline")
    parser.add_argument("--payload-file", help="Ruta a un archivo JSON con el payload")
    parser.add_argument("--list", action="store_true", help="Lista agentes y tools disponibles")
    return parser.parse_args()


def main():
    args = parse_args()
    if any([args.base_url, args.agent, args.tool, args.payload, args.payload_file, args.list]) or args.target != TARGET_LOCAL:
        sys.exit(run_cli(args))

    agents = discover_agents()
    if not agents:
        console.print(f"No se encontraron subproyectos en {TOOLS_DIR} con main.py")
        sys.exit(1)

    agent_names = list(agents.keys())
    settings = get_hub_settings()

    try:
        while True:
            target = choose_target(normalize_target(settings.send_request_default_target))
            if target is None:
                console.print("Hasta luego.")
                return
            agent_package = choose_agent(agent_names)
            if agent_package is None:
                console.print("Hasta luego.")
                return
            run_tool_loop(target, agent_package)
    except (KeyboardInterrupt, EOFError):
        console.print("\nHasta luego.")


if __name__ == "__main__":
    main()
