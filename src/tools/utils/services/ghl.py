import logging
from typing import Optional, Dict, Any
import httpx
from instances.helpers.ghl import default_ghl_client_config, get_ghl_headers
from tools.utils.schemas.ghl import CustomValueResponse, GhlCustomValuesList

logger = logging.getLogger(__name__)

async def update_custom_field_service(
    name: str,
    value: str,
    contact_id: Optional[str] = None,
    location_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Unifica la actualización de campos personalizados en GHL.
    1. Si hay contact_id: Actualiza Custom Field del contacto.
    2. Si NO hay contact_id pero hay location_id: Actualiza Custom Value global.
    """
    config = default_ghl_client_config()
    headers = get_ghl_headers(config)

    if contact_id:
        # --- CASO A: ACTUALIZAR CONTACTO ---
        url = f"{config.base_url}/contacts/{contact_id}"
        standard_fields = {
            "firstName", "lastName", "email", "phone", "address1", "city", 
            "state", "country", "postalCode", "source", "dateOfBirth", "gender"
        }
        
        if name in standard_fields:
            payload = {name: value}
            field_id = name
        else:
            # Custom Field
            if len(name) >= 15 and any(c.isdigit() for c in name):
                field_id = name
            elif location_id:
                field_id = await _find_custom_field_id_by_name(location_id, name, config, headers)
                if not field_id:
                    return {"success": False, "type": "contact", "errors": f"Field '{name}' not found"}
            else:
                return {"success": False, "type": "contact", "errors": "location_id is required to search by name"}
            
            payload = {"customFields": [{"id": field_id, "value": value}]}
        
        try:
            async with httpx.AsyncClient(timeout=config.send_message_timeout_seconds) as client:
                response = await client.put(url, headers=headers, json=payload)
                response.raise_for_status()
                return {
                    "success": True, "type": "contact", "field_id": field_id, 
                    "field_name": name, "value": value
                }
        except Exception as e:
            return {"success": False, "type": "contact", "errors": str(e)}

    elif location_id:
        # --- CASO B: ACTUALIZAR CUSTOM VALUE GLOBAL ---
        list_url = f"{config.base_url}/locations/{location_id}/customValues"
        try:
            async with httpx.AsyncClient(timeout=config.history_timeout_seconds) as client:
                # Buscar ID por nombre
                response = await client.get(list_url, headers=headers)
                response.raise_for_status()
                cvs = response.json().get("customValues", [])
                target = next((cv for cv in cvs if cv.get("name", "").lower() == name.lower()), None)
                
                if not target:
                    return {"success": False, "type": "custom_value", "errors": f"Custom Value '{name}' not found"}
                
                # Actualizar
                update_url = f"{config.base_url}/locations/{location_id}/customValues/{target['id']}"
                payload = {"name": target["name"], "value": value}
                update_resp = await client.put(update_url, headers=headers, json=payload)
                update_resp.raise_for_status()
                
                return {
                    "success": True, "type": "custom_value", "field_id": target["id"], 
                    "field_name": target["name"], "value": value
                }
        except Exception as e:
            return {"success": False, "type": "custom_value", "errors": str(e)}

    return {"success": False, "type": "unknown", "errors": "Missing contact_id or location_id"}

async def _find_custom_field_id_by_name(location_id: str, name: str, config, headers: dict) -> Optional[str]:
    """Helper interno para mapear nombre de campo -> ID en una ubicación."""
    url = f"{config.base_url}/locations/{location_id}/customFields"
    try:
        async with httpx.AsyncClient(timeout=config.history_timeout_seconds) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            fields = resp.json().get("customFields", [])
            # Búsqueda por nombre (case-insensitive) o por fieldKey
            for f in fields:
                if f.get("name", "").lower() == name.lower() or f.get("fieldKey") == name:
                    return f.get("id")
    except Exception as e:
        logger.error(f"Error buscando custom field '{name}': {e}")
    return None
