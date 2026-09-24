import re
import unicodedata
from typing import Any, Optional


def normalize_channel(value: Optional[str]) -> str:
    return (value or "").strip().upper().replace("-", "_")


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(value: Any) -> str:
    return strip_accents(str(value or "")).lower().strip()


def is_affirmative_message(message: str) -> bool:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalize_text(message))
    tokens = [token for token in normalized.split() if token]
    if not tokens:
        return False
    if tokens in (["si"], ["yes"], ["ok"], ["okay"], ["confirmo"], ["confirm"]):
        return True
    phrases = {
        "si la info es correcta",
        "si es correcta",
        "yes correct",
        "yes that is correct",
        "confirmo la informacion",
        "confirmo la info",
    }
    return " ".join(tokens) in phrases


def preview_text(value: str, limit: int = 120) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit - 3]}..."
