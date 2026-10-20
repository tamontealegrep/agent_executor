"""Vendoriza el engine desde agent_runtime hacia agent_executor/src/engine
sin ejecutar export, sin commits y sin push. Copia archivos en el working tree
para que puedas revisar/commitear manualmente después.

Origen:   ../agent_runtime/src/agent_compiler/
Destino:  ./src/engine/

Excluye:  tests/, __pycache__/, *.pyc

Uso:
    python scripts/vendor_engine_no_commit.py [--dry-run]

Notas:
- No modifica ni commitea en agent_runtime.
- No crea commits en agent_executor; solo modifica el working tree.
- Requiere que ambos repos existan como carpetas hermanas del proyecto.
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


AGENT_EXECUTOR_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = AGENT_EXECUTOR_ROOT.parent
AGENT_RUNTIME_ROOT = WORKSPACE_ROOT / "agent_runtime"
SRC_SUBDIR = AGENT_RUNTIME_ROOT / "src" / "agent_compiler"
DST_ENGINE_DIR = AGENT_EXECUTOR_ROOT / "src" / "engine"


EXCLUDE_DIRS = {"tests", "__pycache__"}
EXCLUDE_SUFFIXES = {".pyc"}


def _should_skip(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def _copy_tree(src: Path, dst: Path, dry_run: bool = False) -> None:
    for root, dirs, files in os.walk(src):
        root_path = Path(root)
        # Filtrar dirs in-place para que os.walk no descienda en excluidos
        dirs[:] = [d for d in dirs if not _should_skip(root_path / d)]

        rel = root_path.relative_to(src)
        target_root = dst / rel
        if not dry_run:
            target_root.mkdir(parents=True, exist_ok=True)

        for f in files:
            src_file = root_path / f
            if _should_skip(src_file):
                continue
            dst_file = target_root / f
            print(f"COPY  {src_file.relative_to(src)} -> engine/{rel / f}")
            if not dry_run:
                shutil.copy2(src_file, dst_file)


def _clean_dst(dst: Path, dry_run: bool = False) -> None:
    if not dst.exists():
        return
    # Borrado selectivo: todo el contenido del directorio engine
    for entry in dst.iterdir():
        # Conserva el paquete en caso de que no exista en el origen; de lo contrario será re-copiado
        if entry.is_dir():
            print(f"RM DIR engine/{entry.name}")
            if not dry_run:
                shutil.rmtree(entry)
        else:
            print(f"RM FILE engine/{entry.name}")
            if not dry_run:
                entry.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Solo mostrar acciones, no modificar archivos")
    args = parser.parse_args()

    if not SRC_SUBDIR.exists():
        raise SystemExit(
            f"No se encontró el origen: {SRC_SUBDIR}. Asegúrate de tener agent_runtime al lado del proyecto."
        )
    if not DST_ENGINE_DIR.exists():
        raise SystemExit(
            f"No se encontró el destino: {DST_ENGINE_DIR}. ¿Estás ejecutando dentro de agent_executor?"
        )

    print("== Vendor (sin commits) agent_runtime/src/agent_compiler -> agent_executor/src/engine ==")
    print(f"Origen:  {SRC_SUBDIR}")
    print(f"Destino: {DST_ENGINE_DIR}")
    if args.dry_run:
        print("(dry-run) No se escribirán cambios")

    _clean_dst(DST_ENGINE_DIR, dry_run=args.dry_run)
    _copy_tree(SRC_SUBDIR, DST_ENGINE_DIR, dry_run=args.dry_run)

    print("\nListo. Revisa 'git status' en agent_executor y haz commit/push manual si corresponde.")


if __name__ == "__main__":
    main()
