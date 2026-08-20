"""Controllo remoto del bot via Telegram.

Serve a gestire il mini PC senza andarci fisicamente: da telefono si vede cosa
sta facendo, si ferma e si riavvia un account, si legge il log e si guarda lo
schermo dell'emulatore.

COMANDI
    /status              stato dei due account (in esecuzione? da quanto?)
    /stop <account>      mette in pausa un account (il watchdog non lo rilancia)
    /start <account>     toglie la pausa
    /log <account> [n]   ultime n righe di log (default 25)
    /shot <account>      screenshot dell'emulatore, per vedere dove sta il bot
    /help

Lo stop non uccide il watchdog: crea un file <account>.stop che il watchdog
controlla a ogni giro. Cosi' anche se il PC si riavvia, l'account resta in
pausa finche' non si manda /start.

CONFIGURAZIONE
    Riusa lo stesso schema di accounts/<username>/telegram.yml gia' usato dai
    report, ma legge deploy/telegram_control.yml:

        telegram-api-token: "123456:ABC..."     da @BotFather
        telegram-chat-id: "12345678"            da @myidbot

    Solo quel chat-id puo' dare comandi: qualunque altro viene ignorato e
    loggato. Senza questo controllo, chiunque conoscesse il nome del bot
    potrebbe fermare l'automazione.

AVVIO
    python deploy/remote_control.py
    (in autostart lo mette install-autostart.ps1)

Nessuna dipendenza esterna: solo urllib, long-polling sulle API di Telegram.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "logs" / "deploy"
STATUS_FILE = LOG_DIR / "status.json"
CONFIG_FILE = REPO / "deploy" / "telegram_control.yml"

# nome account -> serial dell'emulatore, per lo screenshot
ACCOUNTS = {
    "rb.coach": "emulator-5554",
    "roberto_buonomo_ifbbpro": "emulator-5556",
}
# alias comodi da digitare sul telefono
ALIASES = {
    "rb": "rb.coach",
    "coach": "rb.coach",
    "pers": "roberto_buonomo_ifbbpro",
    "personale": "roberto_buonomo_ifbbpro",
    "roberto": "roberto_buonomo_ifbbpro",
}

POLL_TIMEOUT = 50

# La cartella va creata QUI, non in main(): il FileHandler qui sotto viene
# costruito al momento dell'import e su una macchina appena installata
# logs/deploy/ non esiste ancora -> FileNotFoundError prima ancora di partire.
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Sotto Task Scheduler lo stdout e' rediretto, quindi su Windows Python usa
# cp1252: un solo carattere non ASCII in un log (i messaggi arrivano da
# Telegram e dai log del bot, pieni di emoji) farebbe morire il processo con
# UnicodeEncodeError. Il FileHandler ha gia' encoding esplicito, questo mette
# in sicurezza lo StreamHandler.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "remote_control.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("remote")


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_config() -> tuple[str, str]:
    """Legge token e chat-id. Parser minimale: niente PyYAML come dipendenza."""
    if not CONFIG_FILE.exists():
        sys.exit(
            f"Manca {CONFIG_FILE}.\n"
            "Copia deploy/telegram_control.example.yml, rinominalo in "
            "telegram_control.yml e mettici token e chat-id."
        )
    token = chat_id = ""
    for raw in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip().strip('"').strip("'")
        if k.strip() == "telegram-api-token":
            token = v
        elif k.strip() == "telegram-chat-id":
            chat_id = v
    if not token or not chat_id or token.startswith("your-"):
        sys.exit(f"token o chat-id non configurati in {CONFIG_FILE}")
    return token, chat_id


# --------------------------------------------------------------------------- #
# telegram
# --------------------------------------------------------------------------- #
class Bot:
    def __init__(self, token: str, owner_chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.owner = str(owner_chat_id)

    def _call(self, method: str, params: dict, timeout: int = 60):
        url = f"{self.base}/{method}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.URLError as e:
            log.warning("telegram %s: %s", method, e)
            return None
        except Exception as e:  # rete assente, timeout del long-poll, ecc.
            log.debug("telegram %s: %s", method, e)
            return None

    def send(self, text: str) -> None:
        # Telegram taglia a 4096 caratteri
        for chunk in (text[i:i + 3900] for i in range(0, len(text), 3900)):
            self._call("sendMessage", {
                "chat_id": self.owner, "text": chunk, "parse_mode": "HTML",
            })

    def send_photo(self, path: Path, caption: str = "") -> bool:
        """Upload multipart a mano: evita la dipendenza da requests."""
        try:
            data = path.read_bytes()
        except OSError as e:
            log.warning("screenshot illeggibile: %s", e)
            return False

        boundary = "----ig-bot-remote-boundary"
        parts = []
        for name, value in (("chat_id", self.owner), ("caption", caption)):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{name}"\r\n\r\n{value}\r\n'.encode()
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="photo"; filename="{path.name}"\r\n'
            f"Content-Type: image/png\r\n\r\n".encode()
        )
        body = b"".join(parts) + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{self.base}/sendPhoto", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read()).get("ok", False)
        except Exception as e:
            log.warning("invio screenshot fallito: %s", e)
            return False

    def updates(self, offset: int):
        res = self._call(
            "getUpdates",
            {"offset": offset, "timeout": POLL_TIMEOUT},
            timeout=POLL_TIMEOUT + 15,
        )
        return (res or {}).get("result", []) if (res or {}).get("ok") else []


# --------------------------------------------------------------------------- #
# comandi
# --------------------------------------------------------------------------- #
def resolve_account(name: str) -> Optional[str]:
    name = (name or "").strip().lower()
    if name in ACCOUNTS:
        return name
    return ALIASES.get(name)


def human_delta(iso: str) -> str:
    if not iso:
        return "mai"
    try:
        from datetime import datetime
        t = datetime.fromisoformat(iso)
        secs = int((datetime.now(t.tzinfo) - t).total_seconds())
    except Exception:
        return iso
    if secs < 60:
        return f"{secs}s fa"
    if secs < 3600:
        return f"{secs // 60}min fa"
    if secs < 86400:
        return f"{secs // 3600}h {(secs % 3600) // 60}min fa"
    return f"{secs // 86400}g fa"


def cmd_status() -> str:
    if not STATUS_FILE.exists():
        return "Nessuno stato: il watchdog non e' mai partito."
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return f"status.json illeggibile: {e}"

    lines = [f"<b>Stato bot</b>  (agg. {human_delta(data.get('updated', ''))})", ""]
    for name, st in (data.get("accounts") or {}).items():
        paused = (LOG_DIR / f"{name}.stop").exists()
        if paused:
            icon, state = "⏸", "in pausa"
        elif st.get("running"):
            icon, state = "▶", f"in esecuzione da {human_delta(st.get('last_start', ''))}"
        else:
            icon, state = "⏹", "fermo"
        lines.append(f"{icon} <b>{name}</b>: {state}")
        if st.get("failures"):
            lines.append(f"    errori consecutivi: {st['failures']}")
        if st.get("last_code") is not None and not st.get("running"):
            lines.append(f"    ultima uscita: codice {st['last_code']} "
                         f"({human_delta(st.get('last_exit', ''))})")
    return "\n".join(lines)


def cmd_stop(account: str) -> str:
    (LOG_DIR / f"{account}.stop").write_text("stop", encoding="utf-8")
    return (f"⏸ <b>{account}</b> messo in pausa.\n"
            "Il watchdog lo fermera' entro ~30s e non lo rilancera'.\n"
            f"Per riprendere: /start {account}")


def cmd_start(account: str) -> str:
    f = LOG_DIR / f"{account}.stop"
    if f.exists():
        f.unlink()
        return f"▶ <b>{account}</b> riattivato: riparte entro ~30s."
    return f"<b>{account}</b> non era in pausa."


def cmd_log(account: str, n: int = 25) -> str:
    # prima il log della sessione vera, poi quello di deploy
    candidates = [
        REPO / "logs" / f"{account}.log",
        LOG_DIR / f"{account}.deploy.log",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if not path:
        return f"Nessun log per {account}."
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"Log illeggibile: {e}"
    tail = [l for l in lines if l.strip()][-n:]
    body = "\n".join(l[:300] for l in tail)
    return f"<b>{path.name}</b> (ultime {len(tail)})\n<pre>{_escape(body)}</pre>"


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cmd_shot(bot: Bot, account: str) -> Optional[str]:
    """Screenshot dell'emulatore: il modo piu' rapido per capire se e' bloccato."""
    serial = ACCOUNTS[account]
    out = LOG_DIR / f"shot_{account}.png"
    try:
        with open(out, "wb") as fh:
            p = subprocess.run(
                ["adb", "-s", serial, "exec-out", "screencap", "-p"],
                stdout=fh, stderr=subprocess.PIPE, timeout=60,
            )
        if p.returncode != 0 or out.stat().st_size == 0:
            return f"Screenshot fallito su {serial}: {p.stderr.decode(errors='replace')[:200]}"
    except FileNotFoundError:
        return "adb non trovato nel PATH."
    except subprocess.TimeoutExpired:
        return f"Timeout su {serial}: l'emulatore potrebbe essere bloccato."
    except Exception as e:
        return f"Screenshot fallito: {e}"

    if bot.send_photo(out, caption=f"{account} ({serial})"):
        return None
    return "Screenshot catturato ma invio fallito."


