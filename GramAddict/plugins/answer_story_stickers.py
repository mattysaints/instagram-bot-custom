"""Risponde agli sticker delle storie altrui (box domande, sondaggi, quiz).

Scopo: farsi notare da chi conta. Un influencer pubblica "cosa alleni oggi?"
nelle storie, il bot risponde "petto" e il nome finisce nella sua casella delle
risposte, insieme a poche decine di altri invece che a migliaia di commenti.

Resource-id verificati su Instagram 300.0.0.29.110 il 19/08/2026:
    question_sticker_container_view   FrameLayout, clickable -> apre il pannello
    question_sticker_view             ImageView, content-desc con la domanda:
                                      "Question sticker. <DOMANDA>. Double tap to reply."
    question_sticker_answer           EditText dove si scrive
    question_sticker_send_button      compare SOLO dopo aver scritto
    cancel_button                     per uscire senza inviare

Due note imparate sul campo:
  * "Double tap to reply" nella content-desc e' la dicitura per TalkBack: con
    uiautomator serve un CLICK SINGOLO. Il double_click non apre niente e viene
    letto come due tap, che fanno avanzare la storia.
  * Nella vista da PROPRIETARIO gli sticker non compaiono nell'albero di
    accessibilita'. Compaiono solo guardando la storia di un altro, che poi e'
    l'unico caso che interessa qui.

Sondaggi, quiz e slider non sono ancora implementati: servono i loro
resource-id, e per averli serve una storia vera che li contenga. Quando lo
sticker non e' un box domande, viene semplicemente saltato.
"""
import logging
from random import seed, shuffle

from colorama import Style

from GramAddict.core.ai_sticker import (
    STICKER_QUESTION,
    generate_sticker_reply,
    is_enabled as sticker_ai_enabled,
)
from GramAddict.core.decorators import run_safely
from GramAddict.core.device_facade import Direction, Timeout
from GramAddict.core.navigation import nav_to_blogger
from GramAddict.core.plugin_loader import Plugin
from GramAddict.core.utils import get_value, random_sleep

logger = logging.getLogger(__name__)

seed()

# --- resource-id (senza prefisso app-id, aggiunto a runtime) ---
_STICKER_CONTAINER = "question_sticker_container_view"
_STICKER_VIEW = "question_sticker_view"
_ANSWER_FIELD = "question_sticker_answer"
_SEND_BUTTON = "question_sticker_send_button"
_CANCEL_BUTTON = "cancel_button"
_REEL_ROOT = "reel_viewer_root"
_REEL_TITLE = "reel_viewer_title"
_PROFILE_AVATAR = "row_profile_header_imageview"

# "Question sticker. Cosa alleni oggi?. Double tap to reply."
import re  # noqa: E402  (tenuto vicino alla regex che serve)

_DESC_RE = re.compile(
    r"^\s*Question sticker\.\s*(.*?)\.?\s*(?:Double tap to reply\.?)?\s*$",
    re.IGNORECASE | re.DOTALL,
)


