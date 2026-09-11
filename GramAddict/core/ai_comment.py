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
      salta il commento (o cade su `comments_list.txt` solo se
      ai-comments-fallback-to-file e' attivo: per questo account e' OFF, un
      commento fisso non conosce il post).
    - Circuit breaker: se rileviamo che la rete e' down (DNS/refused/no
      route) apriamo il breaker per 10 minuti per non sprecare tempo.

Compatibilita' storica: i vecchi flag Gemini (--ai-comments-api-key,
--ai-comments-model, --ai-comments-models, GEMINI_API_KEY env) restano
riconosciuti da argparse ma vengono ignorati - vedi core_arguments.py.
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

_REQUEST_TIMEOUT_S = 25  # lo Space risponde in <1s quando va bene; il caso lento e' un
                         # cold start del container HF o Groq che tarda. Il bot ha il box
                         # commenti aperto e puo' aspettare qualche secondo in piu'.

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
        f"{_BREAKER_COOLDOWN_S}s (il commento viene saltato, o preso dal txt se "
        f"ai-comments-fallback-to-file e' attivo)."
    )


def _looks_like_network_down(err_repr: str) -> bool:
    low = err_repr.lower()
    return any(marker in low for marker in _NETWORK_DOWN_MARKERS)


# ---------------------------------------------------------------------------
# Qualita' dell'input e dell'output.
#
# Il modello NON vede la foto: riceve solo la caption. Sui 1971 commenti AI dei
# log storici il 27% era stato generato con caption VUOTA e un altro 19% con
# caption fatta solo di emoji: li' il modello puo' solo inventare ("la simmetria
# del petto e' notevole" sotto a "☃️☃️"). Il 36% infilava gergo da bodybuilding
# su post che non c'entravano nulla, il 7% usciva mozzato ("Bello il"), l'1.6%
# si rivolgeva a "Mattia" (chi scrive, non chi legge). Queste funzioni chiudono
# i casi che si possono chiudere senza LLM: input non utilizzabile -> non si
# chiama l'AI; output difettoso -> si scarta.
# ---------------------------------------------------------------------------
import re

# "… more" / "... more" che IG appende alle caption troncate
_MORE_SUFFIX_RE = re.compile(r"(\s*(…|\.{3})\s*more|\s+more)\s*$", re.IGNORECASE)
# token che ha la forma di un handle IG (minuscolo, lettere/cifre/punto/underscore)
_HANDLE_TOKEN_RE = re.compile(r"^@?[a-z0-9._]{3,30}$")
# parole di almeno 3 lettere, in qualunque alfabeto (emoji, #, cifre esclusi)
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
# se un commento finisce con una di queste (senza punteggiatura) e' mozzato
_FRAGMENT_TAIL = {
    "il", "lo", "la", "i", "gli", "le", "un", "una", "uno", "di", "da", "in",
    "con", "su", "per", "tra", "fra", "a", "e", "o", "ma", "che", "del",
    "della", "dei", "delle", "degli", "al", "alla", "ai", "alle", "nel",
    "nella", "nei", "sul", "sulla", "sui", "quel", "quella", "quei", "questo",
    "questa", "questi", "molto", "sempre", "non", "anche", "come", "quando",
    "se", "mai", "ogni", "tutto", "the", "an", "of", "to", "on", "for", "and",
    "with", "very", "so", "not",
}
_COMMENT_MIN_WORDS = 3
_COMMENT_TERMINATORS = ".?!\"”)…"
_COMMENT_MAX_WORDS = 22
# hashtag che non dicono nulla del post: da soli non bastano a giustificare
# un commento (le caption di soli hashtag davano i commenti piu' generici)
_GENERIC_TAGS = {
    "gym", "fit", "fitness", "love", "photo", "picoftheday", "instagood",
    "instamood", "instamoment", "instadaily", "like", "likes", "follow",
    "followme", "like4like", "photooftheday", "me", "my", "life", "happy",
    "motivation", "workout", "bodybuilding", "summer", "vibes", "mood",
}
# temi su cui NON conviene proprio commentare (rischio reputazionale)
_ADULT_MARKERS = re.compile(
    r"(#?\b(escort|onlyfans|ollyfans|uominibelli|uominialtissimi|sugar\s?daddy)\b"
    r"|disponibil[ei]\W{0,3}h\s?24|\bh24\b)",
    re.IGNORECASE,
)
# post pubblicitari (vendita di percorsi, codici sconto, call-to-action in DM):
# commentarli significa fare pubblicita' a qualcun altro, sul profilo di un
# coach a un concorrente. Il bot li ha commentati ("Sfrutta il codice ROB15").
_AD_MARKERS = re.compile(
    r"(\b(scrivimi|scrivetemi|contattami|prenota|iscriviti|iscrizioni aperte|"
    r"link in bio|codice sconto|coupon|promo|shop|acquista|ordina ora|"
    r"posti disponibili|ultimi \d+ posti|swipe up)\b"
    r"|\bcodice\s+[A-Z0-9]{3,}\b|\bsconto\b|\bin dm\b|\bdm\b\s*(📩|per|e ti)|\bwhatsapp\b)",
    re.IGNORECASE,
)
_LATIN_LETTER_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]")
_ANY_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
# solo i casi AUTOREFERENZIALI: 'ti abbraccio e penso a te' su un post di malattia
# e' il commento giusto, non va scartato
_FIRST_PERSON_RE = re.compile(
    r"\b(io|anche io|io pure|per me|secondo me|mi piace|adoro|amo|il mio|la mia|i miei)\b",
    re.IGNORECASE,
)
_DEICTIC_OPENER_RE = re.compile(r"^\s*(quel|quella|quei|quelle|quegli)\b", re.IGNORECASE)
# il modello non vede nulla: questi verbi annunciano un dettaglio inventato
_VISUAL_INFERENCE_RE = re.compile(
    r"\b(sembra|sembrano|si vede|si vedono|si nota|si notano|si sente|si legge|traspare|"
    r"in questa foto|in questo video|nella foto|nel video)\b",
    re.IGNORECASE,
)
# riempitivo n.1 nei log (10 volte su 51 nel banco di prova): il modello ignora il
# divieto nel prompt, quindi lo togliamo noi. Toglierlo non rompe la frase.
_FILLER_RE = re.compile(r"\s+davvero\b", re.IGNORECASE)

