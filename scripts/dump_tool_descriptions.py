"""Vuelca a un .md exactamente lo que el LLM ve de cada tool de un agent.json:
la descripcion final (description + triggerCondition + notes + parametros +
inputs/outputs, todo aplanado) y el JSON schema de parametros, tanto en su
forma canonica de LangChain como ya envuelto en formato OpenAI (el que
realmente viaja en bind_tools()).

Corre el mismo pipeline que usa el agente en produccion
(agents.helpers.tools.load_tools_inventory -> build_canonical_tool_schemas ->
convert_to_openai_tools), sin necesitar credenciales de GHL ni llamar al LLM.
Sirve para revisar rapido si una nota/regla quedo bien redactada, o para
comparar el "antes" y "despues" de un cambio en agent.json.

Uso:
    python scripts/dump_tool_descriptions.py
    python scripts/dump_tool_descriptions.py --agent-json src/agents/family_aims_sam/agent.json
    python scripts/dump_tool_descriptions.py --out docs/tool_descriptions/sam.md
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for candidate in (PROJECT_ROOT, SRC_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from agents.helpers.settings import load_agent_definition, resolve_local_app_base_url
from agents.helpers.tools import (
    build_canonical_tool_schemas,
    convert_to_openai_tools,
    load_tools_inventory,
    resolve_tool_webhook,
    tool_is_executable,
)

DEFAULT_AGENT_JSON = SRC_ROOT / "agents" / "family_aims_sam" / "agent.json"

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("dump_tool_descriptions")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera un .md con la descripcion final y el schema de cada tool de un agent.json"
    )
    parser.add_argument(
        "--agent-json",
        default=str(DEFAULT_AGENT_JSON),
        help=f"Ruta al agent.json a inspeccionar (default: {DEFAULT_AGENT_JSON.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Ruta del .md de salida (default: docs/tool_descriptions/<agent>.md)",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Tambien intenta listar tools con enabled=false o sin webhook resoluble (se marcan como tal)",
    )
    return parser.parse_args()


def load_tools(agent_json_path: Path, include_disabled: bool) -> List[Dict[str, Any]]:
    agent_definition = load_agent_definition(agent_json_path)
    tool_config_by_name = {
        tool["name"]: tool
        for tool in agent_definition.get("tools", [])
        if include_disabled or tool.get("enabled", True)
    }

    def _resolve_webhook(tool: Dict[str, Any]) -> Any:
        return resolve_tool_webhook(
            tool,
            get_local_app_base_url=lambda: resolve_local_app_base_url(agent_definition),
            tool_call_default_seconds=10,
        )

    def _is_executable(tool: Dict[str, Any]) -> bool:
        if include_disabled:
            return True
        return tool_is_executable(tool, _resolve_webhook)

    return load_tools_inventory(
        agent_settings=agent_definition,
        tool_inventory_path=agent_definition.get("paths", {}).get("tool_inventory", ""),
        tool_config_by_name=tool_config_by_name,
        tool_is_executable=_is_executable,
        logger=logger,
    )


def render_markdown(agent_json_path: Path, agent_definition: Dict[str, Any], tools_inventory: List[Dict[str, Any]]) -> str:
    canonical_schemas = build_canonical_tool_schemas(tools_inventory)
    openai_schemas = convert_to_openai_tools(canonical_schemas)

    agent_meta = agent_definition.get("agent", {})
    lines: List[str] = [
        f"# Tool descriptions — {agent_meta.get('name', agent_json_path.stem)}",
        "",
        f"Generado desde `{agent_json_path.relative_to(PROJECT_ROOT)}` "
        f"(version `{agent_meta.get('version', '?')}`). Este es el texto y schema exactos que "
        "`ChatOpenAI.bind_tools()` recibe hoy (canonico LangChain -> convertido a OpenAI).",
        "",
        "## Indice",
        "",
    ]
    for schema in canonical_schemas:
        anchor = schema["name"].lower().replace("_", "-")
        lines.append(f"- [{schema['name']}](#{anchor})")
    lines.append("")

    for canonical, openai_schema in zip(canonical_schemas, openai_schemas):
        tool_name = canonical["name"]
        params_schema = canonical["parameters"]
        properties = params_schema.get("properties", {})
        required = set(params_schema.get("required", []))

        lines.append(f"## {tool_name}")
        lines.append("")
        lines.append("**Descripcion final (lo que el modelo lee en `function.description`):**")
        lines.append("")
        lines.append("```text")
        lines.append(canonical["description"])
        lines.append("```")
        lines.append("")

        if properties:
            lines.append("**Parametros:**")
            lines.append("")
            lines.append("| Nombre | Tipo | Requerido | Enum | Ejemplo | Descripcion |")
            lines.append("|---|---|---|---|---|---|")
            for param_name, param_schema in properties.items():
                enum_values = ", ".join(str(v) for v in param_schema.get("enum", [])) or "-"
                examples = param_schema.get("examples", [])
                example = examples[0] if examples else "-"
                description = (param_schema.get("description") or "").replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| `{param_name}` | {param_schema.get('type', 'string')} | "
                    f"{'si' if param_name in required else 'no'} | {enum_values} | {example} | {description} |"
                )
            lines.append("")
        else:
            lines.append("_Sin parametros._")
            lines.append("")

        lines.append("<details><summary>JSON schema OpenAI (formato real enviado a bind_tools)</summary>")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(openai_schema, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    agent_json_path = Path(args.agent_json).resolve()
    if not agent_json_path.exists():
        raise SystemExit(f"No existe {agent_json_path}")

    agent_definition = load_agent_definition(agent_json_path)
    tools_inventory = load_tools(agent_json_path, args.include_disabled)

    if args.out:
        out_path = Path(args.out)
    else:
        agent_slug = agent_definition.get("agent", {}).get("name", agent_json_path.parent.name).lower()
        out_path = PROJECT_ROOT / "docs" / "tool_descriptions" / f"{agent_slug}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    markdown = render_markdown(agent_json_path, agent_definition, tools_inventory)
    out_path.write_text(markdown, encoding="utf-8")

    print(f"{len(tools_inventory)} tool(s) documentadas -> {out_path}")


if __name__ == "__main__":
    main()
