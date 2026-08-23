import logging
import string
from datetime import datetime
from enum import Enum, auto
from inspect import stack
from os import getcwd, listdir
from random import randint, uniform
from re import compile as re_compile, search
from subprocess import PIPE, run
from time import sleep
from typing import Optional

import uiautomator2
from requests.exceptions import RequestException

try:
    from adbutils.errors import AdbError, AdbTimeout
except Exception:  # pragma: no cover - adbutils is always present in practice
    AdbError = OSError
    AdbTimeout = OSError

from GramAddict.core.utils import random_sleep

logger = logging.getLogger(__name__)

# Exceptions that mean the ADB server / atx-agent link dropped (device idle
# during a long throttle sleep, USB hiccup, momentary adb-server restart) — as
# opposed to a normal in-app UI error (JSONRPCError). These MUST be healed in
# place: letting them bubble up to run_safely() costs a full Instagram restart
# plus a counted crash (toward the crash limit that stops the bot), and loses
# the half-done interaction (e.g. a comment already typed but not posted).
CONNECTION_LOST_EXCEPTIONS = (RequestException, AdbError, AdbTimeout, OSError)


def create_device(device_id, app_id):
    try:
        return DeviceFacade(device_id, app_id)
    except ImportError as e:
        logger.error(str(e))
        return None


def get_device_info(device):
    # The first JSON-RPC call right after an atx-agent restart can fail with a
    # ProxyError ("Cannot connect to proxy") because the agent is not yet ready
    # to answer. Retry a few times before giving up to avoid crashing the bot
    # at the start of a new session.
    last_err = None
    for attempt in range(5):
        try:
            info = device.get_info()
            logger.debug(
                f"Phone Name: {info['productName']}, SDK Version: {info['sdkInt']}"
            )
            if int(info["sdkInt"]) < 19:
                logger.warning("Only Android 4.4+ (SDK 19+) devices are supported!")
            logger.debug(
                f"Screen dimension: {info['displayWidth']}x{info['displayHeight']}"
            )
            logger.debug(
                f"Screen resolution: {info['displaySizeDpX']}x{info['displaySizeDpY']}"
            )
            logger.debug(f"Device ID: {device.deviceV2.serial}")
            return
        except Exception as e:
            last_err = e
            logger.warning(
                f"get_device_info: attempt {attempt + 1}/5 failed ({type(e).__name__}: {e}). "
                f"Retrying in 2s..."
            )
            sleep(2.0)
    logger.error(
        f"get_device_info: device did not respond after retries (last error: {last_err}). "
        f"Continuing without device info."
    )


class Timeout(Enum):
    ZERO = auto()
    TINY = auto()
    SHORT = auto()
    MEDIUM = auto()
    LONG = auto()


class SleepTime(Enum):
    ZERO = auto()
    TINY = auto()
    SHORT = auto()
    DEFAULT = auto()


class Location(Enum):
    CUSTOM = auto()
    WHOLE = auto()
    CENTER = auto()
    BOTTOM = auto()
    RIGHT = auto()
    LEFT = auto()
    BOTTOMRIGHT = auto()
    LEFTEDGE = auto()
    RIGHTEDGE = auto()
    TOPLEFT = auto()


class Direction(Enum):
    UP = auto()
    DOWN = auto()
    RIGHT = auto()
    LEFT = auto()


class Mode(Enum):
    TYPE = auto()
    PASTE = auto()


_LOGIN_TEXT_RE = re_compile(
    r"(?i)^(log in|login|accedi|forgot password\?|password dimenticata\?|"
    r"hai dimenticato la password\?|"
    # schermata "scegli l'account" che IG 300 mostra PRIMA della password
    # quando la sessione e' scaduta (personal, 22/08 19:53: 'Continue',
    # 'Use another profile', 'Create new account')
    r"use another profile|usa un altro profilo|create new account|"
    r"crea (un )?nuovo account)$"
)
_CHOOSER_TEXT_RE = re_compile(r"(?i)^(use another profile|usa un altro profilo)$")
_CONTINUE_TEXT_RE = r"(?i)^(continue|continua)$"


def _is_secret_node(n: dict, schermata_login: bool = False) -> bool:
    """True se il testo del nodo va mascherato nei log: campo marcato
    password="true" dal dump, id che parla di password, oppure -- su una
    schermata di login -- qualunque campo di testo (con la password resa
    visibile dall'occhio l'attributo password torna false e l'id puo' non
    aiutare)."""
    if n.get("password") or "password" in n.get("resource_id", "").lower():
        return True
    return schermata_login and n.get("cls", "").endswith("EditText")


