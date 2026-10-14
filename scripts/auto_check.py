import httpx
import asyncio
import sys

async def check_endpoint(client, method, url, name, payload=None):
    try:
        if method == "GET":
            resp = await client.get(url)
        else:
            resp = await client.post(url, json=payload or {})
        
        status = "✅" if resp.status_code < 400 else "❌"
        print(f"{status} {name:<40} | {method} {url:<60} | Status: {resp.status_code}")
        if resp.status_code >= 400:
            print(f"   └─ Error: {resp.text[:100]}")
        return resp.status_code < 400
    except Exception as e:
        print(f"❌ {name:<40} | {method} {url:<60} | FAILED: {e}")
        return False

async def main():
    base_url = "http://127.0.0.1:8010"
    print(f"\n--- Verificando Endpoints en {base_url} ---\n")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        results = []
        
        # 1. Agentes
        print("AGENTES:")
        results.append(await check_endpoint(client, "POST", f"{base_url}/agents/family_aims/v1/sam-text", "Family Aims SAM Text", 
            {"contact": {"phone": "+573215616921"}, "location_id": "test", "contact_id": "test"}))
        results.append(await check_endpoint(client, "POST", f"{base_url}/agents/babynova/v1/triage-text", "Babynova Triage Text", 
            {"contact": {"phone": "+573215616921"}, "location_id": "test", "contact_id": "test"}))
        
        # 2. Tools Utils
        print("\nTOOLS UTILS:")
        results.append(await check_endpoint(client, "POST", f"{base_url}/tools/utils/v1/time-now", "Time Now"))
        results.append(await check_endpoint(client, "POST", f"{base_url}/tools/utils/v1/calculate-bmi", "Calculate BMI", {"weight": 70, "height": 1.75}))
        
        # 3. Tools Family Aims
        print("\nTOOLS FAMILY AIMS:")
        results.append(await check_endpoint(client, "POST", f"{base_url}/tools/family_aims/v1/get-available-slots", "Get Slots", 
            {"calendar_type": "SUR", "iana_timezone": "America/Bogota"}))
        results.append(await check_endpoint(client, "POST", f"{base_url}/tools/family_aims/v1/check-visa", "Check Visa", {"nationalities": "USA"}))
        
        # 4. Tools Babynova
        print("\nTOOLS BABYNOVA:")
        results.append(await check_endpoint(client, "POST", f"{base_url}/tools/babynova/v1/get-available-slots", "Get Slots (BN)", 
            {"calendar_type": "SUR", "iana_timezone": "America/Bogota"}))
        results.append(await check_endpoint(client, "POST", f"{base_url}/tools/babynova/v1/surrogate-classification", "Classification", 
            {"age": 25, "bmi": 22, "previous_pregnancies": 1}))

        print("\n--- Resumen ---")
        if all(results):
            print("🎉 ¡Todos los endpoints están operando correctamente!")
        else:
            print("⚠️ Algunos endpoints fallaron. Revisa los errores arriba.")

if __name__ == "__main__":
    asyncio.run(main())
