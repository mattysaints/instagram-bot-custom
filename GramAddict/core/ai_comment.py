"""
AI-powered comment generation for GramAddict.

Ispirato a https://github.com/dzeveckij/instagram-ai-commenter-bot
(adattato da Playwright/Node a uiautomator2/Python).

Strategia:
    - Provider attualmente supportato: Google Gemini (REST API, no SDK).
    - Input: caption del post (se estraibile via UI), media_type, hint
      opzionale specifico per l'account, lingua target.
    - Output: SINGOLO commento corto, plain text, senza emoji/hashtag/!.
    - Fallback robusto: se la chiamata fallisce (no key, rete giu', rate
      limit, prompt-injection rifiutato dal modello), il caller riceve
      None e usa il file `comments_list.txt` come fallback.

Le regole anti-bot (no emoji/hashtag/exclamation/I-me/generic) ricalcano
lo stile del repo TypeScript di riferimento, perche' sono empiricamente
quelle che fanno apparire i commenti AI come "umani" agli occhi sia di
Instagram che di chi legge.

NB: nessun logging della API key, nessun crash hard se la dipendenza
manca: tutto viene gestito a livello di flag `enabled`/return-None.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# .env.local auto-loader: carichiamo le credenziali (GEMINI_API_KEY, ecc.)
# direttamente all'import, cosi' chi importa questo modulo ha SEMPRE le env
# vars disponibili a prescindere da come ha lanciato il bot:
#   - run-bot.sh           -> gia' faceva 'source .env.local'
#   - run-dynamic.py       -> gia' parsava .env.local prima di subprocess
#   - python run.py ...    -> NON caricava nulla -> ai_comment falliva
#   - IDE PyCharm run cfg  -> idem
# Cerchiamo .env.local risalendo le directory fino alla root del repo
# (max 5 livelli) per supportare cwd diverse. Non sovrascriviamo env vars
# gia' settate (chi le ha gia' export-ate manualmente vince).
# ---------------------------------------------------------------------------
def _autoload_env_local() -> None:
    """Carica .env.local in os.environ se trovato.

    Idempotente: chi ha gia' exportato GEMINI_API_KEY a mano (es. da shell
    rc) NON viene sovrascritto. Tutto in try/except: un .env.local malformato
    non deve mai bloccare l'import del modulo.
    """
    try:
        # parti dal file ai_comment.py e risali al massimo 5 livelli
        here = Path(__file__).resolve().parent
        for _ in range(6):
            candidate = here / ".env.local"
            if candidate.is_file():
                _parse_and_apply_env(candidate)
                return
            if here.parent == here:  # root filesystem raggiunta
                break
            here = here.parent
        # fallback: cwd corrente (utile se l'utente lo mette altrove)
        cwd_candidate = Path.cwd() / ".env.local"
        if cwd_candidate.is_file():
            _parse_and_apply_env(cwd_candidate)
    except Exception as e:
        # NON loggare lo stack: vogliamo un import 100% silenzioso
        logger.debug(f"[ai-comment] autoload .env.local skipped: {e}")


def _parse_and_apply_env(path: Path) -> None:
    """Parser minimale (no python-dotenv dependency): ignora commenti,
    supporta `export KEY=val`, `KEY=val`, e value virgolettati."""
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
        if not k:
            continue
        # NON sovrascrivere env vars gia' settate (precedenza all'utente)
        if os.environ.get(k):
            continue
        os.environ[k] = v
        loaded_keys.append(k)
    if loaded_keys:
        logger.debug(
            f"[ai-comment] auto-loaded {len(loaded_keys)} key(s) from {path.name}: "
            f"{', '.join(loaded_keys)}"
        )


# Esegui all'import (una volta sola).
_autoload_env_local()

# Endpoint REST Gemini (v1beta, generateContent). Lo costruiamo a runtime
# dal nome del modello cosi' l'utente puo' switchare modello senza toccare
# il codice (gemini-2.5-flash-lite, gemini-1.5-flash-8b, ecc.).
_GEMINI_URL_TPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Hard cap per evitare di pagare token assurdi se il prompt diventa
# enorme per qualche motivo (caption > 2k char).
_MAX_CAPTION_CHARS = 800
# Se l'API risponde piu' lentamente di questo, abbandoniamo: meglio
# usare un commento dal file txt che far aspettare il bot per minuti
# (il bot deve restare "umano" nei tempi di risposta).
_REQUEST_TIMEOUT_S = 8

# Cascata di modelli di fallback. Se il primo (configurato dall'utente)
# fallisce con un errore "transitorio" (rate-limit, 5xx, timeout, blocco
# safety), proviamo i successivi in ordine. Costo crescente, qualita'
# crescente: di default partiamo dal piu' economico.
# Aggiornato a maggio 2026: i modelli Gemini Flash 2.5 sono GA.
_DEFAULT_MODEL_CASCADE = [
    "gemini-2.5-flash-lite",   # super-economico, default
    "gemini-2.5-flash",        # piu' qualita', ~3x costo
    "gemini-1.5-flash-8b",     # legacy stabile, ottimo fallback se i 2.5 hanno disagi
    "gemini-1.5-flash",        # ultima spiaggia, stabile da anni
]

# Errori HTTP che hanno senso ritentare con un altro modello:
#   429 -> rate-limit (forse il modello specifico e' saturo, prova un altro)
#   500, 502, 503, 504 -> errori server transitori
#   408 -> timeout server-side
# 401/403/400 NON ha senso ritentare: la key e' sbagliata o il prompt e'
# vietato. Stessa cosa per 404 (modello inesistente -> proviamo il prossimo
# nella cascata).
_RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}


def _build_prompt(
    caption: str,
    target_username: Optional[str],
    media_type: str,
    hint: Optional[str],
    language: str,
) -> str:
    """Costruisce il system+user prompt.

    Le 14 regole sono volutamente assertive ('ABSOLUTELY NO ...') perche'
    Gemini Flash tende altrimenti a infilare emoji/hashtag/'I love ...'
    di default. Vedi anche src/genai.ts del repo di riferimento.
    """
    safe_caption = (caption or "").strip()
    if len(safe_caption) > _MAX_CAPTION_CHARS:
        safe_caption = safe_caption[:_MAX_CAPTION_CHARS] + "…"
    target_clause = (
        f"by @{target_username}" if target_username else "by an Instagram user"
    )
    caption_clause = (
        f'Post caption: "{safe_caption}"'
        if safe_caption
        else "The post has no caption, comment on the photo/video itself."
    )
    hint_clause = f"Context for tone: {hint}\n\n" if hint else ""

    rules = f"""Rules (follow ALL):
