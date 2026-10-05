import asyncio
import os
import sys
from pathlib import Path

# Añadir src al path para poder importar los módulos locales
BASE_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(BASE_DIR))

from tools.utils.services.ghl import update_custom_field_service
from dotenv import load_dotenv

async def run_test():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    
    contact_id = "vWfwGE4zG0YkWeiZtKGJ"
    location_id = "vRUkD2IB8Fbbk2G865v3"
    new_language = "Spanish"
    
    print("--- INICIANDO PRUEBA REAL DE ACTUALIZACIÓN GHL (CUSTOM FIELDS) ---")
    
    # 0. Debug: Ver contacto actual
    print(f"\n[0] Verificando estado actual del contacto {contact_id}...")
    from instances.helpers.ghl import default_ghl_client_config, get_ghl_headers
    import httpx
    cfg = default_ghl_client_config()
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{cfg.base_url}/contacts/{contact_id}", headers=get_ghl_headers(cfg))
        if resp.status_code == 200:
            contact_data = resp.json().get("contact", {})
            print(f"   Nombre: {contact_data.get('firstName')} {contact_data.get('lastName')}")
            print(f"   Email: {contact_data.get('email')}")
            # Ver campos personalizados
            cfs = contact_data.get("customFields", [])
            print(f"   Campos Personalizados (resumen): {len(cfs)} encontrados")
            for cf in cfs:
                print(f"     - {cf}")
        else:
            print(f"   ❌ Error al obtener contacto: {resp.status_code} {resp.text}")

    # 1. Prueba de actualización de CONTACTO vía herramienta de campos personalizados
    print(f"\n[1] Intentando actualizar idioma del contacto {contact_id} (vía update_custom_field)...")
    # Pasamos 'language' como nombre y el location_id para que lo resuelva solo
    contact_result = await update_custom_field_service(
        name="language",
        value=new_language,
        contact_id=contact_id,
        location_id=location_id
    )
    if contact_result.get("success"):
        print(f"✅ ÉXITO: El idioma del contacto ({contact_result['field_id']}) ha sido actualizado a '{new_language}' en GHL.")
    else:
        print(f"❌ ERROR al actualizar contacto: {contact_result.get('errors')}")

    # 2. Prueba de actualización de CUSTOM VALUE global
    print(f"\n[2] Intentando actualizar Custom Value global 'language'...")
    cv_result = await update_custom_field_service(
        name="language",
        value=new_language,
        location_id=location_id
    )
    if cv_result.get("success"):
        print(f"✅ ÉXITO: El Custom Value global '{cv_result['field_name']}' ahora es '{cv_result['value']}'.")
    else:
        print(f"❌ AVISO esperado: {cv_result.get('errors')}")

    print("\n--- PRUEBA FINALIZADA ---")

if __name__ == "__main__":
    asyncio.run(run_test())