class AnswerStoryStickers(Plugin):
    """Answers question stickers in other users' stories"""

    def __init__(self):
        super().__init__()
        self.description = "Answers question stickers in other users' stories"
        self.arguments = [
            {
                "arg": "--answer-story-stickers",
                "nargs": "+",
                "help": "list of usernames whose story stickers (question boxes) you want to answer",
                "metavar": ("username1", "username2"),
                "default": None,
                "operation": True,
            },
            {
                "arg": "--sticker-replies-limit",
                "nargs": None,
                "help": "max sticker replies per session (e.g. 5 or 4-7)",
                "metavar": "4-7",
                "default": "4-7",
            },
            {
                "arg": "--stories-per-source-limit",
                "nargs": None,
                "help": "how many stories to check per source before moving on",
                "metavar": "8",
                "default": "8",
            },
            {
                "arg": "--ai-sticker-enabled",
                "help": "enable AI replies to story stickers. If unset, inherits ai-comments-enabled.",
                "action": "store_true",
            },
            {
                "arg": "--sticker-check-percentage",
                "nargs": None,
                "help": (
                    "chance (0-100) of opening a candidate's stories during a normal "
                    "interaction to look for a question box. 0 disables it. Applies to "
                    "the standard flow (blogger-followers, hashtags...), not to the "
                    "answer-story-stickers job."
                ),
                "metavar": "60-80",
                "default": "0",
            },
            {
                "arg": "--ai-sticker-space-url",
                "nargs": None,
                "help": "Space URL for sticker replies (default: inherits ai-comments-space-url)",
                "metavar": "https://...hf.space",
                "default": None,
            },
            {
                "arg": "--ai-sticker-space-key",
                "nargs": None,
                "help": "bearer token for the sticker endpoint (default: inherits ai-comments-space-key)",
                "metavar": "sk_...",
                "default": None,
            },
            {
                "arg": "--ai-sticker-prompt-hint",
                "nargs": None,
                "help": "who is replying (default: inherits ai-comments-prompt-hint)",
                "metavar": "...",
                "default": None,
            },
            {
                "arg": "--ai-sticker-language",
                "nargs": None,
                "help": "reply language (default: inherits ai-comments-language)",
                "metavar": "Italian",
                "default": None,
            },
        ]

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _rid(self, short: str) -> str:
        return f"{self.args.app_id}:id/{short}"

    def _in_story(self, device) -> bool:
        return device.find(resourceId=self._rid(_REEL_ROOT)).exists()

    def _story_author(self, device) -> str:
        title = device.find(resourceId=self._rid(_REEL_TITLE))
        return title.get_text() if title.exists() else "?"

    def _read_question(self, device):
        """Ritorna il testo della domanda, o None se lo sticker non c'e'."""
        view = device.find(resourceId=self._rid(_STICKER_VIEW))
        if not view.exists():
            return None
        desc = view.ui_info().get("contentDescription") or ""
        if not desc:
            return None
        m = _DESC_RE.match(desc)
        return (m.group(1) if m else desc).strip().rstrip(".") or None

    def _open_stories(self, device, source: str) -> bool:
        """Va sul profilo del target e apre le sue storie. False se non ne ha."""
        # nav_to_blogger ritorna (success, followers_count)
        navigated, _ = nav_to_blogger(device, source, self.current_mode)
        if not navigated:
            return False
        avatar = device.find(resourceId=self._rid(_PROFILE_AVATAR))
        if not avatar.exists(Timeout.MEDIUM):
            logger.info(f"@{source}: avatar non trovato, salto.")
            return False
        desc = (avatar.ui_info().get("contentDescription") or "").lower()
        if "story" not in desc:
            logger.info(f"@{source}: nessuna storia attiva.")
            return False
        avatar.click()
        random_sleep(3, 5, modulable=False)
        return self._in_story(device)

    def _next_story(self, device) -> None:
        """Passa alla storia successiva.

        Swipe invece di tap sul bordo: e' il gesto che fa un utente vero, e
        DeviceFacade non espone un click a coordinate.
        """
        device.swipe(Direction.LEFT, scale=0.5)
        random_sleep(1, 2, modulable=False)

    def _answer(self, device, question: str, author: str) -> bool:
        """Apre il pannello, scrive la risposta e invia. True se inviata."""
        result = generate_sticker_reply(
            self.args,
            sticker_type=STICKER_QUESTION,
            prompt_text=question,
            author_username=author,
        )
        reply = result.get("reply")
        if not reply:
            if result.get("refused"):
                logger.info("Sticker su tema sensibile: non rispondo.")
            else:
                logger.info("Nessuna risposta generata: salto lo sticker.")
            return False

        container = device.find(resourceId=self._rid(_STICKER_CONTAINER))
        if not container.exists():
            logger.info("Lo sticker e' sparito prima del click.")
            return False

        # CLICK SINGOLO: il "double tap" della content-desc e' TalkBack.
        container.click()
        random_sleep(1, 2, modulable=False)

        field = device.find(resourceId=self._rid(_ANSWER_FIELD))
        if not field.exists(Timeout.MEDIUM):
            logger.warning("Campo di risposta non comparso: annullo.")
            self._dismiss(device)
            return False

        field.set_text(reply)
        random_sleep(1, 2, modulable=False)

        send = device.find(resourceId=self._rid(_SEND_BUTTON))
        if not send.exists(Timeout.MEDIUM):
            # il bottone compare solo a campo pieno: se manca, il testo non e'
            # stato scritto davvero
            logger.warning("Bottone Send assente: annullo senza inviare.")
            self._dismiss(device)
            return False

        send.click()
        random_sleep(2, 3, modulable=False)
        logger.info(
            f"Risposto a @{author}: '{reply}'", extra={"color": f"{Style.BRIGHT}"}
        )
        return True

    def _dismiss(self, device) -> None:
        cancel = device.find(resourceId=self._rid(_CANCEL_BUTTON))
        if cancel.exists():
            cancel.click()
        else:
            device.back()
        random_sleep(1, 2, modulable=False)

    # ------------------------------------------------------------------ #
    # entry point
    # ------------------------------------------------------------------ #
    def run(self, device, configs, storage, sessions, profile_filter, plugin):
        self.device_id = configs.args.device
        self.sessions = sessions
        self.session_state = sessions[-1]
        self.args = configs.args
        self.current_mode = plugin

        sources = [s.strip() for s in (self.args.answer_story_stickers or []) if s.strip()]
        if not sources:
            logger.warning("Nessuna sorgente in --answer-story-stickers. Skip.")
            return

        if not sticker_ai_enabled(self.args):
            logger.warning(
                "Risposte AI agli sticker disattivate (ai-sticker-enabled / "
                "ai-comments-enabled). Skip del job."
            )
            return

        replies_target = get_value(self.args.sticker_replies_limit, "Sticker replies: {}", 5)
        per_source = get_value(self.args.stories_per_source_limit, None, 8)
        sent = 0

        shuffle(sources)
        for source in sources:
            if sent >= replies_target:
                logger.info("Raggiunto il limite di risposte per questa sessione.")
                break

            source = source.lstrip("@")
            logger.info(f"Handle {source}", extra={"color": f"{Style.BRIGHT}"})

            @run_safely(
                device=device,
                device_id=self.device_id,
                sessions=self.sessions,
                session_state=self.session_state,
                screen_record=self.args.screen_record,
                configs=configs,
            )
            def job():
                nonlocal sent
                if not self._open_stories(device, source):
                    return
                for _ in range(per_source):
                    if sent >= replies_target:
                        break
                    if not self._in_story(device):
                        break
                    question = self._read_question(device)
                    if question:
                        author = self._story_author(device)
                        logger.info(f"Box domande di @{author}: '{question}'")
                        if self._answer(device, question, author):
                            sent += 1
                            # throttle: stessa cadenza dei commenti, queste
                            # risposte sono altrettanto visibili
                            random_sleep(
                                *self._throttle_range(), modulable=False
                            )
                    if not self._in_story(device):
                        break
                    self._next_story(device)
                # esci dalle storie
                while self._in_story(device):
                    device.back()
                    random_sleep(1, 2, modulable=False)

            job()

        logger.info(f"Risposte a sticker inviate in questa sessione: {sent}")

    def _throttle_range(self):
        """Pausa fra due risposte, riusando il throttle dei commenti."""
        raw = getattr(self.args, "action_throttle_comment_min", None) or "120-240"
        try:
            lo, _, hi = str(raw).partition("-")
            return int(lo), int(hi or lo)
        except Exception:
            return 120, 240
