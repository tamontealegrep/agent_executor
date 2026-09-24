"""Simulador de conversaciones por persona contra CUALQUIER agente compilado.

Corre N conversaciones automatizadas entre un "usuario simulado" (un LLM
barato de OpenAI/Anthropic/Google que actua segun una descripcion de
personalidad/objetivo que vos le das) y el agente LangGraph real compilado
bajo compiled_agents/<slug>/graph.json -- el mismo grafo que usa
compiled_agent_tui.py, contra el backend de tools real (TracingClient
reusado de compiled_agent_tui.py).

General por diseno, no atado a un agente en particular:
- `--agent` es cualquier slug bajo compiled_agents/ con un graph.json.
- `--tools-base-url` apunta las tool calls al prefijo correcto para ESE
  agente -- el prefijo de tools (src/tools/<paquete>/, montado en
  /<paquete>/v1 por agent_executor/main.py) no siempre coincide con el
  slug del agente compilado; default: el mismo /family_aims/v1 que usa
  compiled_agent_tui.py, para no romper el uso actual.
- `--business-context` cambia la frase "el asistente virtual de ___" del
  prompt del usuario simulado para que calce con el rubro real del agente.
- Las personas (`--persona-file`/`--persona-files`) son texto libre tuyo,
  sin nada de fertilidad hardcodeado en el codigo -- cada agente nuevo
  simplemente necesita sus propios archivos de persona bajo
  scripts/personas/.
- `--doubt-boost` agrega, a CUALQUIER persona cargada, una instruccion
  generica para que el usuario simulado dude y pida explicaciones en
  puntos de decision -- util para explorar ramas (not_sure,
  reaseguramiento, ...) que una persona siempre-decidida no dispara, sin
  tener que escribir una persona "indecisa" por separado para cada caso.

Cada corrida es una conversacion nueva, aislada (thread_id unico,
InMemorySaver -- nunca toca compiled_agents/<slug>/sessions.db, la base
real de la TUI/produccion), y queda guardada como un JSON con cada mensaje
del agente, cada mensaje del usuario simulado, y cada tool call (nombre,
argumentos, codigo de respuesta, cuerpo) en el orden real en que ocurrieron.

Uso (family_aims_sam_text_2_0, el caso ya cubierto por scripts/personas/):
    python scripts/persona_conversation_tester.py \
        --agent family_aims_sam_text_2_0 \
        --persona-files scripts/personas/declines_confirmation.txt,scripts/personas/explores_surrogacy.txt \
        --runs 12 --providers openai,anthropic,google --doubt-boost

Uso con otro agente (ejemplo, ajustando el paquete de tools real):
    python scripts/persona_conversation_tester.py \
        --agent otro_agente_compilado \
        --tools-base-url http://127.0.0.1:8010/otro_paquete_de_tools/v1 \
        --business-context "una inmobiliaria" \
        --persona-file scripts/personas/mi_persona_nueva.txt \
        --runs 6

Personas ya escritas en scripts/personas/: declines_confirmation.txt (el
escenario pedido directamente: usuario interesado en los procesos, en
distintas partes del mundo, que siempre dice que no o que un dato esta mal
al momento de confirmar la cita), tries_to_book.txt (sí confirma, sí llega
a book_appointment), explores_surrogacy.txt (fuerza el camino de gestacion
subrogada -- las corridas anteriores sin esta persona jamas lo exploraron,
siempre terminaban en FIV).

Nota sobre errores de proveedor (creditos agotados, rate limit, auth):
se detectan y se marcan explicitamente como "provider_error" en el JSON
de esa corrida y en el resumen final -- nunca se cuentan como un
comportamiento raro del agente. Si ves ese tipo de error, es el
proveedor del usuario simulado (verificar saldo/billing), no un bug del
agente bajo prueba.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid

# El SDK google-genai emite un logger.warning() ruidoso ("direct use of AFC
# not recommended") en cada llamada a generate_content -- inofensivo (no es
# un error), pero se mezcla con la salida de la corrida. Silenciado aca en
# vez de cambiar a Chat.send_message solo para evitar el warning.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = Path(__file__).resolve().parent
for candidate in (PROJECT_ROOT, SRC_ROOT, SCRIPTS_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from sync_engine import sync_agent_runtime_engine  # noqa: E402

sync_agent_runtime_engine()

from langgraph.types import Command  # noqa: E402

from agent_compiler.runtime.graph_builder import build_graph, fresh_state  # noqa: E402
from agent_compiler.runtime.llm_client import OpenAILLMClient  # noqa: E402
from agent_compiler.runtime.session_resolver import resolve_session  # noqa: E402
from agent_compiler.targets.langgraph.runtime_artifact import RuntimeArtifact, runtime_artifact_from_dict  # noqa: E402

from compiled_agent_tui import (  # noqa: E402
    COMPILED_AGENTS_DIR,
    ToolTrace,
    TracingClient,
    _tools_base_url,
    discover_compiled_agents,
    sync_from_agent_compiler,
)

END_TOKEN = "<<END>>"

# Instruccion fija que se agrega SIEMPRE al prompt del usuario simulado,
# sin importar la persona -- para que cualquier nombre/email/telefono que
# el usuario simulado le de al agente sea trivial de encontrar y cancelar
# despues (pedido directamente: corremos contra el backend real, que
# puede llegar a crear una cita real via book_appointment).
IDENTIFIABLE_TEST_DATA_RULE = (
    "Si el agente te pide tu nombre completo, respondelo siempre como "
    "'PRUEBA SCRIPT {run_id}' (literal, con ese run_id). Si te pide correo, "
    "usa siempre 'prueba+{run_id}@example.com'. Si te pide telefono, usa "
    "siempre '3000000000'. Esto es obligatorio en toda la conversacion, "
    "sin excepcion, incluso si no calza perfecto con tu personalidad."
)


# ---------------------------------------------------------------------------
# Usuario simulado -- un cliente chico por proveedor, misma interfaz
# ---------------------------------------------------------------------------


class UserSimulatorClient(Protocol):
    provider: str
    model: str

    def reply(self, prompt: str) -> str:
        """Devuelve el proximo mensaje del usuario simulado, o END_TOKEN."""
        ...


class ProviderError(RuntimeError):
    """Fallo del proveedor del usuario simulado (creditos, rate limit, auth,
    red) -- nunca un bug del agente bajo prueba. Ver el docstring del modulo."""

    def __init__(self, provider: str, model: str, original: Exception) -> None:
        self.provider = provider
        self.model = model
        self.original = original
        super().__init__(f"[{provider}/{model}] {type(original).__name__}: {original}")


@dataclass
class OpenAIUserSimulator:
    model: str = "gpt-4o-mini"
    provider: str = field(default="openai", init=False)

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    def reply(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, see ProviderError
            raise ProviderError(self.provider, self.model, exc) from exc


@dataclass
class AnthropicUserSimulator:
    model: str = "claude-haiku-4-5-20251001"
    provider: str = field(default="anthropic", init=False)

    def __post_init__(self) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    def reply(self, prompt: str) -> str:
        try:
            # Este SDK (1.7.0+) ya no tiene `temperature` -- lo reemplazo el
            # paradigma de reasoning effort (`output_config.effort`).
            # Confirmado en vivo (2026-09-18): `Messages.create()` no acepta
            # `temperature` como kwarg en esta version.
            response = self._client.messages.create(
                model=self.model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low"},
            )
            return "".join(block.text for block in response.content if block.type == "text").strip()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.provider, self.model, exc) from exc


@dataclass
class GoogleUserSimulator:
    model: str = "gemini-2.5-flash-lite"
    provider: str = field(default="google", init=False)

    def __post_init__(self) -> None:
        from google import genai

        self._client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

    def reply(self, prompt: str) -> str:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.9),
            )
            return (response.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(self.provider, self.model, exc) from exc


SIMULATOR_FACTORIES: dict[str, type[UserSimulatorClient]] = {
    "openai": OpenAIUserSimulator,
    "anthropic": AnthropicUserSimulator,
    "google": GoogleUserSimulator,
}


def build_simulator(provider: str) -> UserSimulatorClient:
    factory = SIMULATOR_FACTORIES.get(provider)
    if factory is None:
        raise ValueError(f"Proveedor desconocido {provider!r}. Validos: {sorted(SIMULATOR_FACTORIES)}")
    return factory()  # type: ignore[call-arg]


DOUBT_OVERLAY = (
    "Ademas de tu personalidad de base: sos una persona que no siempre tiene todo claro. "
    "En varios puntos de la conversacion -- especialmente cuando el agente te pida elegir "
    "entre varias opciones, decidir algo importante, o dar un dato sobre tu situacion -- "
    "expresa dudas genuinas antes de responder del todo: decis que no sabes, pedis que te "
    "expliquen las alternativas de nuevo o de otra forma, dudas entre dos opciones en voz "
    "alta, o cambias de idea al menos una vez durante la conversacion. No respondas siempre "
    "de forma directa y seguro/a -- dejar ver la duda es parte del personaje, no un error."
)


def build_user_prompt(
    persona: str,
    run_id: str,
    transcript_lines: list[str],
    *,
    business_context: str = "una empresa",
    doubt_boost: bool = False,
) -> str:
    identifiable_rule = IDENTIFIABLE_TEST_DATA_RULE.format(run_id=run_id)
    transcript_block = "\n".join(transcript_lines) if transcript_lines else "(el agente todavia no dijo nada)"
    persona_block = f"{persona}\n\n{DOUBT_OVERLAY}" if doubt_boost else persona
    return (
        f"Estas actuando como una persona real chateando por WhatsApp/SMS con el asistente "
        f"virtual de {business_context}. No sos un asistente de IA -- actuas 100% como "
        "el usuario descrito abajo. Nunca reveles que sos una simulacion ni rompas el personaje.\n\n"
        f"PERSONALIDAD Y OBJETIVO DE ESTE USUARIO:\n{persona_block}\n\n"
        f"REGLA OBLIGATORIA DE DATOS DE CONTACTO:\n{identifiable_rule}\n\n"
        "Instrucciones de formato:\n"
        "- Respondes en 1 o 2 frases cortas, como escribiria una persona real por chat, "
        "sin firmar ni poner prefijos como 'Usuario:'.\n"
        "- Mantente consistente con la personalidad y el objetivo durante TODA la conversacion.\n"
        "- Decir tu despedida/decision (ej. 'no voy a confirmar', 'lo voy a pensar') es un mensaje "
        "normal como cualquier otro -- mandalo como texto real, dejando que el agente responda.\n"
        f"- SOLO en el turno DESPUES de eso, si el agente te sigue preguntando algo y vos ya no "
        f"tenes nada mas que decir (tu objetivo ya se cumplio), respondes con EXACTAMENTE el "
        f"texto {END_TOKEN} y nada mas -- ni una palabra antes ni despues, nunca pegado al final "
        f"de una frase con contenido.\n\n"
        f"CONVERSACION HASTA AHORA:\n{transcript_block}\n\n"
        "Tu proximo mensaje como el usuario:"
    )


# ---------------------------------------------------------------------------
# Auto-exploracion -- en vez de escribir personas a mano adivinando que
# ramas existen, lee el graph.json REAL del agente y encuentra cada punto
# de decision con opciones enumeradas (`capture: Literal[...]` en un nodo
# `question`), para generar una corrida por cada valor posible. General por
# construccion: no sabe nada de fertilidad ni de ningun dominio en
# particular, solo lee la forma del grafo compilado -- sirve igual para
# cualquier otro agente_compiler.
# ---------------------------------------------------------------------------

_LITERAL_MEMBERS_RE = re.compile(r"^Literal\[(.+)\]$")


def _literal_members(type_expr: str) -> list[str] | None:
    match = _LITERAL_MEMBERS_RE.match(type_expr.strip())
    if not match:
        return None
    return [m.strip() for m in match.group(1).split(",")]


@dataclass(frozen=True)
class ExplorationTarget:
    node_id: str
    node_goal: str
    slot: str
    value: str


def discover_exploration_targets(artifact: RuntimeArtifact) -> list[ExplorationTarget]:
    """Uno por cada (nodo `question`, slot capturado, valor posible del enum).

    Solo mira nodos `question` con un `capture` de tipo `Literal[...]` --
    es exactamente lo que el agente le pregunta al usuario y espera de
    vuelta uno de un set cerrado de valores, asi que es el unico tipo de
    punto de decision que se puede orientar de forma confiable con una
    instruccion de texto al usuario simulado (`free_text`/`person_name`/
    etc. no tienen un conjunto de valores a apuntar).
    """
    targets: list[ExplorationTarget] = []
    for node in artifact.graph.nodes:
        if node.node_type != "question":
            continue
        for slot, type_expr in node.capture:
            members = _literal_members(type_expr)
            if not members:
                continue
            goal_text = " ".join(node.goal) if node.goal else ""
            for value in members:
                targets.append(ExplorationTarget(node_id=node.node_id, node_goal=goal_text, slot=slot, value=value))
    return targets


def discover_namespace_entry_phrases(artifact: RuntimeArtifact) -> dict[str, str]:
    """Namespace (ej. 'AM') -> una frase trigger real que un handler del router
    global usa para entrar ahi (`artifact.global_router.handlers`).

    Varios subflujos (gestion de citas, etc.) no son parte del flujo principal
    que arranca en la apertura -- solo se entra si el usuario dice algo que
    matchea el `trigger:` de un handler. Un target de auto-exploracion en uno
    de esos namespaces es inalcanzable para un lead generico que solo responde
    lo que se le pregunta; necesita que se le diga explicitamente con que
    frase abrir. El resto del grafo (el flujo principal) no aparece aca a
    proposito -- un lead cooperativo lo recorre solo, sin necesitar un hint.
    """
    phrases: dict[str, str] = {}
    for handler in artifact.global_router.handlers:
        if not handler.trigger:
            continue
        for edge in list(handler.route) + list(handler.fallback):
            target = edge.target
            if not target or "__" not in target:
                continue
            namespace = target.split("__", 1)[0]
            phrases.setdefault(namespace, handler.trigger[0])
    return phrases


AUTO_EXPLORE_BASE_PERSONA = (
    "Sos un lead real, colaborativo y con curiosidad genuina: respondes con normalidad a "
    "cualquier pregunta de calificacion que te hagan (datos de contacto, situacion personal, "
    "pais/ciudad, preferencias, etc.), haces alguna pregunta de vez en cuando sobre lo que te "
    "van ofreciendo, y en general dejas que la conversacion avance en vez de cortarla temprano. "
    "Tu nacionalidad y ciudad cambian en cada conversacion nueva -- en cada una elegis un pais y "
    "ciudad distintos, de cualquier parte del mundo, no siempre el mismo."
)


def build_auto_explore_persona(target: ExplorationTarget, entry_phrase: str | None = None) -> str:
    goal_hint = f" (el objetivo de esa pregunta, en la definicion del agente, es: {target.node_goal!r})" if target.node_goal else ""
    entry_hint = (
        f"\n\nIMPORTANTE -- ESTA SITUACION NO ES PARTE DE LA CONVERSACION NORMAL: para que el "
        f"agente te lleve ahi, tenes que abrir la conversacion (tu PRIMER mensaje) transmitiendo "
        f"esta intencion, en tus propias palabras (no la copies literal, es solo una referencia de "
        f"que decir): {entry_phrase!r}. Recien despues de eso segui con el resto de la conversacion "
        "con normalidad."
        if entry_phrase
        else ""
    )
    return (
        f"{AUTO_EXPLORE_BASE_PERSONA}\n\n"
        "OBJETIVO ESPECIFICO DE ESTA CONVERSACION: en algun momento el agente te va a hacer una "
        f"pregunta cerrada, de opcion multiple{goal_hint}. Cuando eso pase, tu respuesta tiene que "
        f"corresponder claramente a esta opcion (es un identificador interno en ingles -- "
        f"traducilo vos a lenguaje natural, nunca lo repitas literal): '{target.value}'.{entry_hint} Si esa "
        "pregunta especifica nunca llega a aparecer en esta conversacion (porque tu situacion no "
        "califica para esa rama), segui la conversacion con normalidad hasta un cierre razonable."
    )


# ---------------------------------------------------------------------------
# Harness del agente -- mismo patron que CompiledAgentHarness de
# compiled_agent_tui.py, pero con InMemorySaver (nunca toca sessions.db,
# la base real de produccion/TUI) porque cada corrida es autocontenida
# dentro de este mismo proceso.
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    agent_messages: list[str]
    conversation_ended: bool
    current_state: str | None
    traces: list[ToolTrace]
    slots: dict[str, Any]


class AgentTestHarness:
    def __init__(self, agent_slug: str, tools_base_url: str, contact: dict[str, Any] | None = None) -> None:
        self.agent_slug = agent_slug
        self.contact = contact or {}
        graph_path = COMPILED_AGENTS_DIR / agent_slug / "graph.json"
        if not graph_path.exists():
            raise FileNotFoundError(f"No hay agente compilado en {graph_path}")
        self.artifact: RuntimeArtifact = runtime_artifact_from_dict(json.loads(graph_path.read_text(encoding="utf-8")))

        self._say_buffer: list[str] = []
        self._trace_buffer: list[ToolTrace] = []
        tracing_client = TracingClient(trace_sink=self._trace_buffer)
        llm_client = OpenAILLMClient()

        # Sin checkpointer explicito -> build_graph() usa InMemorySaver()
        # por default (ver su propio docstring): vive y muere con este
        # proceso, nunca toca compiled_agents/<slug>/sessions.db.
        self.graph = build_graph(
            self.artifact,
            llm_client,
            tools_base_url=tools_base_url,
            tool_http_client=tracing_client,
            say_callback=self._say_buffer.append,
        )

    def turn(self, thread_id: str, user_message: str | None) -> TurnResult:
        config = {"configurable": {"thread_id": thread_id}}
        self._say_buffer.clear()
        self._trace_buffer.clear()

        snapshot = self.graph.get_state(config)
        resolution = resolve_session(snapshot.values, self.artifact.session_timeout_minutes)

        if resolution in ("new", "stale"):
            result = self.graph.invoke(fresh_state(self.artifact, contact=self.contact), config)
        else:
            if not user_message:
                raise RuntimeError("Conversacion en curso pero no se dio un mensaje de usuario.")
            result = self.graph.invoke(Command(resume=user_message), config)

        interrupts = result.get("__interrupt__")
        messages = list(self._say_buffer)
        if interrupts:
            messages.extend(interrupts[0].value.get("prompt", []))
            conversation_ended = False
        else:
            conversation_ended = True

        final_state = self.graph.get_state(config).values
        return TurnResult(
            agent_messages=messages,
            conversation_ended=conversation_ended,
            current_state=(final_state or {}).get("current_state"),
            traces=list(self._trace_buffer),
            slots=dict((final_state or {}).get("slots") or {}),
        )


# ---------------------------------------------------------------------------
# Una corrida completa
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_one_conversation(
    harness: AgentTestHarness,
    persona: str,
    persona_name: str,
    provider: str,
    run_index: int,
    max_turns: int,
    *,
    business_context: str = "una empresa",
    doubt_boost: bool = False,
    exploration_target: ExplorationTarget | None = None,
) -> dict[str, Any]:
    run_id = f"{run_index:03d}-{provider}-{persona_name}-{uuid.uuid4().hex[:6]}"
    thread_id = f"persona-test-{run_id}"
    simulator = build_simulator(provider)

    events: list[dict[str, Any]] = []
    transcript_lines: list[str] = []
    ended_reason = "max_turns_reached"
    error: dict[str, Any] | None = None
    started_at = _now_iso()
    result: TurnResult | None = None

    def record_agent_turn(result: TurnResult) -> None:
        for text in result.agent_messages:
            events.append({"speaker": "agent", "text": text, "current_state": result.current_state, "at": _now_iso()})
            transcript_lines.append(f"Agente: {text}")
        for trace in result.traces:
            events.append(
                {
                    "speaker": "tool_call",
                    "tool_name": trace.tool_name,
                    "args": trace.args,
                    "status_code": trace.status_code,
                    "response": trace.response_body,
                    "at": _now_iso(),
                }
            )

    try:
        result = harness.turn(thread_id, None)
        record_agent_turn(result)

        turn_count = 0
        while not result.conversation_ended and turn_count < max_turns:
            turn_count += 1
            prompt = build_user_prompt(
                persona, run_id, transcript_lines, business_context=business_context, doubt_boost=doubt_boost
            )
            user_text = simulator.reply(prompt)

            # El modelo del usuario simulado a veces no respeta "SOLO el
            # token" y lo pega al final de una frase de despedida real
            # (visto en vivo: "...gracias por la ayuda. <<END>>") -- un
            # chequeo `startswith` dejaba pasar ese mensaje completo como
            # si fuera una respuesta normal, y la conversacion seguia en
            # loop. Se busca el token en cualquier parte del texto.
            if END_TOKEN in user_text:
                ended_reason = "user_ended"
                break

            events.append({"speaker": "user", "text": user_text, "at": _now_iso()})
            transcript_lines.append(f"Usuario: {user_text}")

            result = harness.turn(thread_id, user_text)
            record_agent_turn(result)

            if result.conversation_ended:
                ended_reason = "agent_terminal"
    except ProviderError as exc:
        ended_reason = "provider_error"
        error = {"type": "provider_error", "provider": exc.provider, "model": exc.model, "message": str(exc.original)}
    except Exception as exc:  # noqa: BLE001 -- capturamos para no perder las corridas restantes
        ended_reason = "agent_error"
        error = {"type": "agent_error", "message": f"{type(exc).__name__}: {exc}"}

    final_slots = result.slots if result is not None else {}
    target_reached = (
        exploration_target is not None
        and final_slots.get(exploration_target.slot) == exploration_target.value
    )
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "agent_slug": harness.agent_slug,
        "user_simulator_provider": provider,
        "user_simulator_model": simulator.model,
        "persona_name": persona_name,
        "persona": persona,
        "doubt_boost": doubt_boost,
        "exploration_target": (
            {"node_id": exploration_target.node_id, "slot": exploration_target.slot, "value": exploration_target.value}
            if exploration_target is not None
            else None
        ),
        "target_reached": target_reached if exploration_target is not None else None,
        "started_at": started_at,
        "ended_at": _now_iso(),
        "ended_reason": ended_reason,
        "turn_count": sum(1 for e in events if e["speaker"] == "user"),
        "tool_call_count": sum(1 for e in events if e["speaker"] == "tool_call"),
        "error": error,
        "final_slots": final_slots,
        "events": events,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_personas(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Devuelve [(nombre_persona, texto), ...]. `--persona-files` (plural,
    coma-separado) permite mezclar varias personas en un mismo batch, en
    round-robin independiente del de `--providers` -- por ejemplo, alternar
    entre una persona que siempre rechaza confirmar y una que si intenta
    agendar de verdad, cruzado con los 3 proveedores del usuario simulado."""
    if args.persona_files:
        personas = []
        for raw_path in args.persona_files.split(","):
            raw_path = raw_path.strip()
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = (PROJECT_ROOT / path).resolve()
            personas.append((path.stem, path.read_text(encoding="utf-8").strip()))
        if not personas:
            raise SystemExit("--persona-files no tiene ninguna ruta valida.")
        return personas
    if args.persona_file:
        path = Path(args.persona_file)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return [(path.stem, path.read_text(encoding="utf-8").strip())]
    if args.persona:
        return [("inline", args.persona.strip())]
    raise SystemExit("Falta --persona, --persona-file o --persona-files.")


