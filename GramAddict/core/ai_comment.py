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

Context-awareness (vedi punto #6 di IDEAS):
    - Riconoscimento tipo post dalla caption (workout / food / progress /
      motivational / generic) -> inietta istruzioni di tono specifiche
      nel prompt cosi' Gemini scrive con stile coerente al contenuto.
    - Anti-ripetizione: ultimi N commenti generati salvati in
      ``accounts/<user>/ai_comments_history.json`` (rolling). Se il
      nuovo commento ha Jaccard >= 0.55 su trigram-set rispetto a uno
      qualunque degli storici -> lo scartiamo e proviamo il prossimo
      modello della cascata. Evita lo shadowban da "spam comment".
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

# ---------------------------------------------------------------------------
# Circuit breaker: se la rete e' DOWN (DNS bloccato, connessione assente,
# firewall che droppa) NON ha senso tentare i 4 modelli in cascata ad ogni
# commento -- ogni cascata costa 4 * _REQUEST_TIMEOUT_S = ~32s sprecati
# (osservato in produzione: rete con DNS aziendale che blocca
# generativelanguage.googleapis.com -> NXDOMAIN -> 4 fallimenti immediati,
# ma anche con timeout veri sono 32+ secondi a vuoto).
#
# Soluzione: appena rileviamo un network error "strutturale" (DNS / unable
# to resolve / connection refused), apriamo il breaker e per i prossimi
# _BREAKER_COOLDOWN_S secondi rispondiamo subito None senza nemmeno tentare
# la prima chiamata. Dopo il cooldown ritentiamo (la rete potrebbe essere
# tornata: cambio Wi-Fi, VPN, ecc.).
# ---------------------------------------------------------------------------
_BREAKER_COOLDOWN_S = 600  # 10 minuti
# Marker errori di rete strutturali (case-insensitive sul repr della
# exception). Se compaiono, apriamo il breaker. Altri errori (timeout
# read/write puri) NON aprono il breaker: potrebbe essere solo
# l'endpoint specifico lento.
_NETWORK_DOWN_MARKERS = (
    "nodename nor servname",        # DNS lookup failed (macOS)
    "name or service not known",    # DNS lookup failed (Linux)
    "temporary failure in name resolution",
    "no address associated with hostname",
    "newconnectionerror",           # urllib3 wrapper su errori sock di basso livello
    "failed to establish a new connection",
    "network is unreachable",
    "no route to host",
    "connection refused",
)

_breaker_opened_at: Optional[float] = None  # epoch seconds, None = chiuso


def _breaker_is_open() -> bool:
    """True se il circuit breaker e' aperto (network ritenuto down)."""
    global _breaker_opened_at
    if _breaker_opened_at is None:
        return False
    import time as _t
    elapsed = _t.time() - _breaker_opened_at
    if elapsed >= _BREAKER_COOLDOWN_S:
        # cooldown scaduto: chiudiamo e diamo un'altra chance.
        _breaker_opened_at = None
        logger.info(
            f"[ai-comment] circuit breaker: cooldown di {_BREAKER_COOLDOWN_S}s "
            f"scaduto, ritento la rete."
        )
        return False
    return True


def _breaker_open(reason: str) -> None:
    """Apre il breaker: prossime chiamate skip-pano subito al fallback txt."""
    global _breaker_opened_at
    import time as _t
    _breaker_opened_at = _t.time()
    logger.warning(
        f"[ai-comment] circuit breaker APERTO ({reason}). "
        f"Le prossime chiamate AI verranno skip-pate per "
        f"{_BREAKER_COOLDOWN_S}s (fallback diretto a comments_list.txt)."
    )


def _looks_like_network_down(err_repr: str) -> bool:
    """True se l'errore ha l'odore di una rete down strutturale
    (DNS, no route, refused) piuttosto che di un timeout/lentezza."""
    low = err_repr.lower()
    return any(marker in low for marker in _NETWORK_DOWN_MARKERS)


# ============================================================================
# Context-awareness #1: detection del tipo post dalla caption
# ============================================================================
#
# Approccio keyword-based su entrambe le lingue IT/EN. NIENTE NLP o
# embeddings: vogliamo zero dipendenze pesanti e zero latency. Se la
# caption matcha keyword di una categoria con score >= 2, etichettiamo
# il post di quella categoria. In caso di tie o nessun match, fallback
# a "generic".
#
# La categoria viene poi tradotta in una "tone instruction" che si
# appende alle regole del prompt: cosi' Gemini scrive un commento
# coerente al contenuto (es. su workout fai un commento da palestra,
# su food un commento da appassionato di cucina, ecc.).

_POST_TYPE_KEYWORDS = {
    "workout": [
        # IT
        "allenament", "palestra", "workout", "pesi", "bilanciere", "manubri",
        "squat", "panca", "stacco", "deadlift", "bench", "press", "curl",
        "trazion", "pull up", "pullup", "gambe", "petto", "schiena",
        "bicipit", "tricipit", "addomi", "spall", "leg day", "chest day",
        "back day", "cardio", "ripetizion", "set ", "serie", "circuito",
        "hiit", "training", "session", "rep", "reps", "rm", "1rm",
        "tempo sotto tensione", "tut", "lift", "lifting", "crossfit",
        "wod", "amrap", "emom", "stretching", "mobility",
        # EN
        "lift", "barbell", "dumbbell", "push up", "pushup", "pull-up",
        "leg ", "chest ", "back ", "biceps", "triceps", "abs ", "shoulder",
        "reps", "set ", "PR", "personal record",
    ],
    "food": [
        # IT
        "pasto", "pranzo", "cena", "colazione", "spuntino", "ricetta",
        "cuoc", "cottur", "ingredient", "proteic", "proteine", "carb",
        "carboidrat", "grassi", "kcal", "calorie", "macros", "macro",
        "dieta", "alimentazione", "nutrizion", "pollo", "riso", "avena",
        "uova", "salmone", "tonno", "verdur", "frutta", "pizza", "pasta",
        "insalata", "frullato", "shake", "preparazione",
        # EN
        "meal", "breakfast", "lunch", "dinner", "snack", "recipe",
        "protein", "carbs", "fat", "calorie", "calories", "macro", "diet",
        "chicken", "rice", "oats", "eggs", "salmon", "tuna", "salad",
        "smoothie",
    ],
    "progress": [
        # IT
        "progress", "trasformazione", "transformation", "prima/dopo",
        "before/after", "before and after", "risultat", "obiettivo",
        "obbiettivo", "perso ", "messo su", "muscolo", "definizione",
        "shred", "bulk", "cut", "ricomposizione", "ricomp", "peso",
        "bilancia", "specchio", "selfie progresso", "body check",
        "settiman", "mesi di", "anno di", "mesi fa", "giorni fa",
        "punto di partenza", "trasformare", "miglioramento",
        # EN
        "before/after", "before and after", "transformation", "progress",
        "weight loss", "weight gain", "fat loss", "muscle gain",
        "body check", "body update",
    ],
    "motivational": [
        # IT
        "motivazion", "non mollare", "ce la puoi fare", "ce la fai",
        "credi in te", "mindset", "mentalita", "disciplina", "costanza",
        "abitudini", "sacrificio", "determinazione", "non e' facile",
        "vai avanti", "nessuno regala", "lavora duro", "non arrender",
        "tutto e' possibile", "obiettivi", "sogni", "vittoria",
        "fallimento", "rialzati", "crescita personale", "diario",
        # EN
        "mindset", "motivation", "never give up", "stay strong",
        "discipline", "consistency", "habits", "no excuses",
        "hard work", "no pain no gain",
    ],
}


def _detect_post_type(caption: str) -> str:
    """Classifica la caption in una di: workout|food|progress|motivational|generic.

    Match keyword-based, case-insensitive, su parole/sequenze. Score: 1 punto
    per ogni keyword distinta che appare nella caption. La categoria con
    score piu' alto vince (min score=2 per attivare, altrimenti generic).

    Volutamente conservativo: in caso di ambiguita' (tie o tutto <2)
    cade su "generic" cosi' il prompt resta neutro e Gemini decide da
    solo. Tester piu' avanti se serve regolazione fine.
    """
    if not caption:
        return "generic"
    text = caption.lower()
    best_cat = "generic"
    best_score = 1  # serve almeno 2 per battere il default
    for cat, kws in _POST_TYPE_KEYWORDS.items():
        score = 0
        for kw in kws:
            # match come substring (semplice e veloce). Le keyword sono
            # gia' state scelte radicate (es. "allenament" matcha
            # allenamento/allenamenti/allenamenti, "workout" matcha
            # workouts).
            if kw in text:
                score += 1
        if score > best_score:
            best_score = score
            best_cat = cat
    return best_cat


def _tone_instruction(post_type: str) -> str:
    """Ritorna l'istruzione di tono extra da iniettare nel prompt.

    Le istruzioni sono brevi e in inglese (lingua del prompt). Il
    'language' rimane quello dell'utente (Italian) -> il commento finale
    sara' comunque in italiano, ma con tono adatto al contenuto.
    """
    return {
        "workout": (
            "Detected post type: workout/training. Comment as a fellow "
            "gym-goer who recognizes the exercise/effort. Reference the "
            "specific movement/intensity if mentioned. Tone: peer-to-peer, "
            "respectful of the work shown."
        ),
        "food": (
            "Detected post type: food/nutrition. Comment from the angle of "
            "someone who appreciates clean/practical cooking. Reference an "
            "ingredient or the prep style if visible. Tone: curious, "
            "appreciative, never preachy."
        ),
        "progress": (
            "Detected post type: progress/transformation. Comment "
            "acknowledging the visible result and consistency required. "
            "Avoid hyperbole ('amazing', 'incredible'). Tone: genuine, "
            "concise."
        ),
        "motivational": (
            "Detected post type: motivational/mindset. Comment validating "
            "the principle without restating it. Bring a small concrete "
            "angle (consistency, daily habit). Tone: grounded, "
            "non-cliche."
        ),
        "generic": "",
    }.get(post_type, "")


# ============================================================================
# Context-awareness #2: anti-ripetizione persistente
# ============================================================================
#
# Salviamo gli ultimi N commenti generati con successo in:
#   accounts/<username>/ai_comments_history.json
# Schema rolling-list, FIFO. Quando si va a generare un nuovo commento,
# se la similarita' Jaccard sui trigram-set di parole supera la soglia,
# scartiamo il candidate e proviamo un altro modello (la cascata gia'
# esistente in generate_comment fa il loop).
#
# Perche' Jaccard su trigram: e' un buon proxy di "frase simile" senza
# pesare dipendenze NLP. Soglia 0.55 e' empirica: testando su frasi
# come "Bella determinazione, si vede l'impegno" vs "Bella
# determinazione, si capisce l'impegno" -> Jaccard ~0.6 -> scartato.
# Frasi diverse tipo "Squat profondi, ottima tecnica" vs "Avena e uova,
# colazione solida" -> Jaccard ~0.0 -> tenuti.

_HISTORY_FILENAME = "ai_comments_history.json"
_HISTORY_MAX_ENTRIES = 50  # rolling: tieni ultimi 50 commenti
# Soglia Jaccard sui BIGRAMMI di parole. I commenti IG sono corti (4-8
# parole tipiche), quindi i trigram danno set troppo piccoli e Jaccard
# crolla velocemente. Bigrammi + soglia 0.33 calibrata su test reali:
#   "Bella determinazione si vede l'impegno" vs
#   "Bella determinazione si capisce l'impegno"  -> 0.33 -> dup OK
#   "Squat profondi ottima tecnica" vs "Squat profondi ottima esecuzione"
#   -> 0.50 -> dup OK
#   Frasi non correlate -> < 0.10 -> non dup OK
# 0.33 significa che almeno 1/3 dei bigrammi e' identico: su frasi corte
# tipiche degli AI comment, 1/3 di overlap e' gia' un pattern visibile.
_HISTORY_SIM_THRESHOLD = 0.33


def _normalize_text(text: str) -> str:
    """Lowercase, rimuove punteggiatura, collassa spazi. Stesso pre-process
    sia per la storia che per il candidate -> confronto consistente."""
    if not text:
        return ""
    t = text.lower()
    # togli punteggiatura comune ma mantieni le lettere accentate
    t = re.sub(r"[.,;:!?\"'`\-—–_/\\()\[\]{}]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _trigrams(words: list[str]) -> set[tuple]:
    """Set di N-grammi di parole. Per i commenti IG (4-8 parole tipiche),
    trigrammi danno set troppo sparsi -> usiamo BIGRAMMI di default. Per
    frasi <2 parole, fallback a unigram.

    Nome mantenuto _trigrams per backward-compat con eventuali test
    esistenti; in realta' adesso ritorna bigrammi. Vedi
    _HISTORY_SIM_THRESHOLD per la motivazione.
    """
    if not words:
        return set()
    if len(words) >= 2:
        return {tuple(words[i : i + 2]) for i in range(len(words) - 1)}
    return {(words[0],)}


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _account_path_from_args(args) -> Optional[str]:
    """Ricava il path della cartella dell'account dal namespace args.

    Layout standard del repo: accounts/<username>/. Se non riusciamo a
    determinarlo, ritorniamo None e il caller skippera' silenziosamente
    storia/persistenza (degrade graceful).
    """
    username = getattr(args, "username", None)
    if not username:
        return None
    candidate = os.path.join("accounts", str(username))
    return candidate if os.path.isdir(candidate) else None


def _load_history(account_path: str) -> list[str]:
    """Carica la lista di commenti storici. Ritorna lista vuota su errore."""
    p = os.path.join(account_path, _HISTORY_FILENAME)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # filtro: solo stringhe
            return [str(x) for x in data if isinstance(x, str) and x.strip()]
        return []
    except (OSError, json.JSONDecodeError) as e:
        logger.debug(f"[ai-comment] cannot read history: {e}")
        return []


def _save_history(account_path: str, history: list[str]) -> None:
    """Scrive la lista di commenti storici (max N entries, FIFO)."""
    if len(history) > _HISTORY_MAX_ENTRIES:
        history = history[-_HISTORY_MAX_ENTRIES:]
    p = os.path.join(account_path, _HISTORY_FILENAME)
    try:
        os.makedirs(account_path, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.debug(f"[ai-comment] cannot write history: {e}")


def _is_duplicate(candidate: str, history: list[str]) -> tuple[bool, float]:
    """Controlla se il candidate e' troppo simile a uno qualunque storico.

    Ritorna (is_dup, max_jaccard_seen). Max jaccard utile per logging.
    """
    if not candidate or not history:
        return False, 0.0
    cand_words = _normalize_text(candidate).split()
    cand_set = _trigrams(cand_words)
    if not cand_set:
        return False, 0.0
    max_sim = 0.0
    for old in history:
        old_set = _trigrams(_normalize_text(old).split())
        sim = _jaccard(cand_set, old_set)
        if sim > max_sim:
            max_sim = sim
        if sim >= _HISTORY_SIM_THRESHOLD:
            return True, sim
    return False, max_sim


def _build_prompt(
    caption: str,
    target_username: Optional[str],
    media_type: str,
    hint: Optional[str],
    language: str,
    post_type: Optional[str] = None,
) -> str:
    """Costruisce il system+user prompt.

    Le 14 regole sono volutamente assertive ('ABSOLUTELY NO ...') perche'
    Gemini Flash tende altrimenti a infilare emoji/hashtag/'I love ...'
    di default. Vedi anche src/genai.ts del repo di riferimento.

    Se ``post_type`` e' valorizzato (workout/food/progress/motivational),
    appendiamo una "tone instruction" che orienta Gemini verso lo stile
    appropriato al contenuto (vedi _tone_instruction).
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
    tone_extra = _tone_instruction(post_type) if post_type else ""
    tone_clause = f"{tone_extra}\n\n" if tone_extra else ""

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
        f"{tone_clause}"
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
        err_repr = repr(e)
        logger.warning(f"[ai-comment] {model}: errore di rete: {e}")
        # Se l'errore puzza di "rete down strutturale" (DNS bloccato,
        # connessione rifiutata, no route), apri il circuit breaker:
        # tentare gli altri 3 modelli e' solo spreco di tempo (~24s).
        if _looks_like_network_down(err_repr):
            _breaker_open(f"network down detected on {model}")
            # retryable=False per fermare la cascata immediatamente nel
            # chiamante generate_comment().
            return None, False
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
    # Circuit breaker: se l'ultima chiamata ha rilevato rete giu'
    # (DNS bloccato / no connection), skippiamo direttamente al fallback
    # txt senza tentare nemmeno il primo modello. Riproviamo dopo
    # _BREAKER_COOLDOWN_S (vedi _breaker_is_open).
    if _breaker_is_open():
        logger.debug(
            "[ai-comment] circuit breaker aperto: skip diretto al fallback txt."
        )
        return None
    api_key = (
        getattr(args, "ai_comments_api_key", None)
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_AI_API_KEY")
    )
    hint = getattr(args, "ai_comments_prompt_hint", None) or None
    language = getattr(args, "ai_comments_language", None) or "Italian"

    # Context detection #1: tipo del post dalla caption -> tone instruction.
    post_type = _detect_post_type(caption or "")
    if post_type != "generic":
        logger.info(f"[ai-comment] detected post type: {post_type}")

    prompt = _build_prompt(
        caption=caption or "",
        target_username=target_username,
        media_type=str(media_type).lower(),
        hint=hint,
        language=language,
        post_type=post_type,
    )

    # Context detection #2: anti-ripetizione. Carichiamo storia ora cosi'
    # confrontiamo ogni candidate prodotto dalla cascata. Se l'utente non
    # ha una cartella account valida, account_path=None -> storia
    # disabilitata silenziosamente (degrade graceful).
    account_path = _account_path_from_args(args)
    history = _load_history(account_path) if account_path else []

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
            if cleaned is None:
                logger.info(
                    f"[ai-comment] {model}: output non conforme alle regole, "
                    f"provo prossimo modello."
                )
                continue
            # Anti-ripetizione: confronto con storico.
            is_dup, sim = _is_duplicate(cleaned, history)
            if is_dup:
                logger.info(
                    f"[ai-comment] {model}: output troppo simile a un commento "
                    f"recente (Jaccard={sim:.2f} >= {_HISTORY_SIM_THRESHOLD}). "
                    f"Provo prossimo modello."
                )
                continue
            # Successo: persist e ritorna.
            if idx > 0:
                logger.info(
                    f"[ai-comment] cascata ha funzionato: modello "
                    f"{model} ha generato dopo che {idx} modello/i "
                    f"avevano fallito."
                )
            if account_path:
                history.append(cleaned)
                _save_history(account_path, history)
            return cleaned

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
            f"[ai-comment] tutti i {len(chain)} modelli hanno fallito "
            f"o prodotto duplicati; fallback al file txt."
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

