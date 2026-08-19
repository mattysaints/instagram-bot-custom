"""Risposte agli sticker delle storie altrui (box domande, sondaggi, quiz).

Backend: HuggingFace Space (mattysaints/instagram_bot), endpoint
``POST /api/reply_to_sticker``. Lo Space genera una risposta CORTA, del tipo
che scriverebbe un follower qualunque: alla domanda "cosa alleni oggi?"
risponde "petto", non un trattato.

Come ai_comment.py e ai_dm.py, qui c'e' solo il client HTTP: prompt, guardrail
e cascata di modelli vivono nello Space. Se la chiamata fallisce si ritorna
None e il chiamante salta lo sticker (per uno sticker non esiste un fallback
da file: una risposta a caso sotto la domanda di un altro fa danno).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from GramAddict.core.ai_comment import (  # noqa: F401
    _autoload_env_local,
    _breaker_is_open,
    _breaker_open,
    _looks_like_network_down,
    _REQUEST_TIMEOUT_S,
    _DEFAULT_SPACE_URL,
)

logger = logging.getLogger(__name__)

_REPLY_STICKER_PATH = "/api/reply_to_sticker"

# Tipi di sticker riconosciuti lato Space.
STICKER_QUESTION = "question"
STICKER_POLL = "poll"
STICKER_QUIZ = "quiz"
STICKER_SLIDER = "slider"


def _get_space_url(args) -> str:
    url = (
        getattr(args, "ai_sticker_space_url", None)
        or getattr(args, "ai_comments_space_url", None)
        or _DEFAULT_SPACE_URL
    )
    return str(url).rstrip("/")


def _get_space_key(args) -> Optional[str]:
    return (
        getattr(args, "ai_sticker_space_key", None)
        or getattr(args, "ai_comments_space_key", None)
        or os.environ.get("IG_COMMENT_SPACE_KEY")
        or None
    )


def is_enabled(args) -> bool:
    """True se le risposte AI agli sticker sono attive.

    Se ``ai-sticker-enabled`` non e' impostato eredita da
    ``ai-comments-enabled``: chi ha acceso i commenti AI vuole quasi sempre
    anche questo.
    """
    enabled = getattr(args, "ai_sticker_enabled", None)
    if enabled is None:
        enabled = getattr(args, "ai_comments_enabled", False)
    if not enabled:
        return False
    return bool(_get_space_url(args))


def generate_sticker_reply(
    args,
    sticker_type: str,
    prompt_text: str,
    options: Optional[list] = None,
    author_username: Optional[str] = None,
) -> dict:
    """Chiede allo Space cosa rispondere a uno sticker.

    Returns:
        dict con:
          - reply:  testo da digitare (box domande), oppure None
          - choice: indice dell'opzione da toccare (sondaggio/quiz) o valore
            0-100 (slider), oppure None
          - refused: True se lo Space si e' rifiutato (tema medico/sensibile)
        Su errore tutti i campi sono None e refused e' False.
    """
    empty = {"reply": None, "choice": None, "refused": False}
    if not is_enabled(args):
        return empty
    if _breaker_is_open():
        logger.debug("[ai-sticker] circuit breaker aperto: salto lo sticker.")
        return empty

    try:
        import requests  # type: ignore
    except Exception as e:
        logger.warning(f"[ai-sticker] 'requests' non disponibile: {e}")
        return empty

    payload = {
        "sticker_type": sticker_type,
        "prompt_text": prompt_text or "",
        "options": options or None,
        "author_username": author_username,
        "hint": (
            getattr(args, "ai_sticker_prompt_hint", None)
            or getattr(args, "ai_comments_prompt_hint", None)
            or None
        ),
        "language": (
            getattr(args, "ai_sticker_language", None)
            or getattr(args, "ai_comments_language", None)
            or "Italian"
        ),
    }

    url = _get_space_url(args) + _REPLY_STICKER_PATH
    headers = {"Content-Type": "application/json"}
    key = _get_space_key(args)
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        r = requests.post(
            url, headers=headers, data=json.dumps(payload),
            timeout=_REQUEST_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[ai-sticker] errore di rete: {e}")
        if _looks_like_network_down(repr(e)):
            _breaker_open("network down detected on sticker call")
        return empty

    if r.status_code == 401:
        logger.warning("[ai-sticker] 401 dallo Space (SPACE_API_KEY errata).")
        return empty
    if r.status_code != 200:
        logger.warning(f"[ai-sticker] Space HTTP {r.status_code}")
        return empty

    try:
        data = r.json()
    except Exception as e:
        logger.warning(f"[ai-sticker] risposta non JSON: {e}")
        return empty

    if data.get("refused"):
        logger.info(
            f"[ai-sticker] sticker saltato, tema sensibile: {data.get('error')}"
        )
        return {"reply": None, "choice": None, "refused": True}

    reply = data.get("reply")
    choice = data.get("choice")
    if reply is None and choice is None:
        logger.info(f"[ai-sticker] nessuna risposta: {data.get('error')}")
        return empty

    logger.info(
        f"[ai-sticker] generata via {data.get('model_used')} in "
        f"{data.get('latency_ms')}ms"
    )
    return {"reply": reply, "choice": choice, "refused": False}