def _write_summary(out_dir: Path, results: list[dict[str, Any]]) -> Path:
    summary = {
        "generated_at": _now_iso(),
        "total_runs": len(results),
        "by_ended_reason": {},
        "provider_errors": [],
        "runs": [],
    }
    targets_assigned = 0
    targets_reached = 0
    for r in results:
        summary["by_ended_reason"][r["ended_reason"]] = summary["by_ended_reason"].get(r["ended_reason"], 0) + 1
        if r["ended_reason"] == "provider_error":
            summary["provider_errors"].append(
                {"run_id": r["run_id"], "provider": r["user_simulator_provider"], "detail": r["error"]}
            )
        if r.get("exploration_target") is not None:
            targets_assigned += 1
            if r.get("target_reached"):
                targets_reached += 1
        summary["runs"].append(
            {
                "run_id": r["run_id"],
                "provider": r["user_simulator_provider"],
                "model": r["user_simulator_model"],
                "persona_name": r["persona_name"],
                "ended_reason": r["ended_reason"],
                "turn_count": r["turn_count"],
                "tool_call_count": r["tool_call_count"],
                "exploration_target": r.get("exploration_target"),
                "target_reached": r.get("target_reached"),
                "file": f"{r['run_id']}.json",
            }
        )
    if targets_assigned:
        summary["auto_explore_coverage"] = {
            "targets_assigned": targets_assigned,
            "targets_reached": targets_reached,
            "not_reached": [
                {"run_id": r["run_id"], "target": r["exploration_target"]}
                for r in results
                if r.get("exploration_target") is not None and not r.get("target_reached") and r["ended_reason"] != "provider_error"
            ],
        }
    summary_path = out_dir / "_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary_path


