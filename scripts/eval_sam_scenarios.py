"""Eval harness: corre escenarios de conversacion guionados contra Sam N veces
cada uno (mismo pipeline real que sam_tui.py, sin tocar GHL) y aplica un grader
mecanico sobre los traces + respuestas para detectar dos clases de falla ya
confirmadas en produccion:

  - RULE_PHANTOM_SUCCESS: Sam le dice al contacto que la cita quedo agendada
    sin que haya un book_appointment con success=true en ese turno o antes.
  - RULE_EMPTY_BOOKING_ARGS: Sam llama a book_appointment con contact_name/
    contact_email/contact_phone vacios (o sea, nunca pidio esos datos).

No es un juicio de calidad de respuesta - son chequeos estructurales sobre
datos que el propio agente ya expone (traces con args/response_body).

Dos modos:
  - Guionado (default): lista de mensajes fija, replica textual del caso
    reportado. Bueno para reproducir un bug puntual, malo para medir tasa de
    exito en general (si Sam se desvia, el guion se desincroniza).
  - Adaptativo (--adaptive): un "usuario simulado" responde segun lo que Sam
    pregunte (por keyword), no por posicion fija. Mas representativo, pero
    puede llegar a completar una reserva real - por eso hay limpieza
    automatica (cancel_appointment) si eso pasa.

Uso:
    python scripts/eval_sam_scenarios.py
    python scripts/eval_sam_scenarios.py --repeats 5
    python scripts/eval_sam_scenarios.py --scenario sur_booking_empty_contact
    python scripts/eval_sam_scenarios.py --adaptive --repeats 5
    python scripts/eval_sam_scenarios.py --adaptive --scenario adaptive_sur_empty_contact
    python scripts/eval_sam_scenarios.py --out-dir backups/eval_runs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for candidate in (PROJECT_ROOT, SRC_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from discovery import get_hub_settings  # carga el .env del root antes de leer OPENAI_API_KEY, etc.
from agents.family_aims_sam import agent as sam_agent
from sam_tui import LocalSamHarness, ToolTrace, _new_state  # reusa el mismo harness que la TUI


@contextmanager
def override_temperature(temperature: Optional[float]) -> Iterator[None]:
    """Monkeypatches sam_agent._model_settings so this eval run uses a
    different temperature than agent.json, without touching that file (which
    also drives production). Restores the original on exit regardless of
    temperature being None (no-op) or a real override.
    """
    if temperature is None:
        yield
        return

    original_model_settings = sam_agent._model_settings

    def _patched_model_settings() -> Dict[str, Any]:
        settings = dict(original_model_settings())
        settings["temperature"] = temperature
        return settings

    sam_agent._model_settings = _patched_model_settings
    try:
        yield
    finally:
        sam_agent._model_settings = original_model_settings

DEFAULT_OUT_DIR = PROJECT_ROOT / "backups" / "eval_runs"

SUCCESS_PHRASES = [
    "ha sido programada",
    "ha sido agendada",
    "quedo agendada",
    "queda agendada",
    "fue programada",
    "cita creada",
    "esta confirmada",
    "quedo confirmada",
    "quedo reservada",
]


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _contains_success_phrase(reply: str) -> Optional[str]:
    normalized = _strip_accents(reply.lower())
    for phrase in SUCCESS_PHRASES:
        if phrase in normalized:
            return phrase
    return None


@dataclass
class Scenario:
    name: str
    description: str
    turns: List[str]
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    channel: str = "SMS"


# Escenario 1: replica textual de la conversacion real donde se confirmo el bug
# (cita "programada" sin llamar book_appointment, datos de contacto nunca pedidos).
SCENARIO_SUR_EMPTY = Scenario(
    name="sur_booking_empty_contact",
    description="Subrogacion, contacto sin datos en el CRM (replica exacta del caso reportado).",
    turns=[
        "Hola",
        "me interesa el FIV",
        "si, puedo ir a bogota",
        "soy una mujer soltera",
        "espera y como seria el proceso de subrrogacion que ya me dio curiosidad",
        "el de subrrogacion",
        "soy de nicaragua",
        "la schengen",
        "no",
        "ya te dije que soy madre soltera",
        "estoy iniciando desde cro",
        "si",
        "estoy en yakarta",
        "la mas proxim",
        "la de las 11",
        "si",
        "si",
    ],
)

# Mismo guion exacto, pero con el contacto precargado como vendria de un CRM
# real con datos completos - aisla si el problema es "nunca pidio los datos"
# o si el booking fantasma pasa igual con datos disponibles.
SCENARIO_SUR_PREFILLED = Scenario(
    name="sur_booking_prefilled_contact",
    description="Mismo guion que sur_booking_empty_contact, pero con name/email/phone ya cargados.",
    turns=list(SCENARIO_SUR_EMPTY.turns),
    contact_name="Ana Martinez",
    contact_email="ana.martinez@samqa-eval.dev",
    contact_phone="+50588887777",
)

ALL_SCENARIOS: Dict[str, Scenario] = {
    scenario.name: scenario for scenario in (SCENARIO_SUR_EMPTY, SCENARIO_SUR_PREFILLED)
}


# --- Harness adaptativo -----------------------------------------------------
# En vez de una lista fija de mensajes, un "usuario simulado" que responde a
# lo que Sam realmente pregunto (por keyword), en el orden que sea. Evita que
# la conversacion se desincronice cuando Sam se desvia del camino esperado -
# ver la explicacion completa en la conversacion que origino este script.


@dataclass
class AdaptivePersona:
    """Que contesta el usuario simulado cuando Sam le pregunta esos datos -
    independiente de si el CRM (crm_contact_*) ya los tenia cargados o no.

    OJO: el dominio de email NO puede ser @example.* - booking_engine.py
    (`is_valid_email`) lo rechaza a proposito (guard anti datos de prueba,
    real, no un bug) y eso contamina la corrida con un fallo de validacion
    que no tiene nada que ver con lo que estamos midiendo.
    """

    spoken_name: str = "Valentina Reyes"
    spoken_email: str = "valentina.reyes@samqa-eval.dev"
    spoken_phone: str = "+50588889999"


def _answer_time(persona: AdaptivePersona, sam_reply: str) -> str:
    match = re.search(r"(\d{1,2}):00", sam_reply)
    if match:
        return f"la de las {match.group(1)}"
    return "la primera que tengas disponible"


# (patron regex sobre el reply de Sam normalizado, funcion que arma la respuesta)
# Orden = prioridad: la primera regla que matchea gana. "confirmas" va ANTES
# de nombre/correo/telefono porque el resumen (SC__SC_SUM) menciona esas
# palabras como etiquetas del resumen, pero la pregunta real de ese turno es
# la confirmacion, no una nueva solicitud de esos datos.
ADAPTIVE_RULES: List[Tuple[str, Callable[[AdaptivePersona, str], str]]] = [
    (r"fiv.*subrogaci|subrogaci.*fiv|convencional.*subrogada", lambda p, r: "me interesa la gestacion subrogada"),
    (r"heterosexual|pareja de mujeres|mujer soltera", lambda p, r: "soy una mujer soltera"),
    (r"padre o madre soltero", lambda p, r: "como madre soltera"),
    (r"visa.*(vigente|schengen|estados unidos)", lambda p, r: "si, tengo visa schengen vigente"),
    # Debe ir DESPUES de la regla de visa: la pregunta de visa suele mencionar
    # "tu nacionalidad" de pasada (ej. "Segun tu nacionalidad, la entrada..."),
    # asi que si la regla de nacionalidad fuera mas amplia o fuera antes,
    # dispararia ahi en vez de contestar la pregunta real (visa). Exigimos
    # que sea la pregunta directa, no una mencion de paso.
    (r"cual es tu nacionalidad|nacionalidad\?", lambda p, r: "soy de nicaragua"),
    (r"primer acercamiento|primera vez", lambda p, r: "no, ya habia contactado antes"),
    (r"tratamientos.*previamente|iniciando desde cero|desde cero", lambda p, r: "estoy iniciando desde cero"),
    (r"desplazarte a bogota|puedes.*bogota", lambda p, r: "si, puedo ir a bogota"),
    (r"confirmas que esta informacion|es correcta para crear la cita|confirmas\b", lambda p, r: "si"),
    (r"pais y ciudad|en que ciudad|donde te encuentras", lambda p, r: "estoy en yakarta"),
    (r"que dia te queda|estas fechas|disponibilidad en estas fechas", lambda p, r: "la mas proxima"),
    (r"hora te funciona|estas son las horas", _answer_time),
    (r"nombre completo|cual es tu nombre|tu nombre", lambda p, r: p.spoken_name),
    (r"correo|email", lambda p, r: p.spoken_email),
    (r"telefono", lambda p, r: p.spoken_phone),
    (r"te gustaria.*(revisemos|agendar|disponibilidad)|comencemos con el agendamiento", lambda p, r: "si"),
]


def next_adaptive_message(persona: AdaptivePersona, sam_reply: str) -> Tuple[str, bool]:
    """Devuelve (mensaje, matched). matched=False = ninguna regla reconocio
    la pregunta de Sam y se uso un 'si' generico como fallback."""
    normalized = _strip_accents(sam_reply).lower()
    for pattern, answer_fn in ADAPTIVE_RULES:
        if re.search(pattern, normalized):
            return answer_fn(persona, sam_reply), True
    return "si", False


@dataclass
class AdaptiveScenario:
    name: str
    description: str
    persona: AdaptivePersona
    crm_contact_name: Optional[str] = None
    crm_contact_email: Optional[str] = None
    crm_contact_phone: Optional[str] = None
    channel: str = "SMS"
    max_turns: int = 25
    fallback_streak_limit: int = 3


ADAPTIVE_SUR_EMPTY = AdaptiveScenario(
    name="adaptive_sur_empty_contact",
    description="Subrogacion, misma persona/objetivo que el caso reportado, contacto CRM vacio, pero respondiendo a lo que Sam realmente pregunte.",
    persona=AdaptivePersona(),
)

ADAPTIVE_SUR_PREFILLED = AdaptiveScenario(
    name="adaptive_sur_prefilled_contact",
    description="Igual, pero con el contacto ya cargado en el CRM (name/email/phone consistentes con lo que la persona diria si se lo preguntan igual).",
    persona=AdaptivePersona(
        spoken_name="Ana Martinez", spoken_email="ana.martinez@samqa-eval.dev", spoken_phone="+50588887777"
    ),
    crm_contact_name="Ana Martinez",
    crm_contact_email="ana.martinez@samqa-eval.dev",
    crm_contact_phone="+50588887777",
)

ADAPTIVE_SCENARIOS: Dict[str, AdaptiveScenario] = {
    scenario.name: scenario for scenario in (ADAPTIVE_SUR_EMPTY, ADAPTIVE_SUR_PREFILLED)
}


@dataclass
class TurnRecord:
    turn_index: int
    user_message: str
    reply: str
    traces: List[Dict[str, Any]]


@dataclass
class Violation:
    rule: str
    turn_index: int
    detail: str


@dataclass
class RunResult:
    scenario_name: str
    repeat_index: int
    temperature: Optional[float]
    turns: List[TurnRecord]
    violations: List[Violation]
    error: Optional[str] = None
    cleanups: List[Dict[str, Any]] = field(default_factory=list)
    stopped_reason: Optional[str] = None


def _trace_to_dict(trace: ToolTrace) -> Dict[str, Any]:
    return {
        "tool_name": trace.tool_name,
        "args": trace.args,
        "status_code": trace.status_code,
        "response_body": trace.response_body,
    }


def _is_successful_book_appointment(trace_dict: Dict[str, Any]) -> bool:
    if trace_dict["tool_name"] != "book_appointment":
        return False
    body = trace_dict.get("response_body")
    return isinstance(body, dict) and body.get("success") is True


def grade_conversation(turns: List[TurnRecord]) -> List[Violation]:
    violations: List[Violation] = []
    any_success_so_far = False
    previous_reply: Optional[str] = None

    for turn in turns:
        if turn.reply == previous_reply:
            violations.append(
                Violation(
                    rule="RULE_REPEATED_REPLY",
                    turn_index=turn.turn_index,
                    detail=f"Sam repitio textualmente la misma respuesta que en el turno anterior: {turn.reply!r}",
                )
            )
        previous_reply = turn.reply

        for trace_dict in turn.traces:
            if _is_successful_book_appointment(trace_dict):
                any_success_so_far = True
            if trace_dict["tool_name"] == "book_appointment":
                args = trace_dict.get("args") or {}
                empty_fields = [
                    field_name
                    for field_name in ("contact_name", "contact_email", "contact_phone")
                    if not str(args.get(field_name) or "").strip()
                ]
                if empty_fields:
                    violations.append(
                        Violation(
                            rule="RULE_EMPTY_BOOKING_ARGS",
                            turn_index=turn.turn_index,
                            detail=f"book_appointment llamado con campos vacios: {empty_fields} (args={args})",
                        )
                    )

        matched_phrase = _contains_success_phrase(turn.reply)
        if matched_phrase and not any_success_so_far:
            violations.append(
                Violation(
                    rule="RULE_PHANTOM_SUCCESS",
                    turn_index=turn.turn_index,
                    detail=f"Sam dijo '...{matched_phrase}...' sin book_appointment success=true previo. reply={turn.reply!r}",
                )
            )

    return violations


async def cleanup_successful_bookings(turns: List[TurnRecord]) -> List[Dict[str, Any]]:
    """LocalSamHarness only mocks the GHL side (search/fetch/send) - a real
    book_appointment success here creates a REAL event on the real Family
    Aims Google Calendar, same as production. Cancel each one right after
    grading so eval runs never leave live test appointments behind.
    """
    cleanups: List[Dict[str, Any]] = []
    for turn in turns:
        for trace_dict in turn.traces:
            if not _is_successful_book_appointment(trace_dict):
                continue
            body = trace_dict.get("response_body") or {}
            event_id = body.get("event_id")
            if not event_id:
                continue
            args = trace_dict.get("args") or {}
            cancel_args = {
                "event_id": event_id,
                "calendar_type": args.get("calendar_type", "SUR"),
                "iana_timezone": args.get("iana_timezone") or "America/Bogota",
            }
            cancel_result = await sam_agent._execute_custom_tool(
                "cancel_appointment", cancel_args, f"eval-cleanup-{event_id}"
            )
            cleanups.append({"event_id": event_id, "cancel_args": cancel_args, "cancel_result": cancel_result})
    return cleanups


async def run_scenario_once(scenario: Scenario, repeat_index: int, temperature: Optional[float]) -> RunResult:
    state = _new_state(
        contact_id=f"eval-{scenario.name}-{repeat_index}",
        location_id="eval-location",
        channel=scenario.channel,
        contact_name=scenario.contact_name,
        contact_email=scenario.contact_email,
        contact_phone=scenario.contact_phone,
    )
    harness = LocalSamHarness(state)
    turns: List[TurnRecord] = []

    try:
        with override_temperature(temperature):
            for index, message in enumerate(scenario.turns, start=1):
                result = await harness.ask(message)
                turns.append(
                    TurnRecord(
                        turn_index=index,
                        user_message=message,
                        reply=result.reply,
                        traces=[_trace_to_dict(trace) for trace in result.traces],
                    )
                )
    except Exception as exc:  # noqa: BLE001 - queremos capturar cualquier falla y seguir con las demas corridas
        violations = grade_conversation(turns)
        cleanups = await cleanup_successful_bookings(turns)
        return RunResult(
            scenario_name=scenario.name,
            repeat_index=repeat_index,
            temperature=temperature,
            turns=turns,
            violations=violations,
            error=f"{type(exc).__name__}: {exc}",
            cleanups=cleanups,
        )

    violations = grade_conversation(turns)
    cleanups = await cleanup_successful_bookings(turns)
    return RunResult(
        scenario_name=scenario.name,
        repeat_index=repeat_index,
        temperature=temperature,
        turns=turns,
        violations=violations,
        cleanups=cleanups,
    )


async def run_adaptive_scenario_once(
    scenario: AdaptiveScenario, repeat_index: int, temperature: Optional[float]
) -> RunResult:
    state = _new_state(
        contact_id=f"eval-{scenario.name}-{repeat_index}",
        location_id="eval-location",
        channel=scenario.channel,
        contact_name=scenario.crm_contact_name,
        contact_email=scenario.crm_contact_email,
        contact_phone=scenario.crm_contact_phone,
    )
    harness = LocalSamHarness(state)
    turns: List[TurnRecord] = []
    sam_reply = ""
    previous_sam_reply: Optional[str] = None
    fallback_streak = 0
    repeated_reply_streak = 0
    stopped_reason = "max_turns_exhausted"

    try:
        with override_temperature(temperature):
            for index in range(1, scenario.max_turns + 1):
                if index == 1:
                    user_message, matched = "Hola", True
                else:
                    user_message, matched = next_adaptive_message(scenario.persona, sam_reply)

                result = await harness.ask(user_message)
                sam_reply = result.reply
                trace_dicts = [_trace_to_dict(trace) for trace in result.traces]
                turns.append(TurnRecord(turn_index=index, user_message=user_message, reply=sam_reply, traces=trace_dicts))

                fallback_streak = 0 if matched else fallback_streak + 1
                # El "si"/"no" del usuario simulado repitiendose entre turnos es
                # normal (dos preguntas distintas seguidas pueden contestarse
                # igual). Lo que SI delata un loop real es que Sam responda
                # textualmente lo mismo dos veces seguidas.
                repeated_reply_streak = repeated_reply_streak + 1 if sam_reply == previous_sam_reply else 0
                previous_sam_reply = sam_reply

                if any(_is_successful_book_appointment(t) for t in trace_dicts):
                    stopped_reason = "book_appointment_success"
                    break
                if _contains_success_phrase(sam_reply):
                    stopped_reason = "success_phrase_without_prior_booking"
                    break
                if repeated_reply_streak >= 2:
                    stopped_reason = "sam_repeated_identical_reply"
                    break
                if fallback_streak >= scenario.fallback_streak_limit:
                    stopped_reason = f"stuck_no_rule_matched_{fallback_streak}x"
                    break
    except Exception as exc:  # noqa: BLE001
        violations = grade_conversation(turns)
        cleanups = await cleanup_successful_bookings(turns)
        return RunResult(
            scenario_name=scenario.name,
            repeat_index=repeat_index,
            temperature=temperature,
            turns=turns,
            violations=violations,
            error=f"{type(exc).__name__}: {exc}",
            cleanups=cleanups,
            stopped_reason="exception",
        )

    violations = grade_conversation(turns)
    cleanups = await cleanup_successful_bookings(turns)
    return RunResult(
        scenario_name=scenario.name,
        repeat_index=repeat_index,
        temperature=temperature,
        turns=turns,
        violations=violations,
        cleanups=cleanups,
        stopped_reason=stopped_reason,
    )


def _run_to_json(run: RunResult) -> Dict[str, Any]:
    return {
        "scenario_name": run.scenario_name,
        "repeat_index": run.repeat_index,
        "temperature": run.temperature,
        "error": run.error,
        "stopped_reason": run.stopped_reason,
        "violations": [asdict(violation) for violation in run.violations],
        "cleanups": run.cleanups,
        "turns": [asdict(turn) for turn in run.turns],
    }


def print_summary(results: List[RunResult]) -> None:
    print("\n=== RESUMEN ===")
    by_group: Dict[tuple, List[RunResult]] = {}
    for run in results:
        by_group.setdefault((run.scenario_name, run.temperature), []).append(run)

    for (scenario_name, temperature), runs in by_group.items():
        total = len(runs)
        clean = sum(1 for run in runs if not run.violations and not run.error)
        errored = sum(1 for run in runs if run.error)
        phantom = sum(1 for run in runs if any(v.rule == "RULE_PHANTOM_SUCCESS" for v in run.violations))
        empty_args = sum(1 for run in runs if any(v.rule == "RULE_EMPTY_BOOKING_ARGS" for v in run.violations))
        repeated = sum(1 for run in runs if any(v.rule == "RULE_REPEATED_REPLY" for v in run.violations))
        temp_label = "agent.json default" if temperature is None else f"temperature={temperature}"

        real_success = sum(
            1
            for run in runs
            if any(_is_successful_book_appointment(t) for turn in run.turns for t in turn.traces)
        )

        print(f"\n[{scenario_name} | {temp_label}] {total} corrida(s)")
        print(f"  limpias (sin violaciones, sin error): {clean}/{total}")
        print(f"  book_appointment REAL exitoso: {real_success}/{total}")
        print(f"  con RULE_PHANTOM_SUCCESS (dijo 'agendada' sin tool exitosa): {phantom}/{total}")
        print(f"  con RULE_EMPTY_BOOKING_ARGS (booking con datos vacios): {empty_args}/{total}")
        print(f"  con RULE_REPEATED_REPLY (Sam se repitio textual): {repeated}/{total}")
        if errored:
            print(f"  corridas con excepcion: {errored}/{total}")
        stopped_reasons = [run.stopped_reason for run in runs if run.stopped_reason]
        if stopped_reasons:
            print(f"  motivos de corte: {stopped_reasons}")
        for run in runs:
            if run.error:
                print(f"    - repeat {run.repeat_index}: ERROR {run.error}")
            for violation in run.violations:
                print(f"    - repeat {run.repeat_index} turno {violation.turn_index} [{violation.rule}]: {violation.detail[:160]}")
            for cleanup in run.cleanups:
                cancel_ok = isinstance(cleanup["cancel_result"], dict) and cleanup["cancel_result"].get("success")
                print(
                    f"    - repeat {run.repeat_index}: CITA REAL creada (event_id={cleanup['event_id']}) "
                    f"-> cancelada automaticamente: {'OK' if cancel_ok else cleanup['cancel_result']}"
                )


async def main_async(
    scenario_names: List[str], repeats: int, out_dir: Path, temperature: Optional[float], adaptive: bool
) -> None:
    get_hub_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    results: List[RunResult] = []
    for scenario_name in scenario_names:
        for repeat_index in range(1, repeats + 1):
            print(f"--- corriendo {scenario_name} repeat {repeat_index}/{repeats} (temperature={temperature}) ---")
            if adaptive:
                run = await run_adaptive_scenario_once(ADAPTIVE_SCENARIOS[scenario_name], repeat_index, temperature)
            else:
                run = await run_scenario_once(ALL_SCENARIOS[scenario_name], repeat_index, temperature)
            results.append(run)
            status = "ERROR" if run.error else ("OK" if not run.violations else f"{len(run.violations)} violacion(es)")
            print(f"    -> {status} (turnos={len(run.turns)}, corte={run.stopped_reason})")

    prefix = "eval_adaptive" if adaptive else "eval"
    out_file = out_dir / f"{prefix}_{timestamp}.json"
    out_file.write_text(
        json.dumps([_run_to_json(run) for run in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nResultados detallados guardados en {out_file}")

    print_summary(results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Corre escenarios contra Sam y grada las respuestas")
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="Usa el harness adaptativo (usuario simulado responde a lo que Sam pregunte) en vez del guion fijo.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Escenario a correr (repetible). Default: todos los del modo elegido.",
    )
    parser.add_argument("--repeats", type=int, default=3, help="Repeticiones por escenario (default: 3)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Directorio para el JSON de resultados")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Overridea model.temperature de agent.json solo para esta corrida (no toca el archivo). Default: usa el de agent.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    registry = ADAPTIVE_SCENARIOS if args.adaptive else ALL_SCENARIOS
    for name in args.scenario or []:
        if name not in registry:
            raise SystemExit(f"Escenario desconocido para este modo: {name!r}. Disponibles: {sorted(registry.keys())}")
    scenario_names = args.scenario or sorted(registry.keys())
    asyncio.run(main_async(scenario_names, args.repeats, Path(args.out_dir), args.temperature, args.adaptive))


if __name__ == "__main__":
    main()
