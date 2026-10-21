"""
Punto de entrada raíz del proyecto.

Expone la app compuesta y, al ejecutarse como script, levanta uvicorn con la
configuración del .env del root.
"""

import sys
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError

from discovery import BASE_DIR, agent_slug, discover_agents, get_hub_settings, load_agent_router

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


class SelectiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        logger_name = record.name or ""
        message = record.getMessage()

        if logger_name.startswith("uvicorn.access"):
            return True
        # Allow httpx/httpcore only at WARNING or higher to reduce noise
        if logger_name.startswith("httpx") or logger_name.startswith("httpcore"):
            return record.levelno >= logging.WARNING
        if logger_name.startswith("compiled_runner.ghl_endpoint."):
            return True
        if logger_name == "compiled_runner.postgres":
            return True

        return False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Ensure noisy network libraries log only WARNING+ even if new handlers are added later
for _name in ("httpx", "httpx2", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(SelectiveLogFilter())
logger = logging.getLogger("ghl_hub")

app = FastAPI(title="GHL Tools")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    body_size = len(body)
    try:
        payload = await request.json()
        payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    except Exception:
        payload_keys = []
    logger.error(f"Validation error for {request.url}: {exc.errors()}")
    logger.error(f"Validation request summary: method={request.method} size_bytes={body_size} keys={payload_keys}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

for _agent_package in discover_agents():
    _slug = agent_slug(_agent_package)
    # Skip routers that are mounted explicitly to avoid double-mounts and mixed styles.
    # - utils lives under /tools/v1 (gateway)
    # - family_aims lives under /family_aims/v1 (gateway family_api_router)
    # - babynova_surrogacy lives under /babynova/v1 (gateway babynova_api_router)
    if _slug in {"utils", "family_aims", "babynova_surrogacy"}:
        continue
    app.include_router(load_agent_router(_agent_package), prefix=f"/{_slug}/v1", tags=[_slug])

# Pilot: compiled agent_compiler agents, served via agent_runtime — separate
# from the src/tools/* discovery above, since a compiled agent is not a
# tools backend. See compiled_runner/README (or SPEC notes) for scope.
compiled_agent_router = None
family_agents_router = None
babynova_agents_router = None
tools_utils_router = None
family_tools_router = None
babynova_tools_router = None

try:
    from compiled_runner.endpoint import router as compiled_agent_router  # type: ignore  # noqa: E402
except Exception as exc:  # pragma: no cover
    logger.warning("Compiled agents disabled: %s", exc)

try:
    from agents.family_aims.v1.router import family_agents_router as _family_agents_router  # type: ignore  # noqa: E402
    family_agents_router = _family_agents_router
except Exception as exc:  # pragma: no cover
    logger.warning("Family Aims agent endpoints disabled: %s", exc)

try:
    from agents.babynova.v1.router import babynova_agents_router as _babynova_agents_router  # type: ignore  # noqa: E402
    babynova_agents_router = _babynova_agents_router
except Exception as exc:  # pragma: no cover
    logger.warning("Babynova agent endpoints disabled: %s", exc)

try:
    from tools.gateway.api.v1.router import api_router as tools_utils_router  # type: ignore  # noqa: E402
except Exception as exc:  # pragma: no cover
    logger.error("Utils tools router failed to import: %s", exc)

try:
    from tools.family_aims.api.v1.router import api_router as family_tools_router  # type: ignore  # noqa: E402
except Exception as exc:  # pragma: no cover
    logger.warning("Family Aims tools disabled: %s", exc)

try:
    from tools.babynova_surrogacy.api.v1.router import api_router as babynova_tools_router  # type: ignore  # noqa: E402
except Exception as exc:  # pragma: no cover
    logger.warning("Babynova Surrogacy tools disabled: %s", exc)

if compiled_agent_router is not None:
    app.include_router(compiled_agent_router, prefix="/agents/compiled", tags=["compiled_agents"])

# Agents (Chat/Webhooks)
if family_agents_router is not None:
    app.include_router(family_agents_router, prefix="/agents/family_aims/v1")
if babynova_agents_router is not None:
    app.include_router(babynova_agents_router, prefix="/agents/babynova/v1")

# Tools (Functions)
if tools_utils_router is not None:
    app.include_router(tools_utils_router, prefix="/tools/utils/v1", tags=["tools.utils"])
if family_tools_router is not None:
    app.include_router(family_tools_router, prefix="/tools/family_aims/v1", tags=["tools.family_aims"])
if babynova_tools_router is not None:
    app.include_router(babynova_tools_router, prefix="/tools/babynova/v1", tags=["tools.babynova"])


@app.get("/api-contracts", include_in_schema=False)
def api_contracts() -> FileResponse:
    """Referencia HTML de entrada/salida de todos los tools."""
    return FileResponse(Path(__file__).resolve().parent / "API_CONTRACTS.html")


def main() -> None:
    settings = get_hub_settings()
    agents = discover_agents()

    if not agents:
        print("ERROR: no se encontró ningún subproyecto en src/tools/ con main.py", file=sys.stderr)
        sys.exit(1)

    print(f"Agentes: {', '.join(agents)}")
    print(f"Iniciando servidor en http://{settings.host}:{settings.port}")
    print(f"Docs interactivas en http://{settings.host}:{settings.port}/docs")
    print("Presiona CTRL+C para detener.\n")

    try:
        uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False, log_level="info")
    except Exception as exc:
        print(f"\nERROR al iniciar el servidor: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

