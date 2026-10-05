import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI


def build_payload() -> dict:
    sample = Path(__file__).with_name("sample_payload.json")
    data = json.loads(sample.read_text(encoding="utf-8"))
    # Simula una petición real del contacto pidiendo cambiar idioma a portugués
    data["message"] = "cambia mi idioma a portugues"
    return data


async def main() -> None:
    # Evita que el startup intente barrer Postgres (no instalado en este entorno)
    os.environ["DATABASE_URL"] = ""

    # Construye una app mínima con el router de family_aims montado
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # add agent_executor/ to sys.path
    from discovery import load_agent_router  # noqa: WPS433

    mini_app = FastAPI(title="Mini app for sam_text simulation")
    mini_app.include_router(load_agent_router("family_aims"), prefix="/family_aims/v1", tags=["family_aims"])

    transport = httpx.ASGITransport(app=mini_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        payload = build_payload()
        r = await client.post("/family_aims/v1/sam_text", json=payload)
        print("status:", r.status_code)
        try:
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        except Exception:
            print(r.text)


if __name__ == "__main__":
    asyncio.run(main())
