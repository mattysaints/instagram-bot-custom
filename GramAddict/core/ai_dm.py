"""AI-powered Direct Message generation for the dm-followback job.

Backend: HuggingFace Space (mattysaints/instagram_bot), endpoint
``POST /api/generate_dm``. Il Space usa la stessa cascata di modelli di
ai_comment.py (Llama 3.3 70B -> Qwen 2.5 72B -> Mistral Small 3.1 24B)
con un prompt e guardrail specifici per DM (2-3 frasi, saluto + 1 domanda
aperta, opzionalmente 1 emoji leggera, no link/hashtag/CTA hard-sell).

Il modulo e' un thin HTTP client: prompt engineering, cascata e guardrail
sono lato Space. Sulla stessa riga di ai_comment.py, riusa i simboli
condivisi da quest'ultimo (autoload .env.local, circuit breaker,
network-down detection).

Fallback: se lo Space non risponde (rete giu', 5xx, timeout, guardrail
hit) il caller riceve None e usa il file ``pm_list.txt`` come fallback.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

# Condivide con ai_comment.py: autoload env, circuit breaker, timeout.
from GramAddict.core.ai_comment import (  # noqa: F401
    _autoload_env_local,
    _breaker_is_open,
    _breaker_open,
    _looks_like_network_down,
    _REQUEST_TIMEOUT_S,
    _DEFAULT_SPACE_URL,
)

logger = logging.getLogger(__name__)

_GENERATE_DM_PATH = "/api/generate_dm"


def _to_bool(value, default: bool = False) -> bool:
    """Coerce '--flag true/false/0/1/yes/no' into bool. Tollera None."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


def _get_space_url(args) -> str:
    """URL dello Space DM. Fallback: --ai-comments-space-url, poi default."""
    url = (
        getattr(args, "ai_dm_space_url", None)
        or getattr(args, "ai_comments_space_url", None)
        or _DEFAULT_SPACE_URL
    )
    return str(url).rstrip("/")


def _get_space_key(args) -> Optional[str]:
    """Bearer key. Fallback: --ai-comments-space-key, poi env."""
    return (
        getattr(args, "ai_dm_space_key", None)
        or getattr(args, "ai_comments_space_key", None)
        or os.environ.get("IG_COMMENT_SPACE_KEY")
        or None
    )


def is_enabled(args) -> bool:
    """True se la generazione AI dei DM e' abilitata.

    Convenzione: se ``ai-dm-enabled`` non e' settato esplicitamente, eredita
    da ``ai-comments-enabled``. Non e' piu' richiesta una API key locale: la
    vera key vive nei Secrets dello Space.
    """
    enabled = getattr(args, "ai_dm_enabled", None)
    if enabled is None:
        enabled = getattr(args, "ai_comments_enabled", False)
    if not enabled:
        return False
    return bool(_get_space_url(args))


def _call_space(
    space_url: str,
    space_key: Optional[str],
    payload: dict,
) -> Optional[str]:
    """Chiama /api/generate_dm. Ritorna il DM o None su errore."""
    try:
        import requests  # type: ignore
    except Exception as e:
        logger.warning(f"[ai-dm] 'requests' non disponibile: {e}")
        return None

    url = space_url + _GENERATE_DM_PATH
    headers = {"Content-Type": "application/json"}
    if space_key:
        headers["Authorization"] = f"Bearer {space_key}"

    try:
        r = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            timeout=_REQUEST_TIMEOUT_S,
        )
    except Exception as e:
        err_repr = repr(e)
        logger.warning(f"[ai-dm] space call network error: {e}")
        if _looks_like_network_down(err_repr):
            _breaker_open("network down detected on DM Space call")
        return None

    if r.status_code == 401:
        logger.warning("[ai-dm] Space returned 401 (invalid SPACE_API_KEY).")
        return None
    if r.status_code == 503:
        logger.info("[ai-dm] Space returned 503 (probable cold-start).")
        return None
    if r.status_code != 200:
        logger.warning(f"[ai-dm] Space HTTP {r.status_code}")
        return None

    try:
        data = r.json()
    except Exception as e:
        logger.warning(f"[ai-dm] Space non-JSON response: {e}")
        return None

    dm = data.get("dm")
    err = data.get("error")
    model_used = data.get("model_used")
    latency_ms = data.get("latency_ms")

    if dm:
        logger.info(
            f"[ai-dm] generated via {model_used} in {latency_ms}ms "
            f"(attempts={data.get('attempts')})"
        )
        return str(dm).strip()

    if err:
        logger.info(f"[ai-dm] Space returned no DM: {err}")
    return None


def generate_dm(
    args,
    target_username: Optional[str],
    full_name: Optional[str] = None,
    bio: Optional[str] = None,
    last_post_caption: Optional[str] = None,
) -> Optional[str]:
    """Genera un DM personalizzato. Ritorna None per fallback al pm_list.txt.

    Args:
        args: namespace CLI (deve avere ai_dm_* / ai_comments_* attributes).
        target_username: il follower destinatario.
        full_name: nome reale visualizzato sul profilo.
        bio: biografia dell'utente.
        last_post_caption: caption del post piu' recente, se estraibile.
    """
    if not is_enabled(args):
        return None
    if _breaker_is_open():
        logger.debug(
            "[ai-dm] circuit breaker aperto: skip diretto al fallback pm_list.txt."
        )
        return None

    space_url = _get_space_url(args)
    space_key = _get_space_key(args)
    hint = (
        getattr(args, "ai_dm_prompt_hint", None)
        or getattr(args, "ai_comments_prompt_hint", None)
        or None
    )
    language = (
        getattr(args, "ai_dm_language", None)
        or getattr(args, "ai_comments_language", None)
        or "Italian"
    )
    allow_emoji = _to_bool(getattr(args, "ai_dm_allow_emoji", True), default=True)

    payload = {
        "target_username": target_username,
        "full_name": full_name,
        "bio": bio,
        "last_post_caption": last_post_caption,
        "hint": hint,
        "language": language,
        "allow_emoji": allow_emoji,
    }

    return _call_space(space_url, space_key, payload)