# Ogni chiamata al modello e' indipendente: nessun prompt puo' variare attacco e
# chiusura TRA un commento e l'altro, e nel banco di prova "continua cosi'" e'
# uscito 10 volte su 34. La varieta' la creiamo qui, appendendo all'hint una
# micro-istruzione diversa a ogni chiamata.
_VARIATIONS = (
    "",  # nessuna indicazione extra: lascia respirare il modello
    "",
    "Per questo commento: inizia con un verbo all'indicativo (non un imperativo, non un consiglio), nessun augurio in chiusura.",
    "Per questo commento: una sola frase secca di 6-9 parole, tono asciutto, nessun augurio.",
    "Per questo commento: chiudi con un augurio breve e specifico al tema della caption (mai 'continua cosi'', mai 'forza', mai 'complimenti').",
    "Per questo commento: tono leggero, una battuta bonaria sul dettaglio della caption, senza punti esclamativi e senza consigli.",
    "Per questo commento: inizia con un aggettivo o con un numero presente nella caption; chiudi senza augurio e senza 'quel/quella'.",
)

_last_rejection: Optional[str] = None


def last_rejection_reason() -> Optional[str]:
    """Motivo per cui l'ULTIMO output AI e' stato scartato dal validatore (None se
    l'ultima chiamata non e' stata scartata). Il chiamante lo usa per SALTARE il
    commento invece di ripiegare sul file fitness generico: se avevamo una
    caption e il modello ha prodotto spazzatura, il file non e' meglio."""
    return _last_rejection


def build_hint_for_call(base_hint: Optional[str]) -> Optional[str]:
    """Hint di configurazione + una micro-istruzione di variazione scelta a caso."""
    import random

    variation = random.choice(_VARIATIONS)
    return f"{base_hint.strip()} {variation}" if base_hint else variation


def polish_comment(comment: str) -> str:
    """Ritocchi che non cambiano il senso: via il riempitivo 'davvero', spazi
    doppi, spazio prima della punteggiatura."""
    text = _FILLER_RE.sub("", comment or "")
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return " ".join(text.split()).strip()


