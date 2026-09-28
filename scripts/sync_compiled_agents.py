"""Sincroniza el contenido compilado (agents/compiled/) hacia
agent_executor/compiled_agents/ -- la carpeta que compiled_runner/loader.py
realmente lee en tiempo de ejecucion.

Por que existe: agents/compiled/<slug>/ es la salida cruda de `agent-compiler`
(langgraph/graph.json, text/, diagram.html, ...), gitignoreada por completo en
el repo `agents` -- nunca viaja con un deploy. compiled_runner/loader.py
espera una carpeta plana `agent_executor/compiled_agents/<slug>/graph.json`.
Este script es el paso explicito que conecta ambas cosas: se corre a mano
(o como parte de publicar contenido nuevo) despues de compilar un agente,
nunca automaticamente al arrancar el servidor.

Nunca toca `sessions.db*` -- eso es estado real de conversaciones, no un
artefacto de compilacion.

Uso:
    python scripts/sync_compiled_agents.py [slug ...]

Sin argumentos, sincroniza todos los agentes que agents/compiled/ tenga
compilados.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_COMPILED_DIR = PROJECT_ROOT.parent / "agents" / "compiled"
COMPILED_AGENTS_DIR = PROJECT_ROOT / "compiled_agents"


def _sync_one(slug: str) -> bool:
    src_dir = AGENTS_COMPILED_DIR / slug
    graph_src = src_dir / "langgraph" / "graph.json"
    if not graph_src.exists():
        print(f"SKIP {slug}: no existe {graph_src}")
        return False

    dest_dir = COMPILED_AGENTS_DIR / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(graph_src, dest_dir / "graph.json")

    assets_src = src_dir / "assets.json"
    if assets_src.exists():
        shutil.copy2(assets_src, dest_dir / "assets.json")

    print(f"OK   {slug}")
    return True


def main() -> None:
    slugs = sys.argv[1:]
    if not slugs:
        if not AGENTS_COMPILED_DIR.exists():
            print(f"No existe {AGENTS_COMPILED_DIR} -- nada para sincronizar.")
            return
        slugs = sorted(p.name for p in AGENTS_COMPILED_DIR.iterdir() if p.is_dir())

    synced = sum(_sync_one(slug) for slug in slugs)
    print(f"\n{synced}/{len(slugs)} agente(s) sincronizado(s) hacia {COMPILED_AGENTS_DIR}")


if __name__ == "__main__":
    main()
