"""Launcher TUI para probar cualquier agente compilado con agent_compiler y
servido via agent_runtime (compiled_agents/<slug>/graph.json).

A diferencia de sam_tui.py (que es especifico del agente family_aims_sam,
hecho a mano), esta herramienta descubre automaticamente que agentes hay
compilados bajo compiled_agents/ y sirve a cualquiera de ellos con el mismo
loop de chat: no hay nada de family_aims/Sam hardcodeado aca.

Corre el grafo LangGraph directamente en proceso (misma forma que
compiled_runner/endpoint.py, sin pasar por HTTP) para poder mostrar en la
terminal, turno a turno: la respuesta hablada, el estado interno
(current_state) y cada tool call real con sus argumentos y su respuesta.

El estado de la conversacion es el checkpoint real de LangGraph
(compiled_agents/<slug>/sessions.db, SqliteSaver) -- el mismo archivo que
usa el endpoint HTTP, asi que podes arrancar una conversacion aca y
seguirla por HTTP (o al reves) usando el mismo thread_id. El historial que
se ve en pantalla (para /history) es un log local aparte, en
backups/tui_sessions/, solo para mostrarlo -- no es la fuente de verdad
del flujo.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import questionary
from dotenv import load_dotenv
from langgraph.types import Command
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_here = Path(__file__).resolve()
# Robust root finder: Busca hacia arriba la carpeta que contenga 'agents/compiled'
def _find_project_root(start=_here):
    for p in [_here] + list(_here.parents):
        ac = p / "agents" / "compiled"
        if ac.exists():
            return p
    # Fallback original logic (por si la estructura cambia en el futuro)
    return _here.parents[1]

PROJECT_ROOT = _find_project_root()
# Deliberately PROJECT_ROOT/"src" (the workspace root's, which doesn't
# exist) and not agent_executor/src -- the latter now also holds the
# vendored, frozen copy of agent_compiler (see src/agent_compiler/'s own
# history, vendored via git subtree from agent_runtime for a self-
# contained Render deploy). Putting agent_executor/src on this script's
# path would shadow the live sibling ../agent_runtime editable install
# that sync_agent_runtime_engine() below refreshes -- see
# persona_conversation_tester.py's own comment on the same point for the
# staleness bug that would reintroduce. Leave this pointed at nothing.
SRC_ROOT = PROJECT_ROOT / "src"
for candidate in (PROJECT_ROOT, SRC_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

# Carga primero .env en el root del proyecto, luego en agent_executor si existe
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "agent_executor" / ".env", override=True)

from sync_engine import sync_agent_runtime_engine

sync_agent_runtime_engine()

from agent_compiler.runtime.graph_builder import build_graph, fresh_state
from agent_compiler.runtime.llm_client import OpenAILLMClient
from agent_compiler.runtime.session_resolver import resolve_session
from agent_compiler.targets.langgraph.runtime_artifact import RuntimeArtifact, runtime_artifact_from_dict

console = Console()
COMPILED_AGENTS_DIR = PROJECT_ROOT / "agent_executor" / "compiled_agents"
DEFAULT_SESSION_DIR = PROJECT_ROOT / "backups" / "tui_sessions"
AGENT_COMPILER_DIST_DIR = PROJECT_ROOT / "agents" / "compiled"

# Seeded into `history` (never spoken -- see LLMContext.block()) so a
# fresh conversation's very first `eval: "llm"` gate has real evidence to
# reason from. Fixes outbound-call agents (family_aims_sam_*, babynova_*)
# whose root `states_root.yaml` starts with a "was the call answered?"
# decision: on a genuinely empty `history`, that gate has nothing to judge
# and reliably falls back to "not answered", ending the conversation
# before the agent ever speaks. English on purpose -- the model reasons
# about it regardless of which language the compiled agent itself speaks.
OUTBOUND_ANSWERED_SEED_LINE = (
    "[system note] The outbound call has connected -- the contact answered and is on the line."
)

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


class ExitLauncher(SystemExit):
    pass


class ReturnToLauncher(Exception):
    pass


def _tools_base_url() -> str:
    import os

    host = os.getenv("HOST", "127.0.0.1")
    port = os.getenv("PORT", "8010")
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{probe_host}:{port}/family_aims/v1"


@dataclass
class ToolTrace:
    tool_name: str
    args: Dict[str, Any]
    status_code: Optional[int] = None
    response_body: Any = None


@dataclass
class CompiledSessionState:
    agent_slug: str
    thread_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_traces: List[ToolTrace] = field(default_factory=list)
    # "agent" (saliente/voz -- el agente habla primero, ej. family_aims_sam_*)
    # or "user" (entrante/texto -- el usuario escribe primero).
    first_speaker: str = "agent"


@dataclass
class ChatTurnResult:
    messages: List[str]
    conversation_ended: bool
    current_state: Optional[str]
    traces: List[ToolTrace]


class TracingClient(httpx.Client):
    """httpx.Client that records every tool POST -- passed as
    build_graph()'s tool_http_client so make_tool_executor() uses it for
    every tool call, with no monkeypatching involved (that parameter
    exists exactly for this: see runtime/tool_executor.py's docstring)."""

    def __init__(self, *args: Any, trace_sink: List[ToolTrace], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._trace_sink = trace_sink

    def post(self, url: str, *args: Any, json: Any = None, **kwargs: Any) -> httpx.Response:  # noqa: A002
        response = super().post(url, *args, json=json, **kwargs)
        tool_name = url.rstrip("/").rsplit("/", 1)[-1]
        try:
            body = response.json()
        except ValueError:
            body = response.text
        self._trace_sink.append(
            ToolTrace(tool_name=tool_name, args=dict(json or {}), status_code=response.status_code, response_body=body)
        )
        return response


def sync_from_agent_compiler() -> None:
    """Copia graph.json/assets.json recién compilados desde la ruta estándar
    agents/compiled/<slug>/ hacia compiled_agents/<slug>/, si son más
    nuevos que lo que ya está desplegado acá.

    Corre siempre, antes de discover_compiled_agents(), para que un agente
    recien compilado (o solo actualizado) aparezca sin un paso manual de
    copia. Nunca toca sessions.db -- eso es estado de conversaciones reales,
    no un artefacto de compilacion.
    """
    if not AGENT_COMPILER_DIST_DIR.exists():
        return

    for dist_agent_dir in sorted(AGENT_COMPILER_DIST_DIR.iterdir()):
        graph_src = dist_agent_dir / "langgraph" / "graph.json"
        if not dist_agent_dir.is_dir() or not graph_src.exists():
            continue

        slug = dist_agent_dir.name
        dest_dir = COMPILED_AGENTS_DIR / slug
        graph_dst = dest_dir / "graph.json"
        assets_src = dist_agent_dir / "assets.json"
        assets_dst = dest_dir / "assets.json"

        if graph_dst.exists() and graph_src.stat().st_mtime <= graph_dst.stat().st_mtime:
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(graph_src, graph_dst)
        if assets_src.exists():
            try:
                shutil.copy2(assets_src, assets_dst)
            except PermissionError as e:
                console.print(f"[yellow]Advertencia: No se pudo copiar {assets_src} → {assets_dst}: {e}. El archivo está siendo usado por otro proceso.[/yellow]")
                # Continúa sin interrumpir el TUI
        console.print(f"[dim]Sincronizado {slug} desde agents/compiled/[/dim]")


def discover_compiled_agents() -> List[str]:
    """Cualquier subcarpeta de compiled_agents/ con un graph.json -- sin
    nada especifico de un agente en particular hardcodeado."""
    if not COMPILED_AGENTS_DIR.exists():
        return []
    return sorted(
        entry.name
        for entry in COMPILED_AGENTS_DIR.iterdir()
        if entry.is_dir() and (entry / "graph.json").exists()
    )


class CompiledAgentHarness:
    """Un grafo LangGraph real, construido una sola vez por sesion de TUI,
    reusado turno a turno via el mismo thread_id."""

    def __init__(self, agent_slug: str, contact: Optional[Dict[str, Any]] = None):
        self.agent_slug = agent_slug
        self.contact = contact or {}
        graph_path = COMPILED_AGENTS_DIR / agent_slug / "graph.json"
        self.artifact: RuntimeArtifact = runtime_artifact_from_dict(json.loads(graph_path.read_text(encoding="utf-8")))

        self._say_buffer: List[str] = []
        self._trace_buffer: List[ToolTrace] = []

        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        db_path = COMPILED_AGENTS_DIR / agent_slug / "sessions.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)

        tracing_client = TracingClient(trace_sink=self._trace_buffer)
        llm_client = OpenAILLMClient()

        self.graph = build_graph(
            self.artifact,
            llm_client,
            tools_base_url=_tools_base_url(),
            tool_http_client=tracing_client,
            checkpointer=checkpointer,
            say_callback=self._say_buffer.append,
        )

    def start(
        self,
        thread_id: str,
        first_speaker: str = "agent",
        opening_message: Optional[str] = None,
    ) -> ChatTurnResult:
        """Invoke the very first turn of a brand-new thread.

        `first_speaker` decides what the graph's first `eval: "llm"` gate
        (or any other node reading `history`/`last_user_message`) gets to
        reason from, since a plain `fresh_state()` starts with none of
        that -- see `fresh_state`'s own docstring for why that silently
        breaks an outbound-call agent's "was the call answered?" decision:

        - "agent" (saliente/voz): seeds a neutral note that the call
          connected, so that kind of gate has real evidence and the agent
          actually gets to greet, instead of the graph landing on its
          "not answered" fallback with zero context.
        - "user" (entrante/texto): seeds `opening_message` as the first
          thing the contact said, so any early node that reasons over
          `last_user_message`/`history` sees it from turn one -- the same
          shape a real inbound webhook delivers (the platform already has
          the user's message when it starts the conversation).
        """
        config = {"configurable": {"thread_id": thread_id}}
        self._say_buffer.clear()
        self._trace_buffer.clear()

        if first_speaker == "user":
            seed_history = [f"user: {opening_message}"] if opening_message else []
            state = fresh_state(
                self.artifact,
                contact=self.contact,
                seed_history=seed_history,
                seed_last_user_message=opening_message or "",
            )
        else:
            state = fresh_state(
                self.artifact,
                contact=self.contact,
                seed_history=[OUTBOUND_ANSWERED_SEED_LINE],
            )

        result = self.graph.invoke(state, config)
        return self._finish(thread_id, result)

    def turn(self, thread_id: str, message: Optional[str]) -> ChatTurnResult:
        config = {"configurable": {"thread_id": thread_id}}
        self._say_buffer.clear()
        self._trace_buffer.clear()

        snapshot = self.graph.get_state(config)
        resolution = resolve_session(snapshot.values, self.artifact.session_timeout_minutes)

        if resolution in ("new", "stale"):
            result = self.graph.invoke(fresh_state(self.artifact, contact=self.contact), config)
        else:
            if not message:
                raise RuntimeError("Este thread ya tiene una conversacion en curso -- se necesita un mensaje.")
            result = self.graph.invoke(Command(resume=message), config)

        return self._finish(thread_id, result)

    def _finish(self, thread_id: str, result: Dict[str, Any]) -> ChatTurnResult:
        config = {"configurable": {"thread_id": thread_id}}
        interrupts = result.get("__interrupt__")
        messages = list(self._say_buffer)
        if interrupts:
            messages.extend(interrupts[0].value.get("prompt", []))
            conversation_ended = False
        else:
            conversation_ended = True

        final_state = self.graph.get_state(config).values
        return ChatTurnResult(
            messages=messages,
            conversation_ended=conversation_ended,
            current_state=final_state.get("current_state"),
            traces=list(self._trace_buffer),
        )

    def state_snapshot(self, thread_id: str) -> Dict[str, Any]:
        config = {"configurable": {"thread_id": thread_id}}
        return dict(self.graph.get_state(config).values or {})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _history_entry(direction: str, body: str) -> Dict[str, Any]:
    return {"direction": direction, "body": body, "at": _now_iso()}


def _new_state(agent_slug: str) -> CompiledSessionState:
    return CompiledSessionState(agent_slug=agent_slug, thread_id=f"tui-{uuid.uuid4().hex[:12]}")


def _session_payload(state: CompiledSessionState) -> Dict[str, Any]:
    return {
        "agent_slug": state.agent_slug,
        "thread_id": state.thread_id,
        "history": state.history,
        "first_speaker": state.first_speaker,
        "saved_at": _now_iso(),
    }


def save_session(state: CompiledSessionState, session_file: Path) -> None:
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(json.dumps(_session_payload(state), indent=2, ensure_ascii=False), encoding="utf-8")


def load_session(session_file: Path) -> CompiledSessionState:
    data = json.loads(session_file.read_text(encoding="utf-8"))
    return CompiledSessionState(
        agent_slug=data["agent_slug"],
        thread_id=data["thread_id"],
        history=data.get("history", []),
        last_traces=[],
        first_speaker=data.get("first_speaker", "agent"),
    )


def resolve_session_file(agent_slug: str, session_file_arg: Optional[str]) -> Path:
    if session_file_arg:
        path = Path(session_file_arg)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()

    DEFAULT_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_SESSION_DIR / f"compiled_{agent_slug}_session_{timestamp}.json"


def render_history(state: CompiledSessionState) -> None:
    if not state.history:
        console.print("[yellow]No hay historial todavia.[/yellow]")
        return
    for item in state.history:
        title = "User" if item.get("direction") == "inbound" else state.agent_slug
        style = "green" if item.get("direction") == "inbound" else "magenta"
        console.print(Panel.fit(str(item.get("body", "")), title=title, border_style=style))


def render_traces(traces: List[ToolTrace]) -> None:
    if not traces:
        console.print("[dim]Sin tool calls en este turno.[/dim]")
        return
    for index, trace in enumerate(traces, start=1):
        code_label = "n/a" if trace.status_code is None else str(trace.status_code)
        body = trace.response_body
        response_text = json.dumps(body, indent=2, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
        details = (
            f"codigo: {code_label}\n\n"
            f"argumentos:\n{json.dumps(trace.args, indent=2, ensure_ascii=False)}\n\n"
            f"respuesta:\n{response_text}"
        )
        console.print(Panel(details, title=f"Tool {index}: {trace.tool_name}", border_style="cyan"))


def render_state(harness: CompiledAgentHarness, state: CompiledSessionState) -> None:
    snapshot = harness.state_snapshot(state.thread_id)
    if not snapshot:
        console.print("[yellow]Sin estado todavia -- la conversacion no arranco.[/yellow]")
        return
    current_state = snapshot.get("current_state") or "(ninguno)"
    slots = snapshot.get("slots", {})
    console.print(f"[bold cyan]current_state:[/bold cyan] {current_state}")
    if slots:
        console.print_json(data=slots)
    else:
        console.print("[dim]Sin slots capturados todavia.[/dim]")


def ask_first_speaker() -> str:
    """Menu shown once per brand-new thread: who speaks first.

    "agent" mirrors an outbound voice call (family_aims_sam_*, babynova_*):
    the graph seeds evidence that the call connected, so its `states_root`
    "was the call answered?" gate resolves and the agent's greeting plays.
    "user" mirrors an inbound chat: you type the opening message yourself,
    seeded as the first thing the contact said, before the graph runs at
    all -- the same shape a real inbound webhook delivers.
    """
    choice = questionary.select(
        "Quien habla primero en esta conversacion?",
        choices=[
            questionary.Choice("El agente (saliente / voz)", value="agent"),
            questionary.Choice("El usuario (entrante / texto)", value="user"),
        ],
        style=STYLE,
    ).ask()
    return choice or "agent"


def render_chat_header(state: CompiledSessionState, session_file: Path) -> None:
    console.clear()
    console.print(f"[bold cyan]{state.agent_slug}[/bold cyan] -- agente compilado (agent_compiler + agent_runtime)")
    console.print(f"Sesion: {session_file}")
    console.print(f"thread_id={state.thread_id}")
    speaker_label = "el agente (saliente/voz)" if state.first_speaker == "agent" else "el usuario (entrante/texto)"
    console.print(f"Habla primero: {speaker_label}")
    console.print("Comandos: /help  /history  /traces  /state  /save  /new  /back  /exit\n")


def render_chat_help() -> None:
    table = Table(title="Comandos")
    table.add_column("Comando", style="cyan", no_wrap=True)
    table.add_column("Descripcion")
    table.add_row("/help", "Muestra esta ayuda")
    table.add_row("/history", "Muestra el historial actual")
    table.add_row("/traces", "Muestra las tool calls del ultimo turno")
    table.add_row("/state", "Muestra current_state y los slots capturados (estado real del grafo)")
    table.add_row("/save", "Guarda la sesion actual (el historial local -- el estado real ya esta persistido)")
    table.add_row("/new", "Empieza una conversacion nueva (thread_id nuevo)")
    table.add_row("/back", "Guarda y vuelve al selector de agentes")
    table.add_row("/exit", "Guarda y cierra el script")
    console.print(table)


def handle_chat_command(raw_text: str, state: CompiledSessionState, session_file: Path, harness: CompiledAgentHarness) -> bool:
    command = raw_text.strip().lower()
    if command == "/help":
        render_chat_help()
        return True
    if command == "/history":
        render_history(state)
        return True
    if command == "/traces":
        render_traces(state.last_traces)
        return True
    if command == "/state":
        render_state(harness, state)
        return True
    if command == "/save":
        save_session(state, session_file)
        console.print(f"[cyan]Sesion guardada en {session_file}[/cyan]")
        return True
    if command == "/new":
        fresh = _new_state(state.agent_slug)
        state.thread_id = fresh.thread_id
        state.history = []
        state.last_traces = []
        console.print(f"[cyan]Conversacion nueva. thread_id={state.thread_id}[/cyan]")
        run_first_turn(harness, state, session_file)
        return True
    if command == "/back":
        save_session(state, session_file)
        raise ReturnToLauncher()
    if command == "/exit":
        save_session(state, session_file)
        raise ExitLauncher(0)
    return False


def run_first_turn(harness: CompiledAgentHarness, state: CompiledSessionState, session_file: Path) -> None:
    """Kick off a brand-new thread: ask who speaks first, then invoke it.

    "agent": the graph runs with no user text, same as before, but now with
    `OUTBOUND_ANSWERED_SEED_LINE` seeded so an outbound-call agent's "was
    the call answered?" gate can actually resolve -- see `harness.start`.
    "user": you type the opening message here, before the graph runs at
    all, and it's seeded as the first thing the contact said.
    """
    state.first_speaker = ask_first_speaker()
    render_chat_header(state, session_file)

    opening_message: Optional[str] = None
    if state.first_speaker == "user":
        opening_message = console.input("\n[bold green]Primer mensaje (usuario)> [/bold green]").strip()
        console.print(Panel.fit(opening_message, title="User", border_style="green"))
        state.history.append(_history_entry("inbound", opening_message))

    try:
        result = harness.start(state.thread_id, state.first_speaker, opening_message)
    except Exception as exc:
        console.print(Panel.fit(str(exc), title="Error", border_style="red"))
        return

    for msg in result.messages:
        console.print(Panel.fit(msg, title=state.agent_slug, border_style="magenta"))
    state.history.extend(_history_entry("outbound", m) for m in result.messages)
    state.last_traces = result.traces
    render_traces(result.traces)
    if result.conversation_ended:
        console.print("[dim]-- conversacion finalizada (el grafo llego a un nodo terminal) --[/dim]")
    save_session(state, session_file)


def chat_loop(state: CompiledSessionState, session_file: Path, contact: Optional[Dict[str, Any]] = None) -> None:
    harness = CompiledAgentHarness(state.agent_slug, contact=contact)
    render_chat_header(state, session_file)
    render_chat_help()
    console.print()
    render_history(state)

    # Primer turno: si el thread es nuevo, pregunta quien habla primero y
    # arranca el flujo (ver run_first_turn).
    snapshot = harness.state_snapshot(state.thread_id)
    if not snapshot:
        run_first_turn(harness, state, session_file)

    while True:
        try:
            raw_text = console.input("\n[bold green]Tu> [/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            save_session(state, session_file)
            raise ReturnToLauncher()

        if not raw_text:
            continue
        if raw_text.startswith("/") and handle_chat_command(raw_text, state, session_file, harness):
            continue

        console.print(Panel.fit(raw_text, title="User", border_style="green"))
        state.history.append(_history_entry("inbound", raw_text))
        try:
            result = harness.turn(state.thread_id, raw_text)
        except Exception as exc:
            console.print(Panel.fit(str(exc), title="Error", border_style="red"))
            continue

        for msg in result.messages:
            console.print(Panel.fit(msg, title=state.agent_slug, border_style="magenta"))
        state.history.extend(_history_entry("outbound", m) for m in result.messages)
        state.last_traces = result.traces
        render_traces(result.traces)
        if result.conversation_ended:
            console.print("[dim]-- conversacion finalizada (el grafo llego a un nodo terminal) --[/dim]")
        save_session(state, session_file)


def choose_agent(agent_slugs: List[str]) -> str:
    console.clear()
    choice = questionary.select(
        "Selecciona un agente compilado:",
        choices=[*agent_slugs, questionary.Separator(), "Salir"],
        style=STYLE,
    ).ask()
    if choice in (None, "Salir"):
        raise ExitLauncher(0)
    return choice


def run_launcher(args: argparse.Namespace) -> int:
    agent_slugs = discover_compiled_agents()
    if not agent_slugs:
        console.print(
            f"[red]No se encontro ningun agente compilado bajo {COMPILED_AGENTS_DIR}[/red] "
            "(se espera compiled_agents/<slug>/graph.json)."
        )
        return 1

    while True:
        try:
            agent_slug = args.agent if args.agent else choose_agent(agent_slugs)
            if args.agent and agent_slug not in agent_slugs:
                console.print(f"[red]Agente desconocido: {agent_slug!r}. Disponibles: {agent_slugs}[/red]")
                return 1

            session_file = resolve_session_file(agent_slug, args.session_file)
            state = load_session(session_file) if session_file.exists() else _new_state(agent_slug)
            chat_loop(state, session_file, contact=_contact_from_args(args))
        except ReturnToLauncher:
            args.agent = None  # volver siempre al selector, no repetir el mismo agente en --agent directo
            continue
        except ExitLauncher as exc:
            return int(exc.code or 0)


def _contact_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    """Build the session's `contact` dict (CRM fields the graph resolves any
    `{{contact.X}}` reference against) from the optional `--contact-*` flags.
    Empty (no flags given) matches today's behavior -- every such reference
    stays unresolved/unknown, same as before `contact` existed at all."""
    contact = {
        "name": args.contact_name,
        "email": args.contact_email,
        "phone": args.contact_phone,
        "language": args.contact_language,
    }
    return {k: v for k, v in contact.items() if v is not None}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TUI para probar cualquier agente compilado bajo compiled_agents/")
    parser.add_argument("--agent", help="Slug del agente a abrir directo (ver --list). Si se omite, muestra el selector.")
    parser.add_argument("--session-file", help="Ruta del JSON de sesion a retomar/guardar")
    parser.add_argument("--list", action="store_true", help="Lista los agentes compilados disponibles y sale")
    parser.add_argument(
        "--contact-name", help="Simula un contacto ya conocido por el CRM -- nombre (resuelve {{contact.name}})."
    )
    parser.add_argument("--contact-email", help="Simula un contacto ya conocido -- email (resuelve {{contact.email}}).")
    parser.add_argument("--contact-phone", help="Simula un contacto ya conocido -- telefono (resuelve {{contact.phone}}).")
    parser.add_argument(
        "--contact-language", help="Simula un contacto ya conocido -- idioma preferido (resuelve {{contact.language}})."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sync_from_agent_compiler()
    if args.list:
        agent_slugs = discover_compiled_agents()
        if not agent_slugs:
            console.print(f"[yellow]Sin agentes compilados bajo {COMPILED_AGENTS_DIR}[/yellow]")
        for slug in agent_slugs:
            console.print(f"- {slug}")
        raise SystemExit(0)

    try:
        code = run_launcher(args)
        raise ExitLauncher(code)
    except ExitLauncher as exc:
        raise SystemExit(int(exc.code or 0))


if __name__ == "__main__":
    main()