def clean_caption(caption: Optional[str], target_username: Optional[str] = None) -> str:
    """Toglie il rumore che IG appiccica alla caption prima di darla al modello.

    - collassa gli spazi;
    - toglie il "… more" delle caption troncate (18.7% dei casi nei log): il
      modello non deve leggere "more" come parte del testo;
    - toglie l'handle del poster incollato davanti. Il chiamante gia' toglie
      ``target_username``, ma nei post in collaborazione/repost il primo autore
      e' un altro ("gastronomiadaluciano_udine Carote, pomodoro..."). Tolgo il
      primo token solo se ha la forma di un handle E contiene . _ o cifre: una
      parola italiana non li ha mai, quindi zero falsi positivi.
    """
    text = " ".join((caption or "").split())
    text = _MORE_SUFFIX_RE.sub("", text).strip()
    parts = text.split(" ", 1)
    if len(parts) == 2:
        head, rest = parts
        head_clean = head.lstrip("@").lower()
        is_target = bool(target_username) and head_clean == target_username.lower()
        looks_handle = bool(_HANDLE_TOKEN_RE.match(head)) and bool(
            re.search(r"[._0-9]", head)
        )
        if is_target or looks_handle:
            text = rest.strip()
    return text


def caption_skip_reason(caption: Optional[str], min_words: int = 3) -> Optional[str]:
    """Perche' NON chiamare l'AI su questa caption (None = si puo' chiamare).

    - poche parole vere (emoji, cifre, cuoricini non contano): qualunque
      commento "specifico" sarebbe inventato di sana pianta;
    - caption di soli hashtag generici (#gym #love #photo): il modello puo' solo
      cucire complimenti da bot;
    - alfabeto prevalentemente non latino (cirillico, arabo): il modello ne
      indovina il senso e in italiano esce una frase a caso;
    - temi adult/escort: meglio il solo like;
    - post pubblicitari (percorsi in vendita, codici sconto, "scrivimi in DM"):
      commentarli e' fare pubblicita' a qualcun altro.
    """
    text = caption or ""
    if _ADULT_MARKERS.search(text):
        return "tema adult/escort"
    if _AD_MARKERS.search(text):
        return "post pubblicitario (vendita/sconto/DM)"
    letters = _ANY_LETTER_RE.findall(text)
    if letters and len(_LATIN_LETTER_RE.findall(text)) < len(letters) * 0.6:
        return "alfabeto non latino"
    words = _WORD_RE.findall(text)
    if len(words) < max(0, int(min_words)):
        return f"meno di {min_words} parole vere"
    plain = [w for w in text.split() if not w.startswith(("#", "@"))]
    if "#" in text and not any(_WORD_RE.search(w) for w in plain):
        # solo hashtag: servono almeno 3 tag "parlanti" (>=5 lettere, non generici)
        tags = [
            w for w in re.findall(r"#([^\W\d_]{5,})", text, re.UNICODE)
            if w.lower() not in _GENERIC_TAGS
        ]
        if len(tags) < 3:
            return "solo hashtag generici"
    return None


def caption_is_usable(caption: Optional[str], min_words: int = 3) -> bool:
    """True se la caption da' al modello qualcosa di concreto su cui scrivere."""
    return caption_skip_reason(caption, min_words) is None


_EN_STOPWORDS = {"the", "is", "are", "and", "with", "for", "you", "your", "this", "that", "in", "of", "to", "it", "was", "on"}
_IT_STOPWORDS = {"il", "la", "che", "con", "per", "una", "un", "del", "della", "non", "sempre", "anche", "quel", "quella", "ti", "tuo", "tua"}


_ALPHA_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ']+")


def _looks_english(text: str) -> bool:
    """True se il testo e' chiaramente inglese (i modelli di riserva ogni tanto
    rispondono in inglese anche con language=Italian). Tokenizza anche le parole
    corte ("is", "in", "of"): sono proprio quelle che fanno la differenza."""
    words = [w.lower() for w in _ALPHA_TOKEN_RE.findall(text or "")]
    en = sum(w in _EN_STOPWORDS for w in words)
    it = sum(w in _IT_STOPWORDS for w in words)
    return en >= 2 and en > it


def _shares_long_run(comment: str, caption: str, run: int = 4) -> bool:
    """True se commento e caption condividono ``run`` parole consecutive: e' il
    modello che ricopia la caption invece di reagirci ("zero rimpianti")."""
    def norm(s):
        return [w.lower() for w in _WORD_RE.findall(s or "")]

    a, b = norm(comment), norm(caption)
    if len(a) < run or len(b) < run:
        return False
    grams = {tuple(b[i : i + run]) for i in range(len(b) - run + 1)}
    return any(tuple(a[i : i + run]) in grams for i in range(len(a) - run + 1))


