"""
AI-powered comment generation for GramAddict.

Backend: HuggingFace Space (mattysaints/instagram_bot).
Lo Space usa HF Inference Providers con cascata Llama 3.3 70B -> Qwen 2.5 72B
-> Mistral Small 3.1 24B. Vedi C:\\Users\\mat.marra\\PycharmProjects\\ig-comment-space
per il codice dello Space.

Strategia:
    - Il bot POSTa {caption, media_type, target_username, hint, language}
      al `/api/generate` dello Space.
    - Lo Space fa tutto: prompt engineering, chiamata LLM, cascata modelli,
      sanitize output, guardrail anti-emoji/#/!.
    - Se lo Space fallisce (rete giu', 5xx, timeout, response None), il bot
      cade sul file `comments_list.txt` come da configurazione.
    - Circuit breaker: se rileviamo che la rete e' down (DNS/refused/no
      route) apriamo il breaker per 10 minuti per non sprecare tempo.

I vecchi flag Gemini (--ai-comments-api-key, --ai-comments-model,
--ai-comments-models) e la env GEMINI_API_KEY sono stati rimossi.
Usa --ai-comments-space-url / --ai-comments-space-key.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env.local auto-loader (invariato rispetto alla versione Gemini).
# Serve a garantire che IG_COMMENT_SPACE_KEY sia sempre disponibile
# a prescindere da come e' stato lanciato il bot.
# ---------------------------------------------------------------------------
def _autoload_env_local() -> None:
    try:
        here = Path(__file__).resolve().parent
        for _ in range(6):
            candidate = here / ".env.local"
            if candidate.is_file():
                _parse_and_apply_env(candidate)
                return
            if here.parent == here:
                break
            here = here.parent
        cwd_candidate = Path.cwd() / ".env.local"
        if cwd_candidate.is_file():
            _parse_and_apply_env(cwd_candidate)
    except Exception as e:
        logger.debug(f"[ai-comment] autoload .env.local skipped: {e}")


def _parse_and_apply_env(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return
    loaded_keys = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if not k or os.environ.get(k):
            continue
        os.environ[k] = v
        loaded_keys.append(k)
    if loaded_keys:
        logger.debug(
            f"[ai-comment] auto-loaded {len(loaded_keys)} key(s) from {path.name}: "
            f"{', '.join(loaded_keys)}"
        )


_autoload_env_local()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_DEFAULT_SPACE_URL = "https://mattysaints-instagram-bot.hf.space"
_GENERATE_PATH = "/api/generate"

_REQUEST_TIMEOUT_S = 15  # lo Space chiama un LLM esterno, un po' piu' generoso di Gemini diretto

# ---------------------------------------------------------------------------
# Circuit breaker: se la rete e' DOWN (DNS bloccato, connessione rifiutata,
# no route) NON ha senso continuare a colpire lo Space per ogni commento.
# ---------------------------------------------------------------------------
_BREAKER_COOLDOWN_S = 600  # 10 minuti
_NETWORK_DOWN_MARKERS = (
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "no address associated with hostname",
    "newconnectionerror",
    "failed to establish a new connection",
    "network is unreachable",
    "no route to host",
    "connection refused",
)

_breaker_opened_at: Optional[float] = None


def _breaker_is_open() -> bool:
    global _breaker_opened_at
    if _breaker_opened_at is None:
        return False
    import time as _t
    elapsed = _t.time() - _breaker_opened_at
    if elapsed >= _BREAKER_COOLDOWN_S:
        _breaker_opened_at = None
        logger.info(
            f"[ai-comment] circuit breaker: cooldown di {_BREAKER_COOLDOWN_S}s "
            f"scaduto, ritento la rete."
        )
        return False
    return True


def _breaker_open(reason: str) -> None:
    global _breaker_opened_at
    import time as _t
    _breaker_opened_at = _t.time()
    logger.warning(
        f"[ai-comment] circuit breaker APERTO ({reason}). "
        f"Le prossime chiamate AI verranno skip-pate per "
        f"{_BREAKER_COOLDOWN_S}s (fallback diretto a comments_list.txt)."
    )


def _looks_like_network_down(err_repr: str) -> bool:
    low = err_repr.lower()
    return any(marker in low for marker in _NETWORK_DOWN_MARKERS)


# ---------------------------------------------------------------------------
# Space call
# ---------------------------------------------------------------------------
def _get_space_url(args) -> str:
    url = getattr(args, "ai_comments_space_url", None) or _DEFAULT_SPACE_URL
    return str(url).rstrip("/")


def _get_space_key(args) -> Optional[str]:
    return (
        getattr(args, "ai_comments_space_key", None)
        or os.environ.get("IG_COMMENT_SPACE_KEY")
        or None
    )


def is_enabled(args) -> bool:
    """True se la generazione AI e' abilitata.

    Non e' piu' richiesta una API key locale (la key vera vive nei Secrets
    dello Space). Basta che l'utente abbia messo `ai-comments-enabled: true`
    e un URL Space (che ha default).
    """
    if not getattr(args, "ai_comments_enabled", False):
        return False
    return bool(_get_space_url(args))


def _call_space(
    space_url: str,
    space_key: Optional[str],
    payload: dict,
) -> Optional[str]:
    """Chiama /api/generate. Ritorna il commento o None su qualunque errore."""
    try:
        import requests  # type: ignore
    except Exception as e:
        logger.warning(f"[ai-comment] 'requests' non disponibile: {e}")
        return None

    url = space_url + _GENERATE_PATH
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
        logger.warning(f"[ai-comment] space call network error: {e}")
        if _looks_like_network_down(err_repr):
            _breaker_open("network down detected on Space call")
        return None

    if r.status_code == 401:
        # key sbagliata: fatal, non ha senso ritentare
        logger.warning("[ai-comment] Space returned 401 (invalid SPACE_API_KEY).")
        return None
    if r.status_code == 503:
        # Space in cold-start / sleep: ritentare piu' tardi ha senso, ma per
        # ora skippiamo semplicemente al fallback.
        logger.info(f"[ai-comment] Space returned 503 (probable cold-start).")
        return None
    if r.status_code != 200:
        logger.warning(f"[ai-comment] Space HTTP {r.status_code}")
        return None

    try:
        data = r.json()
    except Exception as e:
        logger.warning(f"[ai-comment] Space non-JSON response: {e}")
        return None

    comment = data.get("comment")
    err = data.get("error")
    model_used = data.get("model_used")
    latency_ms = data.get("latency_ms")

    if comment:
        logger.info(
            f"[ai-comment] generated via {model_used} in {latency_ms}ms "
            f"(attempts={data.get('attempts')})"
        )
        return str(comment).strip()

    if err:
        logger.info(f"[ai-comment] Space returned no comment: {err}")
    return None


def generate_comment(
    args,
    caption: str,
    target_username: Optional[str],
    media_type: str,
) -> Optional[str]:
    """Genera un commento AI. Ritorna None per fallback al file txt.

    Args:
        args: namespace CLI (deve avere gli ai_comments_* attributes -
            vedi core_arguments.py). Se AI e' disabilitato, ritorna None.
        caption: caption del post. Stringa vuota OK.
        target_username: chi ha postato (per personalizzare il prompt).
            Puo' essere None.
        media_type: 'photo'|'video'|'reel'|'igtv'|'carousel' (case-free).
    """
    if not is_enabled(args):
        return None
    if _breaker_is_open():
        logger.debug(
            "[ai-comment] circuit breaker aperto: skip diretto al fallback txt."
        )
        return None

    space_url = _get_space_url(args)
    space_key = _get_space_key(args)
    hint = getattr(args, "ai_comments_prompt_hint", None) or None
    language = getattr(args, "ai_comments_language", None) or "Italian"

    payload = {
        "caption": caption or "",
        "media_type": str(media_type or "photo").lower(),
        "target_username": target_username,
        "hint": hint,
        "language": language,
    }

    return _call_space(space_url, space_key, payload)