1. Write ONE short, relevant comment. 1 sentence ideal, max 2 short.
2. Sound 100% authentic, like a real follower, NEVER like a bot.
3. If possible, refer to something specific from the caption.
4. ABSOLUTELY NO emojis.
5. ABSOLUTELY NO hashtags.
6. ABSOLUTELY NO exclamation marks.
7. NO generic compliments ("Great post", "Love this", "So inspiring", "Amazing").
8. NO questions, do not try to start a conversation.
9. NO first-person pronouns ("I", "me", "my", "io", "mi", "mio").
10. NO opinions, NO personal experiences.
11. Vary tone and phrasing; do not reuse common patterns.
12. Media type is: {media_type}. If video/reel, comment on motion/action.
13. Output language: {language}.
14. Output ONLY the final comment text. No quotes, no prefix, no explanation."""

    return (
        f"You write engaging human-like Instagram comments.\n"
        f"Write a comment for a post {target_clause}.\n\n"
        f"{caption_clause}\n\n"
        f"{hint_clause}"
        f"{rules}\n"
    )


def _sanitize_output(text: str) -> str:
    """Rimuove rumore comune dei modelli: virgolette wrap, prefissi tipo
    'Comment:', newline interni, spazi doppi, tag HTML, e taglia a 220 char."""
    if not text:
        return ""
    t = text.strip()
    # rimuovi tag HTML (es. </blockquote>, <br>, ecc.)
    t = re.sub(r"<[^>]+>", "", t).strip()
    # rimuovi un eventuale wrap di virgolette singole/doppie/back-tick
    for q in ('"', "'", "`"):
        if len(t) >= 2 and t.startswith(q) and t.endswith(q):
            t = t[1:-1].strip()
    # togli prefissi comuni
    for prefix in ("Comment:", "comment:", "Output:", "Reply:"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
    # collassa whitespace
    t = " ".join(t.split())
    if len(t) > 220:
        t = t[:217].rstrip() + "..."
    return t


def _call_gemini(
    api_key: str,
    model: str,
    prompt: str,
) -> tuple[Optional[str], bool]:
    """Chiamata REST a Gemini.

    Returns:
        (text, retryable):
          - text: il commento o None su errore.
          - retryable: True se l'errore suggerisce di provare un altro
            modello (rate-limit, 5xx, timeout, modello-non-trovato, blocco
            safety). False se e' un errore "definitivo" che non migliora
            cambiando modello (key invalida, prompt malformato, ecc.).

    Importiamo `requests` lazy: cosi' chi non usa l'AI non paga niente,
    e in caso di import error degradiamo a None invece di crashare.
    """
    try:
        import requests  # type: ignore
    except Exception as e:  # pragma: no cover
        logger.warning(f"[ai-comment] 'requests' non disponibile: {e}")
        return None, False

    url = _GEMINI_URL_TPL.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "topP": 1.0,
            "topK": 40,
            "maxOutputTokens": 120,
        },
        # Filtri di sicurezza: lasciamo i default (BLOCK_MEDIUM_AND_ABOVE).
    }
    try:
        r = requests.post(
            url,
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=_REQUEST_TIMEOUT_S,
        )
    except Exception as e:
        # network/timeout -> sempre retryable: prova un altro modello,
        # magari sta saturo solo quel pool.
        logger.warning(f"[ai-comment] {model}: errore di rete: {e}")
        return None, True

    if r.status_code != 200:
        # NON loggo r.text intero: puo' contenere echo del prompt /
        # snippet della key se l'utente ha sbagliato setup. Solo status.
        retryable = r.status_code in _RETRYABLE_HTTP or r.status_code == 404
        # 404: probabilmente modello inesistente o non disponibile per la
        # tua region/tier -> ha senso passare al prossimo della cascata.
        logger.warning(
            f"[ai-comment] {model}: HTTP {r.status_code} "
            f"({'retryable' if retryable else 'fatal'})"
        )
        return None, retryable
    try:
        data = r.json()
    except Exception as e:
        logger.warning(f"[ai-comment] {model}: risposta non e' JSON: {e}")
        return None, True
    # Schema: candidates[0].content.parts[0].text
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            # blockReason / safety: il modello ha rifiutato. Provare un
            # altro modello PUO' funzionare (modelli diversi hanno safety
            # filter leggermente diversi); marchiamo retryable.
            block = data.get("promptFeedback", {}).get("blockReason")
            if block:
                logger.info(f"[ai-comment] {model}: prompt bloccato ({block})")
            return None, True
        # finishReason "SAFETY" / "RECITATION" / "OTHER": il singolo
        # candidate e' stato bloccato a meta' generazione. Anche qui
        # un altro modello puo' fare meglio.
        finish_reason = candidates[0].get("finishReason", "")
        parts = (
            candidates[0].get("content", {}).get("parts", [])
            if candidates[0].get("content")
            else []
        )
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        cleaned = _sanitize_output(text)
        if not cleaned:
            logger.info(
                f"[ai-comment] {model}: output vuoto (finishReason={finish_reason})"
            )
            return None, True
        return cleaned, False
    except Exception as e:
        logger.warning(f"[ai-comment] {model}: parsing fallito: {e}")
        return None, True


def is_enabled(args) -> bool:
    """True se la generazione AI e' abilitata e c'e' una API key utile.

    L'utente puo' attivare via flag CLI/YAML (`ai-comments-enabled: true`)
    OPPURE settando solo la env var GEMINI_API_KEY (utile in CI / docker).
    """
    if not getattr(args, "ai_comments_enabled", False):
        return False
    key = (
        getattr(args, "ai_comments_api_key", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_AI_API_KEY")
    )
    return bool(key and str(key).strip() and not str(key).startswith("YOUR_"))


def _build_model_chain(args) -> list[str]:
    """Costruisce la catena di modelli da provare, in ordine.

    Logica:
      1. Se l'utente ha specificato `ai-comments-models` (lista), usa quella
         AS-IS, niente cascata default.
      2. Altrimenti: parte dal `ai-comments-model` (singolo, default
         gemini-2.5-flash-lite) e appende i restanti della cascata default
         che non sono uguali al primo (no duplicati).
    Output: lista di modelli unici, ordine preservato.
    """
    # 1. lista esplicita?
    explicit = getattr(args, "ai_comments_models", None)
    if explicit:
        chain: list[str]
        if isinstance(explicit, str):
            # supporta "model1,model2,model3" come singola stringa
            chain = [m.strip() for m in explicit.split(",") if m.strip()]
        elif isinstance(explicit, (list, tuple)):
            chain = [str(m).strip() for m in explicit if str(m).strip()]
        else:
            chain = []
        if chain:
            return _dedup_keep_order(chain)

    # 2. singolo + cascata default
    primary = getattr(args, "ai_comments_model", None) or _DEFAULT_MODEL_CASCADE[0]
    primary = str(primary).strip()
    chain = [primary] + [m for m in _DEFAULT_MODEL_CASCADE if m != primary]
    return _dedup_keep_order(chain)


def _dedup_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def generate_comment(
    args,
    caption: str,
    target_username: Optional[str],
    media_type: str,
) -> Optional[str]:
    """Genera un commento AI. Ritorna None per fallback al file txt.

    Tenta i modelli della cascata in ordine: se il primo fallisce con un
    errore retryable (rate-limit, 5xx, safety block), passa al successivo.
    Se l'errore e' "fatal" (key invalida, prompt malformato, parametri
    sbagliati) si ferma subito - inutile sprecare chiamate.

    Args:
        args: namespace CLI (deve avere gli ai_* attributes - aggiunti in
            core_arguments.py). Se manca tutto, ritorna None.
        caption: caption del post. Stringa vuota OK.
        target_username: chi ha postato (per personalizzare il prompt).
            Puo' essere None.
        media_type: 'photo'|'video'|'reel'|'igtv'|'carousel' (case-free).
    """
    if not is_enabled(args):
        return None
    api_key = (
        getattr(args, "ai_comments_api_key", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_AI_API_KEY")
    )
    hint = getattr(args, "ai_comments_prompt_hint", None) or None
    language = getattr(args, "ai_comments_language", None) or "Italian"

    prompt = _build_prompt(
        caption=caption or "",
        target_username=target_username,
        media_type=str(media_type).lower(),
        hint=hint,
        language=language,
    )

    chain = _build_model_chain(args)
    api_key_clean = str(api_key).strip()
    last_error_was_fatal = False

    for idx, model in enumerate(chain):
        attempt_label = f"[{idx + 1}/{len(chain)}]"
        logger.debug(f"[ai-comment] {attempt_label} trying model {model}")
        text, retryable = _call_gemini(
            api_key=api_key_clean, model=model, prompt=prompt
        )
        if text:
            # validazione guardrail: se il modello ha sgarrato (emoji/!/#),
            # NON e' un errore "del modello", e' un output non conforme:
            # un altro modello potrebbe fare la stessa cosa, ma vale la
            # pena tentare 1 volta.
            cleaned = _validate_output(text)
            if cleaned is not None:
                if idx > 0:
                    logger.info(
                        f"[ai-comment] cascata ha funzionato: modello "
                        f"{model} ha generato dopo che {idx} modello/i "
                        f"avevano fallito."
                    )
                return cleaned
            else:
                logger.info(
                    f"[ai-comment] {model}: output non conforme alle regole, "
                    f"provo prossimo modello."
                )
                continue

        # text e' None
        if not retryable:
            last_error_was_fatal = True
            logger.warning(
                f"[ai-comment] {model}: errore fatale (key/auth/payload). "
                f"Interrompo cascata."
            )
            break
        # altrimenti loop continua sul prossimo modello

    if last_error_was_fatal:
        logger.info("[ai-comment] cascata interrotta da errore fatale.")
    else:
        logger.info(
            f"[ai-comment] tutti i {len(chain)} modelli hanno fallito; "
            f"fallback al file txt."
        )
    return None


def _validate_output(text: str) -> Optional[str]:
    """Applica i guardrail anti-emoji/hashtag/! e ritorna il testo pulito
    o None se non passa. Centralizzato per riuso nella cascata."""
    if not text:
        return None
    # Hard guardrail anti-emoji/hashtag/!: se il modello sgarra, scarto.
    if any(ch in text for ch in "#!"):
        logger.debug(
            f"[ai-comment] guardrail HIT (#/!): '{text[:60]}...'"
        )
        return None
    # heuristic emoji check senza dipendere da `emoji` lib qui:
    # qualunque codepoint > U+27BF e' quasi sempre emoji/dingbat.
    if any(ord(ch) > 0x27BF for ch in text):
        logger.debug(
            f"[ai-comment] guardrail HIT (emoji): '{text[:60]}...'"
        )
        return None
    return text