def comment_is_acceptable(
    comment: Optional[str],
    author_name: Optional[str] = None,
    min_words: int = _COMMENT_MIN_WORDS,
    caption: Optional[str] = None,
    language: str = "Italian",
) -> tuple:
    """Controlli sull'output che il guardrail dello Space non fa.

    Ritorna ``(ok, motivo)``. Scarta:
      - frammenti: meno di ``min_words`` parole, oppure nessuna punteggiatura
        finale E ultima parola che e' un articolo/preposizione ("Bello il",
        "Ottima intensita' per"). Nei log erano il 7%: e' il ragionamento
        interno dei modelli reasoning che si mangia i token di output;
      - commenti che nominano chi scrive ("continua cosi' Mattia"): il modello
        confonde autore e destinatario.
    """
    text = (comment or "").strip()
    words = text.split()
    if len(words) < min_words:
        return False, f"troppo corto ({len(words)} parole)"
    last = words[-1].strip(",;:").lower()
    if text[-1] not in _COMMENT_TERMINATORS and last in _FRAGMENT_TAIL:
        return False, f"frammento (finisce con '{words[-1]}')"
    if author_name:
        if re.search(rf"\b{re.escape(author_name)}\b", text, re.IGNORECASE):
            return False, f"si rivolge a chi scrive ('{author_name}')"
    # dal panel di giudizio sui 51 casi del banco di prova (tools/ai_comment_bench.py)
    if len(words) > _COMMENT_MAX_WORDS:
        return False, f"troppo lungo ({len(words)} parole)"
    if "@" in text:
        return False, "contiene un @handle"
    if "?" in text:
        return False, "contiene una domanda"
    # il controllo lingua vale solo se la lingua richiesta e' l'italiano: con
    # ai-comments-language: auto (account che commentano anche sotto post
    # stranieri) un commento in inglese e' quello giusto
    if (language or "Italian").strip().lower() == "italian" and _looks_english(text):
        return False, "lingua sbagliata (inglese)"
    if _DEICTIC_OPENER_RE.match(text):
        return False, "attacco 'Quel/Quella' (tic da bot)"
    if _FIRST_PERSON_RE.search(text):
        return False, "prima persona"
    if _VISUAL_INFERENCE_RE.search(text):
        return False, "inferenza visiva ('sembra', 'si vede'...)"
    if caption and _shares_long_run(text, caption):
        return False, "ricopia la caption (4+ parole consecutive)"
    return True, ""


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
    global _last_model_used
    _last_model_used = model_used

    if comment:
        logger.info(
            f"[ai-comment] generated via {model_used} in {latency_ms}ms "
            f"(attempts={data.get('attempts')})"
        )
        return str(comment).strip()

    if err:
        logger.info(f"[ai-comment] Space returned no comment: {err}")
    return None


_last_model_used = None


def last_model_used():
    """Modello che ha risposto all'ultima chiamata allo Space (per log/bench)."""
    return _last_model_used


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
    global _last_rejection
    _last_rejection = None
    hint = build_hint_for_call(getattr(args, "ai_comments_prompt_hint", None) or None)
    language = getattr(args, "ai_comments_language", None) or "Italian"

    caption = clean_caption(caption, target_username)
    payload = {
        "caption": caption,
        "media_type": str(media_type or "photo").lower(),
        "target_username": target_username,
        "hint": hint,
        "language": language,
    }

    comment = _call_space(space_url, space_key, payload)
    if comment is None:
        return None
    comment = polish_comment(comment)
    # Controlli che il guardrail dello Space non fa: frammenti, prima persona,
    # inferenze visive, copia della caption, commenti rivolti a chi scrive.
    # Scartare = il chiamante SALTA il commento (vedi last_rejection_reason).
    author_name = getattr(args, "ai_comments_author_name", None) or None
    ok, why = comment_is_acceptable(
        comment, author_name, caption=caption, language=language
    )
    if not ok:
        _last_rejection = why
        logger.warning(f"[ai-comment] output scartato ({why}): {comment!r}")
        return None
    return comment


def min_caption_words(args) -> int:
    """Soglia di parole vere sotto cui non si chiama l'AI (default 3)."""
    raw = getattr(args, "ai_comments_min_caption_words", None)
    try:
        return max(0, int(str(raw).strip())) if raw not in (None, "") else 3
    except (TypeError, ValueError):
        return 3
