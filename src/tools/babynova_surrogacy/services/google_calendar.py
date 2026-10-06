"""Todo lo que habla por HTTP con Google Calendar vive aquÃ­. Nada de reglas de negocio."""

from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import quote

import httpx

from tools.babynova_surrogacy.utils.timezones import parse_iso


async def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> Optional[str]:
    """Intercambia el refresh token por un access token de Google OAuth."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json().get("access_token")


async def get_free_busy(
    access_token: str, calendar_id: str, time_min: datetime, time_max: datetime
) -> List[Tuple[datetime, datetime]]:
    """Consulta los intervalos ocupados del calendario en Google Calendar dentro del rango dado."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://www.googleapis.com/calendar/v3/freeBusy",
            json={
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "timeZone": "UTC",
                "items": [{"id": calendar_id}],
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        cal = (data.get("calendars") or {}).get(calendar_id) or {}
        busy = cal.get("busy") or []
        return [(parse_iso(b["start"]), parse_iso(b["end"])) for b in busy]


async def create_event(
    access_token: str,
    calendar_id: str,
    summary: str,
    description: str,
    start_dt: datetime,
    end_dt: datetime,
    attendee_email: str,
    request_id: str,
) -> dict:
    """Crea un evento en Google Calendar con videollamada de Google Meet y
    notifica al asistente (sendUpdates=all)."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events",
            params={"sendUpdates": "all", "conferenceDataVersion": 1},
            json={
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Bogota"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Bogota"},
                "attendees": [{"email": attendee_email}],
                "sendUpdates": "all",
                "conferenceData": {
                    "createRequest": {
                        "requestId": request_id,
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def list_events(
    access_token: str, calendar_id: str, time_min: datetime, time_max: datetime, query: Optional[str] = None
) -> List[dict]:
    """Lista eventos en el calendario dado dentro del rango de tiempo, manejando paginaciÃ³n."""
    events = []
    page_token = None
    
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            params = {
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": 250,
            }
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token

            resp = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events",
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            events.extend(data.get("items", []))
            
            page_token = data.get("nextPageToken")
            if not page_token:
                break
                
    return events


async def delete_event(access_token: str, calendar_id: str, event_id: str) -> bool:
    """Elimina un evento del calendario."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.delete(
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events/{event_id}",
            params={"sendUpdates": "all"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        # 204 No Content es Ã©xito en DELETE
        return resp.status_code == 204


async def update_event(
    access_token: str,
    calendar_id: str,
    event_id: str,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
) -> dict:
    """Actualiza parcialmente un evento en Google Calendar (PATCH)."""
    body = {}
    if summary:
        body["summary"] = summary
    if description:
        body["description"] = description
    if start_dt:
        body["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "America/Bogota"}
    if end_dt:
        body["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "America/Bogota"}

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.patch(
            f"https://www.googleapis.com/calendar/v3/calendars/{quote(calendar_id, safe='')}/events/{event_id}",
            params={"sendUpdates": "all"},
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        return resp.json()

