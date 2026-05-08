"""AI-powered Direct Message generation (Gemini) for the dm-followback job.

Differenze chiave vs ``ai_comment.py``:
  * I DM SONO conversazionali: si puo' (e si DEVE) usare prima persona,
    fare 1 domanda aperta, salutare, ringraziare il follow-back.
  * Una emoji leggera occasionale (👋 🙌) e' permessa: rende piu' umano.
  * Niente hashtag, niente link, niente CTA hard-sell ("scrivimi per
    coaching") -> lascia che la conversazione nasca naturalmente.
  * Context disponibile: bio dell'utente, full name, caption ultimo post
    (tutti opzionali; il prompt si adatta a cio' che c'e').
  * Personaggio "io": personal trainer / coach a Fiorano Modenese (passato
    via ``ai-dm-prompt-hint`` dal config), che ha appena ricevuto il
    follow-back e passa a salutare.
  * Output target: 2-3 frasi brevi, italiano colloquiale.

Riusa la stessa cascata di modelli, lo stesso .env loader, gli stessi
codici HTTP retryable di ``ai_comment.py`` (importati direttamente per
evitare duplicazione).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

# Riusa loader .env, costanti e helper di ai_comment per non duplicare codice.
from GramAddict.core.ai_comment import (  # noqa: F401
    _autoload_env_local,  # idempotent re-trigger se import order cambia
    _GEMINI_URL_TPL,
    _REQUEST_TIMEOUT_S,
    _RETRYABLE_HTTP,
    _DEFAULT_MODEL_CASCADE,
    _dedup_keep_order,
)

logger = logging.getLogger(__name__)

# Hard cap per evitare di pagare token assurdi su bio enormi o caption infinite.
_MAX_BIO_CHARS = 400
_MAX_CAPTION_CHARS = 600
_MAX_FULLNAME_CHARS = 80

# Limite finale del DM (Instagram ammette molto di piu', ma >280 char in DM
# sembra spam/template; volutamente cap stretto).
_MAX_DM_CHARS = 280


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


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def is_enabled(args) -> bool:
    """True se la generazione AI dei DM e' abilitata e c'e' una API key.

    Convenzione: se ``ai-dm-enabled`` non e' settato esplicitamente, eredita
    da ``ai-comments-enabled`` (di solito chi ha attivato l'AI per i commenti
    vuole anche i DM AI). La key e' la stessa GEMINI_API_KEY: una sola.
    """
    enabled = getattr(args, "ai_dm_enabled", None)
    if enabled is None:
        # ereditarieta' soft dal flag dei commenti
        enabled = getattr(args, "ai_comments_enabled", False)
    if not enabled:
        return False
    key = (
        getattr(args, "ai_dm_api_key", None)
        or getattr(args, "ai_comments_api_key", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_AI_API_KEY")
    )
    return bool(key and str(key).strip() and not str(key).startswith("YOUR_"))


def generate_dm(
    args,
    target_username: Optional[str],
    full_name: Optional[str] = None,
    bio: Optional[str] = None,
    last_post_caption: Optional[str] = None,
) -> Optional[str]:
    """Genera un DM personalizzato. Ritorna None per fallback al pm_list.txt.

    Tutti i campi context sono opzionali: se mancano, il prompt si degrada
    elegantemente (resta un saluto generico ma sempre coerente).

    Args:
        args: namespace CLI (deve avere ai_dm_* / ai_comments_* attributes).
        target_username: il follower destinatario (per @mention nel prompt).
        full_name: nome reale visualizzato sul profilo (es. "Marco Rossi").
        bio: biografia dell'utente (max ``_MAX_BIO_CHARS``).
        last_post_caption: caption del post piu' recente, se estraibile.
    """
    if not is_enabled(args):
        return None

    api_key = (
        getattr(args, "ai_dm_api_key", None)
        or getattr(args, "ai_comments_api_key", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_AI_API_KEY")
    )
    api_key_clean = str(api_key).strip()

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

    prompt = _build_dm_prompt(
        target_username=target_username,
        full_name=full_name,
        bio=bio,
        last_post_caption=last_post_caption,
        hint=hint,
        language=language,
        allow_emoji=allow_emoji,
    )

    chain = _build_model_chain(args)
    last_error_was_fatal = False

    for idx, model in enumerate(chain):
        attempt_label = f"[{idx + 1}/{len(chain)}]"
        logger.debug(f"[ai-dm] {attempt_label} trying model {model}")
        text, retryable = _call_gemini(
            api_key=api_key_clean, model=model, prompt=prompt
        )
        if text:
            cleaned = _validate_dm_output(text, allow_emoji=allow_emoji)
            if cleaned is not None:
                if idx > 0:
                    logger.info(
                        f"[ai-dm] cascata ha funzionato: modello {model} "
                        f"ha generato dopo {idx} fallimenti."
                    )
                return cleaned
            else:
                logger.info(
                    f"[ai-dm] {model}: output non conforme alle regole, "
                    f"provo prossimo modello."
                )
                continue

        if not retryable:
            last_error_was_fatal = True
            logger.warning(
                f"[ai-dm] {model}: errore fatale (key/auth/payload). "
                f"Interrompo cascata."
            )
            break

    if last_error_was_fatal:
        logger.info("[ai-dm] cascata interrotta da errore fatale.")
    else:
        logger.info(
            f"[ai-dm] tutti i {len(chain)} modelli hanno fallito; "
            f"fallback al file pm_list.txt."
        )
    return None


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _truncate(text: Optional[str], limit: int) -> str:
    if not text:
        return ""
    t = text.strip()
    if len(t) > limit:
        return t[:limit].rstrip() + "…"
    return t


def _build_dm_prompt(
    target_username: Optional[str],
    full_name: Optional[str],
    bio: Optional[str],
    last_post_caption: Optional[str],
    hint: Optional[str],
    language: str,
    allow_emoji: bool,
) -> str:
    """Costruisce il prompt per Gemini.

    Il prompt e' assertivo perche' Flash tende altrimenti a infilare
    "Mi chiamo Marco e sono un coach...", spam di hashtag, CTA ("scrivimi
    per coaching"), o emoji a raffica. Le regole tagliano questo a monte.
    """
    name_part = _truncate(full_name, _MAX_FULLNAME_CHARS)
    bio_part = _truncate(bio, _MAX_BIO_CHARS)
    caption_part = _truncate(last_post_caption, _MAX_CAPTION_CHARS)

    # Sezione context: solo cio' che c'e'.
    context_lines = []
    if target_username:
        context_lines.append(f"- username: @{target_username}")
    if name_part:
        context_lines.append(f"- nome visualizzato: {name_part}")
    if bio_part:
        context_lines.append(f"- bio: {bio_part}")
    if caption_part:
        context_lines.append(f"- caption del suo ultimo post: {caption_part}")
    context_block = (
        "Profilo del destinatario (USA questi indizi per personalizzare,\n"
        "ma NON fare il pappagallo: non ripetere parole esatte della bio):\n"
        + "\n".join(context_lines)
        if context_lines
        else "Nessuna informazione sul profilo disponibile: scrivi un DM\n"
        "amichevole generico che ringrazia per il follow e fa una sola\n"
        "domanda aperta sul suo percorso fitness."
    )

    hint_clause = f"Contesto su CHI scrive (autore del DM): {hint}\n\n" if hint else ""

    emoji_rule = (
        "5. Puoi usare AL MASSIMO 1 emoji leggera (👋 🙌 💪) e SOLO se "
        "calza naturalmente. Nessuna emoji e' la scelta default migliore."
        if allow_emoji
        else "5. ASSOLUTAMENTE NESSUNA emoji."
    )

    rules = f"""Regole (rispettarle TUTTE):
1. Scrivi un DM Instagram di apertura, 2-3 frasi brevi totali, max 280 caratteri.
2. Tono: amichevole, peer-to-peer, mai venditore, mai formale.
3. Ringrazia in modo naturale per il follow (lui ti ha appena seguito).
4. Fai UNA SOLA domanda aperta sul suo percorso fitness/sport, basandoti sugli
   indizi del profilo se ci sono. La domanda deve essere semplice e specifica,
   non generica ("come stai?" e' VIETATO).
{emoji_rule}
6. ASSOLUTAMENTE NIENTE hashtag.
7. ASSOLUTAMENTE NIENTE link, niente "DM me", niente "scrivimi per coaching",
   niente menzioni di servizi o prezzi. Sei un coach ma in questo messaggio
   NON proponi nulla, vuoi solo conoscerlo.
8. NIENTE punti esclamativi multipli ("!!"). Massimo 1 "!" in tutto il DM.
9. NIENTE template robotici tipo "Ciao [nome], ho visto il tuo profilo e...".
   Devi sembrare un essere umano vero che scrive di getto.
10. NON menzionare la parola "bot", "automatico", "AI" o simili.
11. NON ripetere parole della bio in modo letterale: lasciale vivere come
    sottotesto (es. se la bio dice "powerlifter natural", chiedi qualcosa
    sulla sua programmazione di forza, non dire "vedo che sei powerlifter").
12. Lingua di output: {language}.
13. Output SOLO il testo finale del DM. Niente virgolette, niente prefissi,
    niente spiegazioni, niente firma."""

    return (
        f"Sei un personal trainer / coach che ha appena ricevuto un nuovo\n"
        f"follower su Instagram e vuole aprire una conversazione sincera in DM.\n\n"
        f"{hint_clause}"
        f"{context_block}\n\n"
        f"{rules}\n"
    )


def _validate_dm_output(text: str, allow_emoji: bool) -> Optional[str]:
    """Guardrail post-modello: scarta output non conformi alle regole."""
    if not text:
        return None
    cleaned = _sanitize_dm(text)
    if not cleaned:
        return None
    # Hashtag MAI permessi.
    if "#" in cleaned:
        logger.debug(f"[ai-dm] guardrail HIT (#): '{cleaned[:80]}'")
        return None
    # Link MAI permessi (anti-spam, soprattutto bit.ly/whatsapp/wa.me).
    lower = cleaned.lower()
    if any(
        marker in lower
        for marker in (
            "http://",
            "https://",
            "www.",
            "wa.me",
            "bit.ly",
            "linktr.ee",
            "t.me/",
        )
    ):
        logger.debug(f"[ai-dm] guardrail HIT (link): '{cleaned[:80]}'")
        return None
    # CTA hard-sell che dobbiamo evitare nel primo contatto.
    sell_markers = (
        "coaching online",
        "scrivimi per",
        "contattami per",
        "ti seguo io",
        "lavoro con te",
        "pacchetto",
        "prezzo",
        "consulenza",
    )
    if any(marker in lower for marker in sell_markers):
        logger.debug(f"[ai-dm] guardrail HIT (sell): '{cleaned[:80]}'")
        return None
    # Punti esclamativi: max 1.
    if cleaned.count("!") > 1:
        logger.debug(f"[ai-dm] guardrail HIT (multi-!): '{cleaned[:80]}'")
        return None
    # Emoji policy.
    emoji_count = sum(1 for ch in cleaned if ord(ch) > 0x27BF)
    if not allow_emoji and emoji_count > 0:
        logger.debug(f"[ai-dm] guardrail HIT (emoji-forbidden): '{cleaned[:80]}'")
        return None
    if allow_emoji and emoji_count > 2:
        logger.debug(f"[ai-dm] guardrail HIT (too many emoji): '{cleaned[:80]}'")
        return None
    return cleaned


def _sanitize_dm(text: str) -> str:
    """Rimuove rumore comune: virgolette wrap, prefissi tipo 'DM:', spazi
    doppi. Mantiene newlines (un DM puo' avere 1-2 righe)."""
    t = text.strip()
    for q in ('"', "'", "`"):
        if len(t) >= 2 and t.startswith(q) and t.endswith(q):
            t = t[1:-1].strip()
    for prefix in ("DM:", "Messaggio:", "Risposta:", "Output:", "Reply:"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
    # collassa spazi multipli ma preserva newline singoli.
    lines = [" ".join(line.split()) for line in t.splitlines()]
    t = "\n".join(line for line in lines if line)
    if len(t) > _MAX_DM_CHARS:
        t = t[: _MAX_DM_CHARS - 1].rstrip() + "…"
    return t


def _build_model_chain(args) -> list[str]:
    """Catena modelli per i DM.

    Logica analoga a ai_comment._build_model_chain ma con override
    indipendenti (``ai-dm-model`` / ``ai-dm-models``). Se non specificati,
    cade su quelli dei commenti, e infine sul default cascade.
    """
    explicit = getattr(args, "ai_dm_models", None) or getattr(
        args, "ai_comments_models", None
    )
    if explicit:
        if isinstance(explicit, str):
            chain = [m.strip() for m in explicit.split(",") if m.strip()]
        elif isinstance(explicit, (list, tuple)):
            chain = [str(m).strip() for m in explicit if str(m).strip()]
        else:
            chain = []
        if chain:
            return _dedup_keep_order(chain)

    primary = (
        getattr(args, "ai_dm_model", None)
        or getattr(args, "ai_comments_model", None)
        or _DEFAULT_MODEL_CASCADE[0]
    )
    primary = str(primary).strip()
    chain = [primary] + [m for m in _DEFAULT_MODEL_CASCADE if m != primary]
    return _dedup_keep_order(chain)


def _call_gemini(
    api_key: str,
    model: str,
    prompt: str,
) -> tuple[Optional[str], bool]:
    """Chiamata REST a Gemini per la generazione del DM.

    Stesso schema di ``ai_comment._call_gemini`` ma con maxOutputTokens piu'
    alto (un DM e' 2-3 frasi vs 1 del commento) e temperatura leggermente
    piu' bassa (vogliamo meno variazione lessicale, piu' coerenza).

    Returns: (text, retryable). Vedi ai_comment._call_gemini per semantica.
    """
    try:
        import requests  # type: ignore
    except Exception as e:  # pragma: no cover
        logger.warning(f"[ai-dm] 'requests' non disponibile: {e}")
        return None, False

    url = _GEMINI_URL_TPL.format(model=model)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.85,
            "topP": 0.95,
            "topK": 40,
            "maxOutputTokens": 200,
        },
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
        logger.warning(f"[ai-dm] {model}: errore di rete: {e}")
        return None, True

    if r.status_code != 200:
        retryable = r.status_code in _RETRYABLE_HTTP or r.status_code == 404
        logger.warning(
            f"[ai-dm] {model}: HTTP {r.status_code} "
            f"({'retryable' if retryable else 'fatal'})"
        )
        return None, retryable
    try:
        data = r.json()
    except Exception as e:
        logger.warning(f"[ai-dm] {model}: risposta non e' JSON: {e}")
        return None, True
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            block = data.get("promptFeedback", {}).get("blockReason")
            if block:
                logger.info(f"[ai-dm] {model}: prompt bloccato ({block})")
            return None, True
        finish_reason = candidates[0].get("finishReason", "")
        parts = (
            candidates[0].get("content", {}).get("parts", [])
            if candidates[0].get("content")
            else []
        )
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text.strip():
            logger.info(
                f"[ai-dm] {model}: output vuoto (finishReason={finish_reason})"
            )
            return None, True
        return text, False
    except Exception as e:
        logger.warning(f"[ai-dm] {model}: parsing fallito: {e}")
        return None, True