def _looks_like_login(nodi: list) -> bool:
    """Schermata di login di Instagram: c'e' un campo password oppure i
    testi tipici (Log in / Forgot password?)."""
    return any(
        n.get("password") or _LOGIN_TEXT_RE.match(n.get("text", "").strip())
        for n in nodi
    )


class DeviceFacade:
    def __init__(self, device_id, app_id):
        self.device_id = device_id
        self.app_id = app_id
        try:
            if device_id is None or "." not in device_id:
                self.deviceV2 = uiautomator2.connect(
                    "" if device_id is None else device_id
                )
            else:
                self.deviceV2 = uiautomator2.connect_adb_wifi(f"{device_id}")
        except ImportError:
            raise ImportError("Please install uiautomator2: pip3 install uiautomator2")

    def _get_current_app(self):
        try:
            return self.deviceV2.app_current()["package"]
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)

    def _ig_is_opened(self) -> bool:
        return self._get_current_app() == self.app_id

    def check_if_ig_is_opened(func):
        def wrapper(self, **kwargs):
            avoid_lst = ["choose_cloned_app", "check_if_crash_popup_is_there"]
            caller = stack()[1].function
            if not self._ig_is_opened() and caller not in avoid_lst:
                raise DeviceFacade.AppHasCrashed("App has crashed / has been closed!")
            return func(self, **kwargs)

        return wrapper

    def nodes_from_dump(self, xml: Optional[str] = None) -> list:
        """Legge l'albero COMPLETO della schermata e lo restituisce come lista
        di dict: resource_id, text, desc, cls, package, clickable, bounds.

        Perche' esiste: nel processo del bot le query dei selettori
        (exists, count) a volte negano elementi che sono a schermo -- e' stato
        visto sull'avatar del profilo, sul contenitore delle schede dei post,
        sulla lista follower -- mentre dump_hierarchy, che legge l'albero di
        accessibilita' per intero, li riporta sempre. Un solo dump serve a
        tutte le domande su quella schermata (c'e' il profilo? c'e' un
        dialogo? qual e' la descrizione del primo post?), per questo la
        lettura e' separata dalla ricerca.
        Lista vuota = dump fallito o schermata senza nodi: chi chiama non deve
        dedurne "non c'e' niente" e premere tasti a caso.
        """
        import html as _html
        import re as _re

        if xml is None:
            if self is None:
                return []
            try:
                xml = self.deviceV2.dump_hierarchy(compressed=False)
            except Exception as e:
                logger.debug(f"nodes_from_dump: dump failed: {e}")
                return []
        nodi = []
        for m in _re.finditer(r"<node[^>]*>", xml):
            node = m.group(0)

            def attr(nome):
                a = _re.search(rf'(?<![\w-]){nome}="([^"]*)"', node)
                return _html.unescape(a.group(1)) if a else ""

            b = _re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
            bounds = None
            if b:
                l, t, r, btm = (int(x) for x in b.groups())
                bounds = {"left": l, "top": t, "right": r, "bottom": btm}
            nodi.append(
                {
                    "resource_id": attr("resource-id"),
                    "text": attr("text"),
                    "desc": attr("content-desc"),
                    "cls": attr("class"),
                    "package": attr("package"),
                    "clickable": attr("clickable") == "true",
                    # campo password (attributo standard del dump): il suo
                    # testo non deve mai finire nei log, vedi screen_summary
                    "password": attr("password") == "true",
                    "bounds": bounds,
                }
            )
        return nodi

    @staticmethod
    def node_in_dump(nodi: list, resource_id_regex: Optional[str] = None,
                     text_regex: Optional[str] = None):
        """Primo nodo della lista che soddisfa i criteri dati (fullmatch),
        oppure None. resource_id_regex va scritto con l'id completo
        (package:id/nome); piu' id si uniscono con '|'."""
        import re as _re

        pat_id = _re.compile(resource_id_regex) if resource_id_regex else None
        pat_tx = _re.compile(text_regex) if text_regex else None
        for n in nodi:
            if pat_id and not pat_id.fullmatch(n["resource_id"]):
                continue
            if pat_tx and not pat_tx.fullmatch(n["text"]):
                continue
            if n["bounds"] is None:
                continue
            return n
        return None

    def bounds_from_dump(self, resource_id_regex: str, nodi: Optional[list] = None):
        """Bounds {left, top, right, bottom} del primo elemento con quel
        resource-id nell'albero completo, oppure None. Ripiego per i punti in
        cui un falso "non c'e'" del selettore costa un profilo o una sorgente
        intera (vedi nodes_from_dump)."""
        n = self.node_in_dump(
            nodi if nodi is not None else self.nodes_from_dump(), resource_id_regex
        )
        return n["bounds"] if n else None

    def desc_from_dump(self, resource_id_regex: str, nodi: Optional[list] = None) -> str:
        """content-desc del primo elemento con quel resource-id nell'albero
        completo, oppure stringa vuota."""
        n = self.node_in_dump(
            nodi if nodi is not None else self.nodes_from_dump(), resource_id_regex
        )
        return n["desc"] if n else ""

    def text_from_dump(self, resource_id_regex: str, nodi: Optional[list] = None) -> str:
        """text del primo elemento con quel resource-id nell'albero completo,
        oppure stringa vuota."""
        n = self.node_in_dump(
            nodi if nodi is not None else self.nodes_from_dump(), resource_id_regex
        )
        return n["text"] if n else ""

    def box_from_dump(self, resource_id_regex: str, nodi=None):
        """Come bounds_from_dump, ma restituisce un oggetto con click() e
        double_click() (DumpBox) utilizzabile al posto di una View quando il
        selettore nega un elemento che l'albero completo riporta. None se
        l'elemento non c'e' nemmeno nel dump."""
        b = self.bounds_from_dump(resource_id_regex, nodi)
        return None if b is None else DeviceFacade.DumpBox(self, b)

    class DumpBox:
        """Surrogato minimo di View per un nodo visto solo nel dump: tocca
        dentro i suoi bounds con un po' di casualita', come fa View."""

        def __init__(self, device, bounds: dict):
            self.device = device
            self.bounds = bounds

        def exists(self, *args, **kwargs) -> bool:
            return True

        def get_bounds(self) -> dict:
            return self.bounds

        def _random_point(self, padding: float = 0.3):
            b = self.bounds
            dx = int(padding * (b["right"] - b["left"]))
            dy = int(padding * (b["bottom"] - b["top"]))
            return (
                int(uniform(b["left"] + dx, b["right"] - dx)),
                int(uniform(b["top"] + dy, b["bottom"] - dy)),
            )

        def click(self, *args, **kwargs):
            x, y = self._random_point()
            logger.debug(f"Click from dump in ({x},{y}). Surface: {self.bounds}")
            self.device.deviceV2.click(x, y)
            DeviceFacade.sleep_mode(SleepTime.DEFAULT)

        def double_click(self, *args, **kwargs):
            x, y = self._random_point()
            t = uniform(0.050, 0.140)
            logger.debug(f"Double click from dump in ({x},{y}) with t={int(t*1000)}ms. Surface: {self.bounds}")
            self.device.deviceV2.double_click(x, y, duration=t)
            DeviceFacade.sleep_mode(SleepTime.DEFAULT)

    def tap_node(self, nodo: dict, motivo: str = ""):
        """Tocca il centro dei bounds di un nodo letto dal dump."""
        b = nodo["bounds"]
        x, y = (b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2
        logger.debug(f"Tap from dump in ({x},{y}) {motivo}".rstrip())
        self.deviceV2.click(x, y)
        random_sleep()

    @staticmethod
    def is_login_screen(nodi: list) -> bool:
        """True se il dump e' la schermata di login di Instagram (sessione
        scaduta): il bot non deve inserire credenziali ne' premere back."""
        return _looks_like_login(nodi)

    @staticmethod
    def account_chooser_continue(nodi: list):
        """Nella schermata "scegli l'account" (sessione scaduta, account
        salvato) restituisce il nodo del pulsante Continue/Continua, altrimenti
        None. Toccarlo non inserisce credenziali: riprende l'account salvato
        e, se il cookie vale ancora, evita del tutto la password."""
        if not any(_CHOOSER_TEXT_RE.match(n.get("text", "").strip()) for n in nodi):
            return None
        return DeviceFacade.node_in_dump(nodi, text_regex=_CONTINUE_TEXT_RE)

    @staticmethod
    def mask_secrets_in_xml(xml: str) -> str:
        """Oscura nel dump grezzo il testo dei campi password (e, su una
        schermata di login, di ogni campo di testo): il dump finisce negli
        zip dei crash, che non devono contenere la password in chiaro."""
        import re as _re

        login = _looks_like_login(DeviceFacade.nodes_from_dump(None, xml))

        def maschera(m):
            node = m.group(0)
            rid = _re.search(r'(?<![\w-])resource-id="([^"]*)"', node)
            cls = _re.search(r'(?<![\w-])class="([^"]*)"', node)
            segreto = (
                'password="true"' in node
                or (rid is not None and "password" in rid.group(1).lower())
                or (login and cls is not None and cls.group(1).endswith("EditText"))
            )
            if not segreto:
                return node
            return _re.sub(r'(?<![\w-])text="[^"]*"', 'text="***"', node)

        return _re.sub(r"<node[^>]*>", maschera, xml)

    @staticmethod
    def screen_summary(nodi: list, max_testi: int = 12, max_ids: int = 30) -> str:
        """Riassunto leggibile di una schermata, per i log di diagnosi: quali
        package, quali testi, quali resource-id (senza il prefisso del
        package). Serve quando il bot non riconosce dove si trova: senza
        questa riga la causa si indovina soltanto."""
        pacchetti = sorted({n["package"] for n in nodi if n["package"]})
        # Mai scrivere nel log il contenuto di un campo password: il 22/08 la
        # schermata di login del personal e' finita nel riepilogo con la
        # password in chiaro (l'utente l'aveva resa visibile con l'occhio).
        # Si maschera sia il campo marcato password="true" dal dump, sia
        # qualunque campo il cui id parli di password (a visibilita' attiva
        # l'attributo torna false, ma l'id resta).
        login = _looks_like_login(nodi)
        testi = [
            "***" if _is_secret_node(n, login) else n["text"][:60]
            for n in nodi
            if n["text"].strip()
        ][:max_testi]
        ids = sorted({n["resource_id"].split(":id/")[-1] for n in nodi if n["resource_id"]})[:max_ids]
        return f"packages={pacchetti} texts={testi} ids={ids}"

    @check_if_ig_is_opened
    def find(
        self,
        index=None,
        **kwargs,
    ):
        try:
            view = self.deviceV2(**kwargs)
            if index is not None and view.count > 1:
                view = self.deviceV2(**kwargs)[index]
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)
        return DeviceFacade.View(view=view, device=self.deviceV2)

    def back(self, modulable: bool = True):
        logger.debug("Press back button.")
        self.deviceV2.press("back")
        random_sleep(modulable=modulable)

    def start_screenrecord(self, output="debug_0000.mp4", fps=20):
        import imageio

        def _run_MOD(self):
            from collections import deque

            pipelines = [self._pipe_limit, self._pipe_convert, self._pipe_resize]
            _iter = self._iter_minicap()
            for p in pipelines:
                _iter = p(_iter)

            with imageio.get_writer(self._filename, fps=self._fps) as wr:
                frames = deque(maxlen=self._fps * 30)
                for im in _iter:
                    frames.append(im)
                if self.crash:
                    for frame in frames:
                        wr.append_data(frame)
            self._done_event.set()

        def stop_MOD(self, crash=True):
            """
            stop record and finish write video
            Returns:
                bool: whether video is recorded.
            """
            if self._running:
                self.crash = crash
                self._stop_event.set()
                ret = self._done_event.wait(10.0)

                # reset
                self._stop_event.clear()
                self._done_event.clear()
                self._running = False
                return ret

        from uiautomator2 import screenrecord as _sr

        _sr.Screenrecord._run = _run_MOD
        _sr.Screenrecord.stop = stop_MOD
        mp4_files = [f for f in listdir(getcwd()) if f.endswith(".mp4")]
        if mp4_files:
            last_mp4 = mp4_files[-1]
            debug_number = "{0:0=4d}".format(int(last_mp4[-8:-4]) + 1)
            output = f"debug_{debug_number}.mp4"
        self.deviceV2.screenrecord(output, fps)
        logger.warning("Screen recording has been started.")

    def stop_screenrecord(self, crash=True):
        if self.deviceV2.screenrecord.stop(crash=crash):
            logger.warning("Screen recorder has been stopped successfully!")

    def screenshot(self, path=None):
        if path is None:
            return self.deviceV2.screenshot()
        else:
            self.deviceV2.screenshot(path)

    def dump_hierarchy(self, path, xml_dump=None):
        if xml_dump is None:
            xml_dump = self.deviceV2.dump_hierarchy()
        with open(path, "w", encoding="utf-8") as outfile:
            outfile.write(DeviceFacade.mask_secrets_in_xml(xml_dump))

    def press_power(self):
        self.deviceV2.press("power")
        sleep(2)

    def is_screen_locked(self):
        data = run(
            f"adb -s {self.deviceV2.serial} shell dumpsys window",
            encoding="utf-8",
            stdout=PIPE,
            stderr=PIPE,
            shell=True,
        )
        if data != "":
            flag = search("mDreamingLockscreen=(true|false)", data.stdout)
            return flag is not None and flag.group(1) == "true"
        else:
            logger.debug(
                f"'adb -s {self.deviceV2.serial} shell dumpsys window' returns nothing!"
            )
            return None

    def _is_keyboard_show(self):
        data = run(
            f"adb -s {self.deviceV2.serial} shell dumpsys input_method",
            encoding="utf-8",
            stdout=PIPE,
            stderr=PIPE,
            shell=True,
        )
        if data != "":
            flag = search("mInputShown=(true|false)", data.stdout)
            return flag.group(1) == "true"
        else:
            logger.debug(
                f"'adb -s {self.deviceV2.serial} shell dumpsys input_method' returns nothing!"
            )
            return None

    def is_alive(self):
        try:
            return self.deviceV2._is_alive()  # deprecated method
        except AttributeError:
            try:
                return self.deviceV2.server.alive
            except Exception:
                return False
        except Exception:
            return False

    def wake_up(self):
        """Make sure agent is alive or bring it back up before starting."""
        if self.deviceV2 is not None:
            attempts = 0
            while not self.is_alive() and attempts < 5:
                try:
                    self.get_info()
                except Exception:
                    DeviceFacade._heal_connection(self.deviceV2, tries=1)
                attempts += 1

    @staticmethod
    def _heal_connection(deviceV2, tries: int = 4, base_delay: float = 4.0) -> bool:
        """Try to bring the atx-agent / adb link back after a connection drop.

        Returns True if the device answers again. uiautomator2 re-establishes
        the adb port-forward on every request, so a momentary adb-server hiccup
        (the usual cause of ``RemoteDisconnected`` / ``AdbError`` after a long
        idle throttle sleep) normally clears by probing again after a short
        wait; we also force a uiautomator reset midway as a stronger remedy.
        """
        if deviceV2 is None:
            return False
        for attempt in range(1, tries + 1):
            delay = base_delay * attempt
            logger.warning(
                f"[device] ADB/atx-agent link lost. Healing attempt "
                f"{attempt}/{tries}, waiting {delay:.0f}s..."
            )
            sleep(delay)
            try:
                if attempt == 2 and hasattr(deviceV2, "reset_uiautomator"):
                    deviceV2.reset_uiautomator("connection lost")
                _ = deviceV2.info  # cheap probe; forces re-forward of port 7912
                logger.info("[device] connection re-established.")
                return True
            except Exception as e:
                logger.warning(
                    f"[device] healing probe failed ({type(e).__name__}: {e})."
                )
        logger.error("[device] could not re-establish connection after retries.")
        return False

    def unlock(self):
        self.swipe(Direction.UP, 0.8)
        sleep(2)
        logger.debug(f"Screen locked: {self.is_screen_locked()}")
        if self.is_screen_locked():
            self.swipe(Direction.RIGHT, 0.8)
            sleep(2)
            logger.debug(f"Screen locked: {self.is_screen_locked()}")

    def screen_off(self):
        self.deviceV2.screen_off()

    def get_orientation(self):
        try:
            return self.deviceV2._get_orientation()
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)

    def window_size(self):
        """return (width, height)"""
        try:
            self.deviceV2.window_size()
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)

    def swipe(self, direction: Direction, scale=0.5):
        """Swipe finger in the `direction`.
        Scale is the sliding distance. Default to 50% of the screen width
        """
        swipe_dir = ""
        if direction == Direction.UP:
            swipe_dir = "up"
        elif direction == Direction.RIGHT:
            swipe_dir = "right"
        elif direction == Direction.LEFT:
            swipe_dir = "left"
        elif direction == Direction.DOWN:
            swipe_dir = "down"

        logger.debug(f"Swipe {swipe_dir}, scale={scale}")

        try:
            self.deviceV2.swipe_ext(swipe_dir, scale=scale)
            DeviceFacade.sleep_mode(SleepTime.TINY)
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)

    def swipe_points(self, sx, sy, ex, ey, random_x=True, random_y=True):
        if random_x:
            sx = int(sx * uniform(0.85, 1.15))
            ex = int(ex * uniform(0.85, 1.15))
        if random_y:
            ey = int(ey * uniform(0.98, 1.02))
        sy = int(sy)
        try:
            logger.debug(f"Swipe from: ({sx},{sy}) to ({ex},{ey}).")
            self.deviceV2.swipe_points([[sx, sy], [ex, ey]], uniform(0.2, 0.5))
            DeviceFacade.sleep_mode(SleepTime.TINY)
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)

    def get_info(self):
        # {'currentPackageName': 'net.oneplus.launcher', 'displayHeight': 1920, 'displayRotation': 0, 'displaySizeDpX': 411,
        # 'displaySizeDpY': 731, 'displayWidth': 1080, 'productName': 'OnePlus5', '
        #  screenOn': True, 'sdkInt': 27, 'naturalOrientation': True}
        try:
            return self.deviceV2.info
        except uiautomator2.JSONRPCError as e:
            raise DeviceFacade.JsonRpcError(e)

    @staticmethod
    def sleep_mode(mode):
        mode = SleepTime.DEFAULT if mode is None else mode
        if mode == SleepTime.DEFAULT:
            random_sleep()
        elif mode == SleepTime.TINY:
            random_sleep(0, 1)
        elif mode == SleepTime.SHORT:
            random_sleep(1, 2)
        elif mode == SleepTime.ZERO:
            pass

    class View:
        deviceV2 = None  # uiautomator2
        viewV2 = None  # uiautomator2

        def __init__(self, view, device):
            self.viewV2 = view
            self.deviceV2 = device

        def _connection_safe(self, op, *, what: str):
            """Run ``op`` (a no-arg callable that hits the device); if the
            ADB/atx-agent link drops, heal it and retry once instead of letting
            the exception bubble up to run_safely() — which would abort the
            whole interaction and count a crash. JSONRPCError (a normal in-app
            UI error) is left untouched for the caller to handle."""
            try:
                return op()
            except CONNECTION_LOST_EXCEPTIONS as e:
                logger.warning(
                    f"[device] {what}: connection lost "
                    f"({type(e).__name__}: {e}). Trying to recover in place."
                )
                if DeviceFacade._heal_connection(self.deviceV2):
                    return op()
                raise

        def __iter__(self):
            children = []
            try:
                children.extend(
                    DeviceFacade.View(view=item, device=self.deviceV2)
                    for item in self.viewV2
                )
                return iter(children)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def ui_info(self):
            try:
                return self.viewV2.info
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def get_desc(self):
            try:
                return self.viewV2.info["contentDescription"]
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def child(self, *args, **kwargs):
            try:
                view = self.viewV2.child(*args, **kwargs)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)
            return DeviceFacade.View(view=view, device=self.deviceV2)

        def sibling(self, *args, **kwargs):
            try:
                view = self.viewV2.sibling(*args, **kwargs)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)
            return DeviceFacade.View(view=view, device=self.deviceV2)

        def left(self, *args, **kwargs):
            try:
                view = self.viewV2.left(*args, **kwargs)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)
            return DeviceFacade.View(view=view, device=self.deviceV2)

        def right(self, *args, **kwargs):
            try:
                view = self.viewV2.right(*args, **kwargs)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)
            return DeviceFacade.View(view=view, device=self.deviceV2)

        def up(self, *args, **kwargs):
            try:
                view = self.viewV2.up(*args, **kwargs)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)
            return DeviceFacade.View(view=view, device=self.deviceV2)

        def down(self, *args, **kwargs):
            try:
                view = self.viewV2.down(*args, **kwargs)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)
            return DeviceFacade.View(view=view, device=self.deviceV2)

        def click_gone(self, maxretry=3, interval=1.0):
            try:
                self.viewV2.click_gone(maxretry, interval)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def click(self, mode=None, sleep=None, coord=None, crash_report_if_fails=True):
            if coord is None:
                coord = []
            mode = Location.WHOLE if mode is None else mode
            if mode == Location.WHOLE:
                x_offset = uniform(0.15, 0.85)
                y_offset = uniform(0.15, 0.85)

            elif mode == Location.LEFT:
                x_offset = uniform(0.15, 0.4)
                y_offset = uniform(0.15, 0.85)

            elif mode == Location.LEFTEDGE:
                x_offset = uniform(0.1, 0.2)
                y_offset = uniform(0.40, 0.60)

            elif mode == Location.CENTER:
                x_offset = uniform(0.4, 0.6)
                y_offset = uniform(0.15, 0.85)

            elif mode == Location.RIGHT:
                x_offset = uniform(0.6, 0.85)
                y_offset = uniform(0.15, 0.85)

            elif mode == Location.RIGHTEDGE:
                x_offset = uniform(0.8, 0.9)
                y_offset = uniform(0.40, 0.60)

            elif mode == Location.BOTTOMRIGHT:
                x_offset = uniform(0.8, 0.9)
                y_offset = uniform(0.8, 0.9)

            elif mode == Location.TOPLEFT:
                x_offset = uniform(0.05, 0.15)
                y_offset = uniform(0.05, 0.25)
            elif mode == Location.CUSTOM:
                try:
                    logger.debug(f"Single click ({coord[0]},{coord[1]})")
                    self.deviceV2.click(coord[0], coord[1])
                    DeviceFacade.sleep_mode(sleep)
                    return
                except uiautomator2.JSONRPCError as e:
                    if crash_report_if_fails:
                        raise DeviceFacade.JsonRpcError(e)
                    else:
                        logger.debug("Trying to press on a obj which is gone.")

            else:
                x_offset = 0.5
                y_offset = 0.5

            try:
                visible_bounds = self.get_bounds()
                x_abs = int(
                    visible_bounds["left"]
                    + (visible_bounds["right"] - visible_bounds["left"]) * x_offset
                )
                y_abs = int(
                    visible_bounds["top"]
                    + (visible_bounds["bottom"] - visible_bounds["top"]) * y_offset
                )

                logger.debug(
                    f"Single click in ({x_abs},{y_abs}). Surface: ({visible_bounds['left']}-{visible_bounds['right']},{visible_bounds['top']}-{visible_bounds['bottom']})"
                )
                self._connection_safe(
                    lambda: self.viewV2.click(
                        self.get_ui_timeout(Timeout.LONG),
                        offset=(x_offset, y_offset),
                    ),
                    what="click",
                )
                DeviceFacade.sleep_mode(sleep)

            except uiautomator2.JSONRPCError as e:
                if crash_report_if_fails:
                    raise DeviceFacade.JsonRpcError(e)
                else:
                    logger.debug("Trying to press on a obj which is gone.")

        def click_retry(self, mode=None, sleep=None, coord=None, maxretry=2):
            """return True if successfully open the element, else False"""
            if coord is None:
                coord = []
            self.click(mode, sleep, coord)

            while maxretry > 0:
                # we wait a little more before try again
                random_sleep(2, 4, modulable=False)
                if not self.exists():
                    return True
                logger.debug("UI element didn't open! Try again..")
                self.click(mode, sleep, coord)
                maxretry -= 1
            if not self.exists():
                return True
            logger.warning("Failed to open the UI element!")
            return False

        def double_click(self, padding=0.3, obj_over=0):
            """Double click randomly in the selected view using padding
            padding: % of how far from the borders we want the double
                    click to happen.
            """
            visible_bounds = self.get_bounds()
            horizontal_len = visible_bounds["right"] - visible_bounds["left"]
            vertical_len = visible_bounds["bottom"] - max(
                visible_bounds["top"], obj_over
            )
            horizontal_padding = int(padding * horizontal_len)
            vertical_padding = int(padding * vertical_len)
            random_x = int(
                uniform(
                    visible_bounds["left"] + horizontal_padding,
                    visible_bounds["right"] - horizontal_padding,
                )
            )
            random_y = int(
                uniform(
                    visible_bounds["top"] + vertical_padding,
                    visible_bounds["bottom"] - vertical_padding,
                )
            )

            time_between_clicks = uniform(0.050, 0.140)

            try:
                logger.debug(
                    f"Double click in ({random_x},{random_y}) with t={int(time_between_clicks*1000)}ms. Surface: ({visible_bounds['left']}-{visible_bounds['right']},{visible_bounds['top']}-{visible_bounds['bottom']})."
                )
                self.deviceV2.double_click(
                    random_x, random_y, duration=time_between_clicks
                )
                DeviceFacade.sleep_mode(SleepTime.DEFAULT)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def scroll(self, direction):
            try:
                if direction == Direction.UP:
                    self.viewV2.scroll.toBeginning(max_swipes=1)
                else:
                    self.viewV2.scroll.toEnd(max_swipes=1)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def fling(self, direction):
            try:
                if direction == Direction.UP:
                    self.viewV2.fling.toBeginning(max_swipes=5)
                else:
                    self.viewV2.fling.toEnd(max_swipes=5)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def exists(self, ui_timeout=None, ignore_bug: bool = False) -> bool:
            try:
                # Currently, the methods left, right, up and down from
                # uiautomator2 return None when a Selector does not exist.
                # All other selectors return an UiObject with exists() == False.
                # We will open a ticket to uiautomator2 to fix this inconsistency.
                if self.viewV2 is None:
                    return False
                exists: bool = self.viewV2.exists(self.get_ui_timeout(ui_timeout))
                if (
                    hasattr(self.viewV2, "count")
                    and not exists
                    and self.viewV2.count >= 1
                ):
                    logger.debug(
                        f"UIA2 BUG: exists return False, but there is/are {self.viewV2.count} element(s)!"
                    )
                    if ignore_bug:
                        return "BUG!"
                    # More info about that: https://github.com/openatx/uiautomator2/issues/689"
                    return False
                return exists
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def count_items(self) -> int:
            try:
                return self.viewV2.count
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def wait(self, ui_timeout=Timeout.MEDIUM):
            try:
                return self.viewV2.wait(timeout=self.get_ui_timeout(ui_timeout))
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def wait_gone(self, ui_timeout=None):
            try:
                return self.viewV2.wait_gone(timeout=self.get_ui_timeout(ui_timeout))
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def is_above_this(self, obj2) -> Optional[bool]:
            obj1 = self.viewV2
            obj2 = obj2.viewV2
            try:
                if obj1.exists() and obj2.exists():
                    return obj1.info["bounds"]["top"] < obj2.info["bounds"]["top"]
                else:
                    return None
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def get_bounds(self) -> dict:
            try:
                return self._connection_safe(
                    lambda: self.viewV2.info["bounds"], what="get_bounds"
                )
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def get_height(self) -> int:
            bounds = self.get_bounds()
            return bounds["bottom"] - bounds["top"]

        def get_width(self):
            bounds = self.get_bounds()
            return bounds["right"] - bounds["left"]

        def get_property(self, prop: str):
            try:
                return self.viewV2.info[prop]
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def is_scrollable(self):
            try:
                if self.viewV2.exists():
                    return self.viewV2.info["scrollable"]
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        @staticmethod
        def get_ui_timeout(ui_timeout: Timeout) -> int:
            ui_timeout = Timeout.ZERO if ui_timeout is None else ui_timeout
            if ui_timeout == Timeout.ZERO:
                ui_timeout = 0
            elif ui_timeout == Timeout.TINY:
                ui_timeout = 1
            elif ui_timeout == Timeout.SHORT:
                ui_timeout = 3
            elif ui_timeout == Timeout.MEDIUM:
                ui_timeout = 5
            elif ui_timeout == Timeout.LONG:
                ui_timeout = 8
            return ui_timeout

        def get_text(self, error=True, index=None):
            try:
                text = (
                    self.viewV2.info["text"]
                    if index is None
                    else self.viewV2[index].info["text"]
                )
                if text is not None:
                    return text
            except uiautomator2.JSONRPCError as e:
                if error:
                    raise DeviceFacade.JsonRpcError(e)
                else:
                    return ""
            logger.debug("Object exists but doesn't contain any text.")
            return ""

        def get_selected(self) -> bool:
            try:
                if self.viewV2.exists():
                    return self.viewV2.info["selected"]
                logger.debug(
                    "Object has disappeared! Probably too short video which has been liked!"
                )
                return True
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

        def set_text(self, text: str, mode: Mode = Mode.TYPE) -> None:
            punct_list = string.punctuation
            try:
                if mode == Mode.PASTE:
                    self.viewV2.set_text(text)
                else:
                    self.click(sleep=SleepTime.SHORT)
                    self.deviceV2.clear_text()
                    random_sleep(0.3, 1, modulable=False)
                    start = datetime.now()
                    sentences = text.splitlines()
                    for j, sentence in enumerate(sentences, start=1):
                        word_list = sentence.split()
                        n_words = len(word_list)
                        for n, word in enumerate(word_list, start=1):
                            i = 0
                            n_single_letters = randint(1, 3)
                            for char in word:
                                if i < n_single_letters:
                                    self.deviceV2.send_keys(char, clear=False)
                                    # random_sleep(0.01, 0.1, modulable=False, logging=False)
                                    i += 1
                                else:
                                    if word[-1] in punct_list:
                                        self.deviceV2.send_keys(word[i:-1], clear=False)
                                        # random_sleep(0.01, 0.1, modulable=False, logging=False)
                                        self.deviceV2.send_keys(word[-1], clear=False)
                                    else:
                                        self.deviceV2.send_keys(word[i:], clear=False)
                                    # random_sleep(0.01, 0.1, modulable=False, logging=False)
                                    break
                            if n < n_words:
                                self.deviceV2.send_keys(" ", clear=False)
                                # random_sleep(0.01, 0.1, modulable=False, logging=False)
                        if j < len(sentences):
                            self.deviceV2.send_keys("\n")

                    typed_text = self.viewV2.get_text()
                    if typed_text != text:
                        logger.warning(
                            "Failed to write in text field, let's try in the old way.."
                        )
                        self.viewV2.set_text(text)
                    else:
                        logger.debug(
                            f"Text typed in: {(datetime.now()-start).total_seconds():.2f}s"
                        )
                DeviceFacade.sleep_mode(SleepTime.SHORT)
            except uiautomator2.JSONRPCError as e:
                raise DeviceFacade.JsonRpcError(e)

    class JsonRpcError(Exception):
        pass

    class LoginRequired(Exception):
        """Instagram chiede la password: solo l'utente puo' sbloccare la
        situazione, dal device. Non e' un crash e non va trattato come tale."""

    class AppHasCrashed(Exception):
        pass
