"""
Script para interactuar programáticamente con agentes compilados,
reusando el Harness del TUI original pero sin la interfaz interactiva de questionary.
Permite ver logs de LLM y HTTP en tiempo real.
"""

import sys
import uuid
import json
from pathlib import Path

# Configuración de rutas (espejo de compiled_agent_tui.py)
_here = Path(__file__).resolve()
def _find_project_root(start=_here):
    for p in [_here] + list(_here.parents):
        ac = p / "agents" / "compiled"
        if ac.exists():
            return p
    return _here.parents[2] # Ajuste según la ubicación en agent_executor/scripts/

PROJECT_ROOT = _find_project_root()
EXECUTOR_ROOT = PROJECT_ROOT / "agent_executor"
EXECUTOR_SRC = EXECUTOR_ROOT / "src"

# Inyectar rutas en sys.path
for candidate in (EXECUTOR_SRC, EXECUTOR_ROOT):
    candidate_str = str(candidate)
    if candidate.exists() and candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

# Importar el Harness del TUI original
# Nota: compiled_agent_tui.py tiene que estar en el path
sys.path.insert(0, str(EXECUTOR_ROOT / "scripts"))
from compiled_agent_tui import CompiledAgentHarness, console

def run_chat(agent_slug: str, log_llm: bool = True, log_http: bool = False):
    harness = CompiledAgentHarness(
        agent_slug=agent_slug,
        log_llm=log_llm,
        log_http=log_http
    )
    
    thread_id = f"prog-{uuid.uuid4().hex[:8]}"
    console.print(f"[bold green]Iniciando sesión: {thread_id} para agente: {agent_slug}[/bold green]")
    
    # Primer turno
    first_speaker = "agent" if "sam" in agent_slug else "user"
    is_first_turn = True
    
    if first_speaker == "agent":
        result = harness.start(thread_id, first_speaker=first_speaker)
        for msg in result.messages:
            console.print(f"[bold cyan]Bot:[/bold cyan] {msg}")
        is_first_turn = False
    
    console.print("[yellow]Escribe tu mensaje (o 'exit' para salir):[/yellow]")
    
    while True:
        try:
            user_input = input("> ").strip()
            if user_input.lower() in ("exit", "quit", "salir"):
                break
            if not user_input:
                continue
                
            if is_first_turn:
                result = harness.start(thread_id, first_speaker="user", opening_message=user_input)
                is_first_turn = False
            else:
                result = harness.turn(thread_id, user_input)
                
            for msg in result.messages:
                console.print(f"[bold cyan]Bot:[/bold cyan] {msg}")
                
            if result.conversation_ended:
                console.print("[bold red]La conversación ha terminado.[/bold red]")
                break
                
        except EOFError:
            break
        except Exception as e:
            console.print(f"[bold red]Error:[/bold red] {e}")

if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else "family_aims_sam_text"
    run_chat(slug)
