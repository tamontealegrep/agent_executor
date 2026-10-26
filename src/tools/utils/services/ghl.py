import logging
from typing import Optional, Dict, Any
import httpx
from agents.helpers.ghl import ghl_client_config_for_location, get_ghl_headers
from tools.utils.schemas.ghl import CustomValueResponse, GhlCustomValuesList

logger = logging.getLogger(__name__)

async def update_custom_field_service(
    name: str,
    value: str,
    contact_id: Optional[str] = None,
    location_id: Optional[str] = None
) -> Dict[str, Any]:
    """
update_custom_field_service.

    Unifica la actualización de campos personalizados en GHL.
        1. Si hay contact_id: Actualiza Custom Field del contacto.
        2. Si NO hay contact_id pero hay location_id: Actualiza Custom Value global.

    Parameters:
        name: Description.
        value: Description.
        contact_id: Description.
        location_id: Description.

    Returns:
        The result.
"""
    config = ghl_client_config_for_location(location_id)
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
                field = await _find_custom_field_by_name(location_id, name, config, headers)
                if not field:
                    return {"success": False, "type": "contact", "errors": f"Field '{name}' not found"}
                invalid = _invalid_options(field, value)
                if invalid:
                    allowed = ", ".join(field["picklistOptions"])
                    return {
                        "success": False, "type": "contact",
                        "errors": f"Invalid value(s) {invalid} for '{name}'. Allowed: {allowed}",
                    }
                field_id = field["id"]
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

_OPTION_DATA_TYPES = {"SINGLE_OPTIONS", "MULTIPLE_OPTIONS"}


def _invalid_options(field: Dict[str, Any], value: str) -> list[str]:
    """
Valores de `value` que no son opciones del campo desplegable (GHL los guarda igual).

    Un campo de selección múltiple recibe las opciones separadas por coma.
    Un campo que no es desplegable acepta cualquier valor.

    Parameters:
        field: Definición del campo devuelta por GHL.
        value: Valor que se quiere escribir.

    Returns:
        Los valores inválidos; lista vacía si todos son válidos.
"""
    options = field.get("picklistOptions")
    if field.get("dataType") not in _OPTION_DATA_TYPES or not options:
        return []
    values = [v.strip() for v in value.split(",")] if field["dataType"] == "MULTIPLE_OPTIONS" else [value]
    return [v for v in values if v not in options]


async def _find_custom_field_by_name(location_id: str, name: str, config, headers: dict) -> Optional[Dict[str, Any]]:
    """
Helper interno para mapear nombre de campo -> definición del campo en una ubicación.

    Parameters:
        location_id: Description.
        name: Description.
        config: Description.
        headers: Description.

    Returns:
        The result.
    """
    url = f"{config.base_url}/locations/{location_id}/customFields"
    try:
        async with httpx.AsyncClient(timeout=config.history_timeout_seconds) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            fields = resp.json().get("customFields", [])
            # Búsqueda por nombre (case-insensitive) o por fieldKey
            for f in fields:
                if f.get("name", "").lower() == name.lower() or f.get("fieldKey") == name:
                    return f
    except Exception as e:
        logger.error(f"Error buscando custom field '{name}': {e}")
    return None