def main(argv: list[str] | None = None) -> int:
    sync_from_agent_compiler()
    parser = argparse.ArgumentParser(
        description="Corre conversaciones simuladas por persona contra un agente compilado real."
    )
    parser.add_argument("--agent", required=True, help="Slug bajo compiled_agents/ (ej: family_aims_sam_text_2_0).")
    parser.add_argument("--persona", help="Descripcion de la persona/objetivo del usuario, inline.")
    parser.add_argument("--persona-file", help="Ruta a un archivo de texto con la descripcion de la persona.")
    parser.add_argument(
        "--persona-files",
        help="Varias rutas separadas por coma, en round-robin entre corridas (independiente del round-robin de --providers).",
    )
    parser.add_argument("--runs", type=int, default=3, help="Cuantas conversaciones simular (default: 3).")
    parser.add_argument(
        "--providers",
        default="openai,anthropic,google",
        help="Proveedores para el usuario simulado, en round-robin entre corridas (default: los 3).",
    )
    parser.add_argument("--max-turns", type=int, default=20, help="Limite de turnos de usuario por corrida (default: 20).")
    parser.add_argument("--out-dir", help="Donde guardar los JSON (default: test_runs/<timestamp>/).")
    parser.add_argument(
        "--tools-base-url",
        help=(
            "Base URL de las tools reales para este agente, ej. http://127.0.0.1:8010/novafem_surrogacy/v1 "
            "-- el prefijo de tools NO tiene por que coincidir con el slug del agente compilado (ver "
            "src/tools/<paquete>/api/v1/router.py y main.py de agent_executor para el paquete correcto). "
            "Default: el mismo http://HOST:PORT/family_aims/v1 que usa compiled_agent_tui.py."
        ),
    )
    parser.add_argument(
        "--business-context",
        default="una empresa",
        help=(
            "Frase corta que describe el negocio para el prompt del usuario simulado, ej. "
            "'una clinica de fertilidad' o 'una inmobiliaria' (default: 'una empresa', generico)."
        ),
    )
    parser.add_argument(
        "--doubt-boost",
        action="store_true",
        help=(
            "Le agrega a CUALQUIER persona cargada una instruccion extra para que el usuario simulado "
            "dude, pida explicaciones y a veces cambie de opinion en puntos de decision -- util para "
            "explorar ramas del flujo (not_sure, reaseguramiento, etc.) que una persona siempre-decidida "
            "nunca dispara."
        ),
    )
    parser.add_argument(
        "--contact-name", help="Simula un contacto ya conocido por el CRM -- nombre (resuelve {{contact.name}})."
    )
    parser.add_argument("--contact-email", help="Simula un contacto ya conocido -- email (resuelve {{contact.email}}).")
    parser.add_argument("--contact-phone", help="Simula un contacto ya conocido -- telefono (resuelve {{contact.phone}}).")
    parser.add_argument(
        "--contact-language", help="Simula un contacto ya conocido -- idioma preferido (resuelve {{contact.language}})."
    )
    parser.add_argument(
        "--auto-explore",
        action="store_true",
        help=(
            "Ignora --persona/--persona-file(s): lee el graph.json real del agente, encuentra cada "
            "pregunta de opcion multiple (`capture: Literal[...]` en un nodo `question`), y genera "
            "una corrida por cada valor posible, orientando al usuario simulado hacia esa opcion "
            "especifica -- cobertura sistematica del grafo en vez de personas escritas a mano. "
            "General para cualquier agente compilado, no sabe nada del dominio. Con --runs menor al "
            "total de opciones descubiertas, la cobertura de esta corrida queda parcial (round-robin)."
        ),
    )
    args = parser.parse_args(argv)

    known_agents = discover_compiled_agents()
    if args.agent not in known_agents:
        print(f"Agente {args.agent!r} no encontrado bajo compiled_agents/. Disponibles: {known_agents}", file=sys.stderr)
        return 1

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    for p in providers:
        if p not in SIMULATOR_FACTORIES:
            print(f"Proveedor desconocido {p!r}. Validos: {sorted(SIMULATOR_FACTORIES)}", file=sys.stderr)
            return 1

    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "test_runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    tools_base_url = args.tools_base_url or _tools_base_url()
    contact = {
        k: v
        for k, v in {
            "name": args.contact_name,
            "email": args.contact_email,
            "phone": args.contact_phone,
            "language": args.contact_language,
        }.items()
        if v is not None
    }
    harness = AgentTestHarness(args.agent, tools_base_url, contact=contact)

    targets: list[ExplorationTarget] = []
    entry_phrases: dict[str, str] = {}
    personas: list[tuple[str, str]] = []
    if args.auto_explore:
        targets = discover_exploration_targets(harness.artifact)
        entry_phrases = discover_namespace_entry_phrases(harness.artifact)
        if not targets:
            print("--auto-explore no encontro ningun nodo `question` con capture Literal[...] en este agente.", file=sys.stderr)
            return 1
        print(f"Agente: {args.agent}  |  Tools: {tools_base_url}")
        print(f"Auto-explore: {len(targets)} opciones descubiertas en el grafo real.")
        print(f"Namespaces solo alcanzables via trigger del router global: {sorted(entry_phrases)}")
        if args.runs < len(targets):
            print(
                f"AVISO: --runs {args.runs} < {len(targets)} opciones -- esta corrida no las cubre todas "
                "(round-robin, se repiten). Sube --runs para cobertura completa en una sola pasada."
            )
    else:
        personas = _load_personas(args)
        persona_names = [name for name, _ in personas]
        print(f"Agente: {args.agent}  |  Tools: {tools_base_url}")
        print(
            f"Corridas: {args.runs}  |  Proveedores (round-robin): {providers}  |  "
            f"Personas (round-robin): {persona_names}  |  Doubt-boost: {args.doubt_boost}  |  Salida: {out_dir}"
        )
    print()

    results: list[dict[str, Any]] = []
    for i in range(args.runs):
        provider = providers[i % len(providers)]
        if args.auto_explore:
            target = targets[i % len(targets)]
            persona_name = f"explore__{target.slot}__{target.value}"
            namespace = target.node_id.split("__", 1)[0]
            persona_text = build_auto_explore_persona(target, entry_phrases.get(namespace))
        else:
            target = None
            persona_name, persona_text = personas[i % len(personas)]
        print(f"[{i + 1}/{args.runs}] usuario simulado={provider}  persona={persona_name} ...", end=" ", flush=True)
        outcome = run_one_conversation(
            harness,
            persona_text,
            persona_name,
            provider,
            i,
            args.max_turns,
            business_context=args.business_context,
            doubt_boost=args.doubt_boost,
            exploration_target=target,
        )
        results.append(outcome)

        run_path = out_dir / f"{outcome['run_id']}.json"
        run_path.write_text(json.dumps(outcome, indent=2, ensure_ascii=False), encoding="utf-8")

        if outcome["ended_reason"] == "provider_error":
            print(f"FALLO DE PROVEEDOR (no es bug del agente): {outcome['error']['message']}")
        elif outcome["ended_reason"] == "agent_error":
            print(f"ERROR DEL AGENTE: {outcome['error']['message']}")
        else:
            print(f"ok -- {outcome['turn_count']} turnos, {outcome['tool_call_count']} tool calls, termino por: {outcome['ended_reason']}")

    summary_path = _write_summary(out_dir, results)
    print()
    print(f"Resumen: {summary_path}")
    n_provider_errors = sum(1 for r in results if r["ended_reason"] == "provider_error")
    if n_provider_errors:
        print(
            f"Aviso: {n_provider_errors}/{len(results)} corridas fallaron por el PROVEEDOR del usuario simulado "
            "(saldo/rate-limit/auth) -- no son bugs del agente. Revisa billing del proveedor correspondiente."
        )
    if args.auto_explore:
        reached = sum(1 for r in results if r.get("target_reached"))
        assigned = sum(1 for r in results if r.get("exploration_target") is not None)
        print(f"Cobertura auto-explore: {reached}/{assigned} opciones asignadas realmente se vieron en [final_slots].")
        print("(una opcion no alcanzada puede ser: el usuario simulado no logro orientar al agente ahi, o esa rama no era alcanzable con el resto de la conversacion -- revisar el run individual para saber cual.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