HELP = """<b>Comandi</b>
/status - stato dei due account
/stop &lt;account&gt; - mette in pausa
/start &lt;account&gt; - riattiva
/log &lt;account&gt; [n] - ultime n righe (default 25)
/shot &lt;account&gt; - screenshot dell'emulatore

Account: <code>rb</code> (rb.coach), <code>pers</code> (roberto_buonomo_ifbbpro)"""


def handle(bot: Bot, text: str) -> Optional[str]:
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("help", "start_help"):
        return HELP
    if cmd == "status":
        return cmd_status()

    if cmd in ("stop", "start", "log", "shot"):
        if not arg:
            return f"Manca l'account. Es: /{cmd} rb"
        account = resolve_account(arg)
        if not account:
            return (f"Account '{arg}' sconosciuto.\n"
                    f"Validi: {', '.join(ACCOUNTS)} (o rb / pers)")
        if cmd == "stop":
            return cmd_stop(account)
        if cmd == "start":
            return cmd_start(account)
        if cmd == "log":
            n = 25
            if len(parts) > 2 and parts[2].isdigit():
                n = max(1, min(int(parts[2]), 100))
            return cmd_log(account, n)
        if cmd == "shot":
            return cmd_shot(bot, account)

    return None  # comando non riconosciuto: si ignora in silenzio


def main() -> int:
    token, owner = load_config()
    bot = Bot(token, owner)
    log.info("controllo remoto avviato (owner chat_id=%s)", owner)
    bot.send("\U0001f7e2 Controllo remoto attivo. /help per i comandi.")

    offset = 0
    while True:
        try:
            for upd in bot.updates(offset):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                text = msg.get("text") or ""
                sender = str((msg.get("chat") or {}).get("id", ""))
                if not text:
                    continue
                if sender != bot.owner:
                    # unico punto di autorizzazione: senza, chiunque trovi il
                    # bot potrebbe fermare l'automazione
                    log.warning("comando ignorato da chat_id non autorizzato %s: %r",
                                sender, text[:60])
                    continue
                log.info("comando: %s", text[:80])
                reply = handle(bot, text)
                if reply:
                    bot.send(reply)
        except KeyboardInterrupt:
            log.info("interrotto da tastiera")
            return 0
        except Exception as e:
            # il polling non deve morire mai: la rete puo' cadere
            log.exception("errore nel loop: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
