import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI


async def main() -> None:
    # Carga .env del hub (discovery lo hace al import)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # add agent_executor/ to sys.path
    from discovery import load_agent_router  # noqa: WPS433

    # Mini app con router de Family Aims montado
    app = FastAPI(title="Family Aims Tools — in-process")
    app.include_router(load_agent_router("family_aims"), prefix="/family_aims/v1", tags=["family_aims"])

    # Datos reales (de tu payload de ejemplo)
    body = {
        "name": "language",
        "value": "Portuguese",
        "contact_id": "vWfwGE4zG0YkWeiZtKGJ",
        "location_id": "vRUkD2IB8Fbbk2G865v3",
    }

    # Ejecuta request in-process
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        r = await client.post("/family_aims/v1/update_custom_field", json=body)
        print("status:", r.status_code)
        try:
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        except Exception:
            print(r.text)


if __name__ == "__main__":
    # Asegura que no intente barrer Postgres de background
    os.environ["DATABASE_URL"] = ""
    asyncio.run(main())
