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


ALLOWED_TOOL_LOG_SNIPPETS = (
    "Executing tool ",
    "Tool requested:",
    "Tool ",
    "end_call acknowledged locally.",
)


class SelectiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        logger_name = record.name or ""
        message = record.getMessage()

        if logger_name.startswith("uvicorn.access"):
            return True
        if logger_name.startswith("httpx") and "HTTP Request:" in message:
            return True
        if logger_name == "agents.family_aims_sam_text.endpoint":
            return True
        if logger_name == "agents.family_aims_sam.agent":
            if message.startswith("[") and (
                "Executing tool " in message
                or "Tool requested:" in message
                or " payload:" in message
                or "succeeded summary=" in message
                or "failed with HTTP" in message
                or "raised an exception:" in message
                or "end_call acknowledged locally." in message
            ):
                return True
            return False

        return False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
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
    app.include_router(load_agent_router(_agent_package), prefix=f"/{_slug}/v1", tags=[_slug])

# Pilot: compiled agent_compiler agents, served via agent_runtime — separate
# from the src/tools/* discovery above, since a compiled agent is not a
# tools backend. See compiled_runner/README (or SPEC notes) for scope.
from compiled_runner.endpoint import router as compiled_agent_router  # noqa: E402

app.include_router(compiled_agent_router, prefix="/compiled", tags=["compiled_agents"])


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
