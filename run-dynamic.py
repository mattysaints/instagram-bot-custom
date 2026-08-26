#!/usr/bin/env python3
"""
Wrapper per GramAddict: genera working-hours dinamiche basate sull'ora di lancio.

Schema (modalita' normale):
  - La PRIMA sessione parte SUBITO, all'ora del lancio: si avvia il bot e
    lavora, senza aspettare una finestra decisa a tavolino.
  - Le altre N-1 (default 4 in tutto) seguono a distanza di gap-h (3.75h)
    con jitter, tutte dentro la fascia consentita 08:00-23:00. Quattro
    sessioni distanziate, non cinque attaccate: le pause tra le finestre
    salgono a ~2-2.5h, piu' vicine al profilo prudente 2025-26 (sessioni
    60-90 min con pause lunghe) senza rinunciare ai cap giornalieri.
  - Fuori fascia il lancio non lavora di notte: prima delle 08:00 la prima
    sessione slitta all'apertura, dopo le 23:00 a domattina.
  - Le working-hours cosi' calcolate vengono scritte nel config. Con
    total-sessions: -1 e repeat, GramAddict poi le ripete ogni giorno da
    solo: si rilancia solo quando si vuole spostare gli orari.

Esempio: lancio alle 10:20 -> 10.20-11.50, ~13.25-14.55, ~16.30-18.00,
  ~19.35-21.05, ~21.50-23.00 (l'ultima accorciata per non sforare le 23:00).

Modalita' FINESTRE FISSE: se il config contiene il marcatore
  '# finestre-fisse' (lo mettono i config alternati generati da
  tools/make-alternato-config.py) o si passa --fixed-hours, le working-hours
  NON vengono toccate: valgono quelle scritte nel file.

Uso:
  python run-dynamic.py [--config <path>] [--sessions N] [--duration-min M] [--gap-h H]
  python run-dynamic.py --config accounts/rb.coach/config-alternato.yml --fixed-hours --avd rbcoach
"""
import argparse
import atexit
import datetime as dt
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import GramAddict  # noqa: F401  # bootstrap runtime env for IDE launches

# Lo script stampa emoji e box-drawing: su una console Windows in cp1252 (Git
# Bash, cmd senza PYTHONIOENCODING) la print esploderebbe con
# UnicodeEncodeError prima ancora di lanciare il bot.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

DEFAULT_CONFIG = "accounts/rb.coach/config.yml"

# Quanto aspettiamo (al massimo) che il device ADB indicato in config diventi
# 'device' e abbia completato il boot. Se l'emulatore parte freddo serve
# tipicamente 30-90s; mettiamo un cap generoso. Se non si presenta entro il
# timeout, abortiamo prima di lanciare il bot per evitare l'errore
# "Connected devices via adb: 0. Cannot proceed".
ADB_WAIT_TIMEOUT_S = 180
ADB_POLL_INTERVAL_S = 2


def _adb_exe() -> str:
    """Percorso dell'eseguibile adb: prima il PATH, poi l'SDK.

    Serve perche' l'SDK installato da Android Studio su Windows NON finisce nel
    PATH. Chiamando `adb` e basta, il pre-flight qui sotto trova sempre
    'missing', il lancio esce con codice 2 e il bot non parte mai: sotto il
    watchdog il sintomo e' un riavvio dopo l'altro con backoff crescente, senza
    che niente dica che il problema e' un eseguibile non trovato.
    """
    trovato = shutil.which("adb")
    if trovato:
        return trovato

    nome = "adb.exe" if os.name == "nt" else "adb"
    radici = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),
        os.path.expanduser("~/Android/Sdk"),
        os.path.expanduser("~/Library/Android/sdk"),
    ]
    for radice in radici:
        if not radice:
            continue
        candidato = os.path.join(radice, "platform-tools", nome)
        if os.path.isfile(candidato):
            return candidato

    # Nessun fallback silenzioso: restituiamo il nome nudo cosi' l'errore che
    # arriva parla di adb, invece di un generico device 'missing'.
    return "adb"


def _emulator_exe() -> Optional[str]:
    """L'eseguibile dell'emulatore, cercato accanto ad adb (stesso SDK)."""
    adb = _adb_exe()
    if not os.path.isabs(adb):
        return None
    nome = "emulator.exe" if os.name == "nt" else "emulator"
    candidato = os.path.join(os.path.dirname(os.path.dirname(adb)), "emulator", nome)
    return candidato if os.path.isfile(candidato) else None


def _start_emulator(avd: str, serial: str) -> None:
    """Avvia l'AVD legandolo al serial del config, se non e' gia' online.

    Serve al lancio da PyCharm: senza, bisognava ricordarsi di accendere
    l'emulatore giusto a mano prima di premere Run. Core, risoluzione e
    densita' NON si passano qui: stanno nel config.ini dell'AVD (2 core,
    720x1280), cosi' sono gli stessi da qualunque punto lo si avvii.

    -port lega il serial a QUESTO emulatore: senza, il serial lo decide
    l'ordine di avvio, e il bot di un account finirebbe a lavorare
    sull'Instagram dell'altro.
    """
    emu = _emulator_exe()
    if not emu:
        print("⚠️  Non trovo emulator.exe nell'SDK: avvia l'emulatore a mano.")
        return
    porta = serial.replace("emulator-", "")
    cmd = [emu, "-avd", avd, "-port", porta,
           "-no-snapshot-load", "-no-boot-anim", "-no-audio",
           "-gpu", "swiftshader_indirect"]
    print(f"🟢 {serial} non e' online: avvio l'AVD '{avd}' ({' '.join(cmd[1:])})")
    flags = {}
    if os.name == "nt":
        # processo indipendente: deve sopravvivere alla chiusura di questo script
        flags["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)

# Fascia oraria consentita, decisa con il cliente: nessuna sessione prima
# delle 08:00 ne' dopo le 23:00. Le sessioni non sconfinano piu' nella notte:
# l'attivita' notturna e' quella che Instagram guarda con piu' sospetto, e
# comunque il pubblico di questi account di notte non c'e'.
EARLIEST_START = dt.time(8, 0)
LATEST_END = dt.time(23, 0)

# Pausa minima fra la FINE di una sessione e l'INIZIO della successiva. Senza,
# quando il tempo residuo e' poco il calcolo stipa le sessioni una addosso
# all'altra (lancio delle 18:45: quattro sessioni da 50 min con 3 minuti di
# stacco) e il risultato e' un'attivita' continua per ore, cioe' l'opposto di
# quello che fa una persona. Meglio meno sessioni, ma distanziate.
PAUSA_MINIMA_MIN = 30


def gap_minimo_h(duration_min: int) -> float:
    """Distanza minima inizio-inizio: durata della sessione piu' la pausa."""
    return (duration_min + PAUSA_MINIMA_MIN) / 60.0


def fmt(t: dt.time) -> str:
    """Formato GramAddict: HH.MM"""
    return f"{t.hour:02d}.{t.minute:02d}"


def _is_within_allowed(window_dt: dt.datetime, start_day: dt.date) -> bool:
    """True se l'inizio della finestra e' in fascia consentita: stesso giorno
    del lancio e non oltre LATEST_END. Niente wraparound sulla notte."""
    if (window_dt.date() - start_day).days != 0:
        return False
    return EARLIEST_START <= window_dt.time() < LATEST_END


def build_windows(
    start_dt: dt.datetime,
    n_sessions: int,
    duration_min: int,
    gap_h: float,
) -> list[str]:
    """Genera fino a n_sessions finestre a partire dall'ora di lancio.

    Vincoli:
      - inizio sessione in fascia consentita (vedi _is_within_allowed)
      - FINE sessione non oltre LATEST_END dello stesso giorno (altrimenti
        scarta la sessione: meglio averne meno che notturne)
    """
    windows: list[str] = []
    cur = start_dt
    start_day = start_dt.date()
    hard_end = dt.datetime.combine(start_day, LATEST_END)

    for _ in range(n_sessions):
        if not _is_within_allowed(cur, start_day):
            break
        end = cur + dt.timedelta(minutes=duration_min)
        if end > hard_end:
            # la sessione finirebbe oltre il limite umano -> stop
            break
        windows.append(f"{fmt(cur.time())}-{fmt(end.time())}")

        # jitter limitato in 2 direzioni:
        #   - negativo: mai ridurre il gap effettivo sotto la durata (no overlap)
        #   - positivo: mai sforare hard_end con la sessione successiva
        gap_min = int(round(gap_h * 60))
        next_start = cur + dt.timedelta(hours=gap_h)
        next_end = next_start + dt.timedelta(minutes=duration_min)
        slack_min = int((hard_end - next_end).total_seconds() // 60)  # min residui prima di sforare
        # Jitter largo (fino a 35 min): con +-15 le finestre cadevano ogni
        # giorno quasi alla stessa ora, e la regolarita' del ritmo e' il
        # primo segnale che i sistemi anti-bot 2025-26 cercano. Il vincolo
        # PAUSA_MINIMA_MIN resta rispettato dal bound su max_neg_jitter.
        max_pos_jitter = max(0, min(35, slack_min))
        max_neg_jitter = max(0, min(35, gap_min - duration_min - PAUSA_MINIMA_MIN))
        if max_pos_jitter == 0 and max_neg_jitter == 0:
            jitter = 0
        else:
            jitter = random.randint(-max_neg_jitter, max_pos_jitter)
        cur = cur + dt.timedelta(hours=gap_h, minutes=jitter)

    return windows


def shrink_to_fit(
    start_dt: dt.datetime,
    n_sessions: int,
    duration_min: int,
) -> float:
    """Calcola il gap_h (>= duration) per far stare n_sessions tra start_dt e
    LATEST_END dello stesso giorno SENZA sovrapporsi.

    Vincoli:
      - (N-1)*G + D <= minutes_available  (l'ultima sessione finisce entro il limite)
      - G >= D                              (no overlap tra sessioni consecutive)

    Restituisce 0 se impossibile.
    """
    end_of_window = dt.datetime.combine(start_dt.date(), LATEST_END)
    minutes_available = (end_of_window - start_dt).total_seconds() / 60
    if n_sessions <= 1:
        return 0.0
    max_gap_min = (minutes_available - duration_min) / (n_sessions - 1)
    # vincolo no-overlap: gap >= duration
    if max_gap_min < duration_min:
        return 0.0
    return max(0.0, max_gap_min / 60.0)


def patch_working_hours(config_path: Path, windows: list[str]) -> None:
    """Sostituisce la riga 'working-hours: [...]' nel file YAML."""
    text = config_path.read_text()
    new_line = f"working-hours: [{', '.join(windows)}]   # generata dinamicamente da run-dynamic.py"
    pattern = re.compile(r"^\s*#?\s*working-hours\s*:.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(new_line, text, count=1)
    else:
        text = text.rstrip() + "\n" + new_line + "\n"
    config_path.write_text(text)


def _read_device_from_config(config_path: Path) -> Optional[str]:
    """Estrae il valore della chiave 'device:' dal config YAML (parsing
    minimale: niente PyYAML dependency). Ritorna None se non trovato.
    Necessario per sapere a quale serial fare wait-for-device."""
    try:
        text = config_path.read_text()
    except Exception:
        return None
    m = re.search(r"^\s*device\s*:\s*([^\s#]+)", text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _leggi_chiave_config(config_path: Path, chiave: str) -> Optional[str]:
    """Legge una chiave scalare dal config, stesso parsing minimale di
    _read_device_from_config (niente dipendenza da PyYAML qui dentro)."""
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(rf"^\s*{re.escape(chiave)}\s*:\s*([^\s#]+)", text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _e_vero(valore: Optional[str]) -> bool:
    return (valore or "").strip().lower() in ("true", "yes", "si", "1", "on")


def _giornata_finita(config_path: Path) -> Optional[str]:
    """Motivo per cui aprire un'altra sessione oggi non produrrebbe nulla,
    oppure None se invece ha senso partire.

    Il budget like giornaliero e' la risorsa che si esaurisce per prima. Con
    `follow-only-if-engaged` un follow richiede un like che l'ha preceduto, e
    con `end-if-likes-limit-reached` GramAddict chiude la sessione appena quel
    budget finisce: in quelle condizioni la sessione successiva accende
    l'emulatore, fa il login, controlla i limiti e chiude. Il 24/08 sono state
    tre di fila cosi', ~13 minuti di CPU per zero azioni, e su Instagram
    restano tre aperture dell'app senza nessun comportamento umano dietro.

    Il file daily_budget.json e' scritto da GramAddict accanto al config, e
    porta la data: quando cambia giorno il confronto fallisce da solo e si
    riparte, senza bisogno di azzerare niente a mano.
    """
    # Config a ciclo infinito (total-sessions: -1): il processo NON esce a fine
    # giornata, dorme fino alla finestra successiva e domani trova il contatore
    # azzerato da solo. Saltarne il lancio significherebbe non avere nessun bot
    # vivo per il giorno dopo - che senza watchdog installato vuol dire nessun
    # bot e basta. Il cancello serve al modello opposto: lanci brevi che
    # finiscono e vengono rilanciati da fuori.
    sessioni = _leggi_chiave_config(config_path, "total-sessions")
    try:
        if sessioni is not None and int(sessioni) < 0:
            return None
    except ValueError:
        pass

    cap_raw = _leggi_chiave_config(config_path, "daily-likes-cap")
    if not cap_raw:
        return None
    try:
        cap = int(cap_raw)
    except ValueError:
        return None
    if cap <= 0:
        return None

    # Senza almeno uno dei due interruttori la sessione qualcosa puo' ancora
    # farlo (commenti, unfollow), quindi non tocca a noi deciderlo.
    if not (_e_vero(_leggi_chiave_config(config_path, "follow-only-if-engaged"))
            or _e_vero(_leggi_chiave_config(config_path, "end-if-likes-limit-reached"))):
        return None

    budget_file = config_path.parent / "daily_budget.json"
    try:
        dati = json.loads(budget_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    if dati.get("date") != dt.date.today().isoformat():
        return None

    try:
        usati = int(dati.get("likes", 0))
    except (TypeError, ValueError):
        return None
    if usati < cap:
        return None

    return (f"budget like del giorno esaurito ({usati}/{cap}) e questo config "
            f"non permette azioni senza like")


def _adb_device_state(serial: Optional[str]) -> str:
    """Ritorna lo stato del device come riportato da `adb devices`:
      - 'device'   : pronto
      - 'offline'  : in boot / non risponde
      - 'unauthorized' : USB-debug non autorizzato
      - 'missing'  : non presente nell'output
    Se serial e' None, prende il primo non-header.
    """
    try:
        out = subprocess.run(
            [_adb_exe(), "devices"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return "missing"
    for line in out.splitlines()[1:]:  # skip "List of devices attached"
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        s, state = parts[0], parts[1]
        if serial is None or s == serial:
            return state
    return "missing"


def _adb_boot_completed(serial: str) -> bool:
    """Conferma che il device abbia finito il boot (sys.boot_completed=1).
    Senza questo check rischiamo di lanciare il bot mentre l'home screen
    non e' ancora pronta -> uiautomator2 fallisce."""
    try:
        out = subprocess.run(
            [_adb_exe(), "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return out == "1"
    except Exception:
        return False


def wait_for_adb_device(serial: Optional[str], timeout_s: int = ADB_WAIT_TIMEOUT_S) -> bool:
    """Polla `adb devices` finche' il serial non risulta 'device' E ha
    completato il boot. Ritorna True se pronto, False su timeout / unauth.

    Se serial e' None, accetta qualsiasi device pronto.
    """
    target = serial or "<any>"
    deadline = time.monotonic() + timeout_s
    last_state = ""
    announced_wait = False
    while time.monotonic() < deadline:
        state = _adb_device_state(serial)
        if state != last_state:
            print(f"⏳ ADB device '{target}' state: {state}")
            last_state = state
        if state == "device":
            # serial reale (anche se l'utente non l'ha specificato)
            real_serial = serial or _first_ready_serial()
            if real_serial and _adb_boot_completed(real_serial):
                print(f"✅ ADB device '{real_serial}' pronto (boot completato).")
                return True
            if not announced_wait:
                print("⏳ Device 'device' ma boot non ancora completato, aspetto...")
                announced_wait = True
        elif state == "unauthorized":
            print("❌ Device 'unauthorized': autorizza il debug USB sul telefono e riprova.")
            return False
        time.sleep(ADB_POLL_INTERVAL_S)
    print(f"❌ Timeout {timeout_s}s: ADB device '{target}' non e' pronto. "
          f"Verifica con `adb devices` e avvia l'emulatore prima di rilanciare.")
    return False


def _first_ready_serial() -> Optional[str]:
    try:
        out = subprocess.run(
            [_adb_exe(), "devices"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return None
    for line in out.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


# --- un solo bot per device ------------------------------------------------
# Due istanze sullo stesso emulatore si contendono uiautomator e Instagram, e
# sono il modo piu' rapido per farsi bloccare l'account. E' successo tre volte
# il 23/08, sempre nello stesso modo: i bot gia' attivi (watchdog o
# start-account) piu' un lancio dalle configurazioni di PyCharm. Il lucchetto
# qui sotto lo rende impossibile: chi arriva secondo si ferma e lo dice.
#
# E' un file con dentro il PID e un battito aggiornato ogni 30 s. Se il
# processo muore male il battito invecchia e dopo LOCK_SCADENZA_S il device
# torna libero: nessun lucchetto da togliere a mano.
LOCK_BATTITO_S = 30
LOCK_SCADENZA_S = 90


def _percorso_lock(serial: str) -> Path:
    return Path("logs") / "deploy" / f"device_{serial}.lock"


def _lock_attivo(serial: str):
    """Dati del lucchetto se un altro bot sta usando questo device, None se
    e' libero (file assente, battito vecchio, o lucchetto nostro)."""
    percorso = _percorso_lock(serial)
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except Exception:
        return None
    if dati.get("pid") == os.getpid():
        return None
    try:
        eta = time.time() - float(dati.get("battito", 0))
    except (TypeError, ValueError):
        return None
    return dati if eta < LOCK_SCADENZA_S else None


def _scrivi_lock(serial: str, config_path: Path) -> None:
    percorso = _percorso_lock(serial)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "config": str(config_path),
                "battito": time.time(),
            }
        ),
        encoding="utf-8",
    )


def _avvia_battito(serial: str, config_path: Path) -> None:
    """Tiene fresco il lucchetto finche' questo processo e' vivo."""

    def battere():
        while True:
            time.sleep(LOCK_BATTITO_S)
            try:
                _scrivi_lock(serial, config_path)
            except Exception:
                pass

    t = threading.Thread(target=battere, daemon=True)
    t.start()


def prendi_device(serial: Optional[str], config_path: Path, forza: bool = False) -> None:
    """Prende il device per questo processo, o si ferma se e' gia' occupato."""
    if not serial:
        return
    altro = _lock_attivo(serial)
    if altro is not None and not forza:
        messaggio = [
            f"❌ Su {serial} sta gia' girando un bot "
            f"(pid {altro.get('pid')}, config {altro.get('config')}).",
            "   Due istanze sullo stesso emulatore si pestano i piedi: questa non parte.",
            "   Ferma l'altra (oppure aspetta che finisca) e rilancia;",
            "   con --forza-device si insiste comunque, a tuo rischio.",
        ]
        print("\n".join(messaggio), file=sys.stderr)
        sys.exit(3)
    if altro is not None:
        print(f"⚠️  --forza-device: parto anche se {serial} risulta occupato dal pid {altro.get('pid')}.")
    _scrivi_lock(serial, config_path)
    _avvia_battito(serial, config_path)
    atexit.register(_libera_device, serial)


def _libera_device(serial: str) -> None:
    try:
        percorso = _percorso_lock(serial)
        dati = json.loads(percorso.read_text(encoding="utf-8"))
        if dati.get("pid") == os.getpid():
            percorso.unlink(missing_ok=True)
    except Exception:
        pass


def generate_and_patch_windows(args, config_path: Path) -> list[str]:
    """Finestre DINAMICHE a partire dall'ora di lancio (modalita' storica:
    N sessioni da adesso, poi il processo termina e qualcuno lo rilancia).
    Riscrive la riga working-hours del config e restituisce le finestre."""
    now = dt.datetime.now()

    # Lanciato di notte o di primissima mattina: la prima sessione si sposta
    # all'apertura della fascia, non parte alle 4 del mattino.
    if now.time() < EARLIEST_START:
        now = now.replace(hour=EARLIEST_START.hour, minute=random.randint(0, 30), second=0, microsecond=0)
        print(f"ℹ️  Prima delle {EARLIEST_START.strftime('%H:%M')}: prima sessione spostata alle {now.strftime('%H:%M')}.")

    # Lanciato a fascia gia' chiusa (dopo le 23:00) o con cosi' poco tempo
    # residuo che non ci sta nemmeno una sessione minima: oggi non si lavora
    # piu', si riparte domattina. Il bot resta acceso e dorme fino ad allora:
    # meglio che uscire, perche' chi lo ha avviato si aspetta che vada avanti
    # da solo.
    minuti_residui = (
        dt.datetime.combine(now.date(), LATEST_END) - now
    ).total_seconds() / 60
    if now.time() < LATEST_END and minuti_residui < args.min_duration_min:
        print(
            f"ℹ️  Restano {int(minuti_residui)} min prima delle "
            f"{LATEST_END.strftime('%H:%M')}: troppo pochi per una sessione, "
            "si riparte domattina."
        )
    if now.time() >= LATEST_END or minuti_residui < args.min_duration_min:
        domani = now.date() + dt.timedelta(days=1)
        now = dt.datetime.combine(domani, EARLIEST_START) + dt.timedelta(minutes=random.randint(0, 30))
        print(f"ℹ️  Prima sessione domani alle {now.strftime('%H:%M')}.")

    # auto-shrink: tenta in ordine
    #   1) gap_h originale, N sessioni
    #   2) gap_h compresso, N sessioni di durata originale
    #   3) gap+durata compressi, N sessioni (priorita': mantieni N=5)
    #   4) riduci N progressivamente (N-1, N-2, ...)
    #   5) ultima spiaggia: 1 sessione di durata ridotta
    duration_min = args.duration_min
    gap_h = args.gap_h
    windows: list[str] = []

    # tentativo 1: parametri originali
    windows = build_windows(now, args.sessions, duration_min, gap_h)

    # tentativo 2: comprimi il gap mantenendo N e durata
    if len(windows) < args.sessions:
        new_gap = shrink_to_fit(now, args.sessions, duration_min)
        if new_gap >= gap_minimo_h(duration_min):
            print(f"ℹ️  Gap originale ({gap_h:.1f}h) troppo ampio entro le {LATEST_END.strftime('%H:%M')}. Comprimo a ~{new_gap:.2f}h.")
            gap_h = new_gap
            windows = build_windows(now, args.sessions, duration_min, gap_h)

    # tentativo 3: comprimi anche la durata mantenendo N (priorita': N=5 sempre)
    # vincolo: tempo totale = (N-1)*G + D <= window; G >= D (no overlap)
    # => G_min = D, quindi (N-1)*D + D = N*D <= window => D <= window/N
    if len(windows) < args.sessions:
        end_of_window = dt.datetime.combine(now.date(), LATEST_END)
        minutes_available = (end_of_window - now).total_seconds() / 60
        # margine -2min per assorbire jitter accumulato e arrotondamenti
        max_duration_for_n = int(
            (minutes_available - 2 - PAUSA_MINIMA_MIN * (args.sessions - 1)) // args.sessions
        )
        if max_duration_for_n >= args.min_duration_min:
            new_duration = min(duration_min, max_duration_for_n)
            new_gap = shrink_to_fit(now, args.sessions, new_duration)
            if new_gap >= gap_minimo_h(new_duration):
                candidate = build_windows(now, args.sessions, new_duration, new_gap)
                if len(candidate) > len(windows):
                    print(
                        f"ℹ️  Comprimo durata {duration_min}min -> {new_duration}min "
                        f"e gap a ~{new_gap:.2f}h per stipare {len(candidate)} sessioni "
                        f"(target {args.sessions})."
                    )
                    duration_min = new_duration
                    gap_h = new_gap
                    windows = candidate

    # tentativo 4: riduci N progressivamente (solo se anche shrinking durata fallisce)
    if len(windows) < args.sessions:
        n_eff = args.sessions
        while n_eff > 1 and len(windows) < n_eff:
            n_eff -= 1
            new_gap = shrink_to_fit(now, n_eff, args.duration_min)
            if new_gap < gap_minimo_h(args.duration_min):
                continue
            candidate = build_windows(now, n_eff, args.duration_min, new_gap)
            if len(candidate) >= n_eff:
                print(f"ℹ️  Riduco da {args.sessions} a {n_eff} sessioni di {args.duration_min}min (gap ~{new_gap:.2f}h, fine entro le {LATEST_END.strftime('%H:%M')}).")
                windows = candidate
                gap_h = new_gap
                duration_min = args.duration_min
                break

    # tentativo 5: ultima spiaggia, 1 sessione di durata ridotta
    if not windows:
        end_of_window = dt.datetime.combine(now.date(), LATEST_END)
        minutes_left = int((end_of_window - now).total_seconds() / 60)
        if minutes_left >= args.min_duration_min:
            shrunk = min(args.duration_min, minutes_left)
            print(f"ℹ️  Solo {minutes_left}min disponibili: genero 1 sessione di {shrunk}min.")
            windows = build_windows(now, 1, shrunk, gap_h)
            duration_min = shrunk
        else:
            print(f"⚠️  Restano solo {minutes_left}min entro le {LATEST_END.strftime('%H:%M')} "
                  f"(min richiesto: {args.min_duration_min}min). Niente da fare ora.")

    if not windows:
        print("⚠️  Nessuna finestra generata. Rilancia piu' tardi.")
        sys.exit(0)

    print("┌─────────────────────────────────────────────────")
    print(f"│ Working-hours generate ({len(windows)}/{args.sessions} sessioni di {duration_min}min, gap ~{gap_h:.2f}h):")
    for i, w in enumerate(windows, 1):
        print(f"│   {i}. {w}")
    print("└─────────────────────────────────────────────────")

    patch_working_hours(config_path, windows)
    print(f"✅ Config aggiornata: {config_path}")
    return windows


def _read_working_hours(config_path: Path) -> list[str]:
    """Legge la riga 'working-hours: [a.b-c.d, ...]' del config (parsing
    minimale come _read_device_from_config). Lista vuota se assente."""
    try:
        # errors="replace": un commento con una lettera accentata salvata in
        # cp1252 non deve far perdere la riga (e far scattare di nascosto la
        # modalita' dinamica, che riscriverebbe le finestre fisse)
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    m = re.search(r"^\s*working-hours\s*:\s*\[([^\]]*)\]", text, re.MULTILINE)
    if not m:
        return []
    finestre = [w.strip() for w in m.group(1).split(",") if w.strip()]
    return [w for w in finestre if re.fullmatch(r"\d{1,2}\.\d{2}-\d{1,2}\.\d{2}", w)]


def _finestre_fisse_richieste(config_path: Path) -> bool:
    """True se il config chiede di NON rigenerare le working-hours, cioe' se
    contiene il marcatore '# finestre-fisse'. Lo mettono i config alternati
    (tools/make-alternato-config.py), che hanno orari decisi a tavolino.
    Gli altri config vogliono finestre calcolate dall'ora di lancio: si
    avvia il bot e la prima sessione parte subito."""
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return re.search(r"(?m)^\s*#\s*finestre-fisse(?![\w-])", text) is not None


def _total_sessions_from_config(config_path: Path) -> Optional[int]:
    """Valore di 'total-sessions' nel config, None se assente o non numerico."""
    try:
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r"^\s*total-sessions\s*:\s*(-?\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="Path al config.yml")
    ap.add_argument("--sessions", type=int, default=4, help="Numero massimo di sessioni nella giornata")
    ap.add_argument("--duration-min", type=int, default=90, help="Durata di ogni sessione (minuti)")
    ap.add_argument("--gap-h", type=float, default=3.75, help="Distanza inizio-inizio tra sessioni (ore)")
    ap.add_argument("--dry-run", action="store_true", help="Calcola e stampa senza lanciare il bot")
    ap.add_argument(
        "--forza-device",
        action="store_true",
        help="Parte anche se sul device risulta gia' attivo un altro bot. "
             "Da usare solo se sai che il lucchetto e' rimasto orfano.",
    )
    ap.add_argument(
        "--fixed-hours",
        action="store_true",
        help="Non generare finestre: usa le working-hours gia' scritte nel config "
             "(loop giornaliero con total-sessions: -1). Attivo da solo se il config ha total-sessions: -1.",
    )
    ap.add_argument(
        "--ignora-budget",
        action="store_true",
        help="Parte anche se il budget like della giornata e' gia' esaurito. "
             "Senza, la sessione viene saltata perche' non potrebbe fare nulla.",
    )
    ap.add_argument(
        "--min-duration-min",
        type=int,
        default=30,
        help="Durata minima accettabile per una sessione quando si fa auto-shrink (default 30 min).",
    )
    ap.add_argument(
        "--skip-adb-check",
        action="store_true",
        help="Salta il wait-for-device pre-lancio (utile in CI o con device gia' garantito pronto).",
    )
    ap.add_argument(
        "--adb-wait-timeout",
        type=int,
        default=ADB_WAIT_TIMEOUT_S,
        help=f"Timeout (s) per l'attesa del device ADB pronto (default {ADB_WAIT_TIMEOUT_S}s).",
    )
    ap.add_argument(
        "--avd",
        default=None,
        help="Nome dell'AVD da avviare se il device del config non e' online "
             "(es. rbcoach). Senza, il device deve essere gia' acceso.",
    )
    args = ap.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ Config non trovata: {config_path}", file=sys.stderr)
        sys.exit(1)

    if args.fixed_hours or _finestre_fisse_richieste(config_path):
        # Finestre FISSE: il config ha le sue working-hours giornaliere (es.
        # 08-22) e total-sessions: -1, e GramAddict cicla da solo giorno dopo
        # giorno (di notte dorme fino alla prima finestra del mattino). Qui
        # non si riscrive niente: solo pre-flight ADB e lancio. E' la
        # modalita' per girare h24 senza rilanci a mano.
        windows = _read_working_hours(config_path)
        if not windows:
            print(f"❌ Finestre fisse richieste ma in {config_path} non c'e' una riga "
                  "'working-hours: [...]' valida.", file=sys.stderr)
            sys.exit(1)
        if not args.fixed_hours:
            print("ℹ️  Il config chiede finestre fisse (marcatore '# finestre-fisse'): "
                  "working-hours NON riscritte.")
        print("┌─────────────────────────────────────────────────")
        print(f"│ Working-hours fisse dal config ({len(windows)} sessioni al giorno, in loop):")
        for k, w in enumerate(windows, 1):
            print(f"│   {k}. {w}")
        print("└─────────────────────────────────────────────────")
    else:
        windows = generate_and_patch_windows(args, config_path)

    if args.dry_run:
        print("\n(--dry-run: bot non lanciato)")
        return

    # Il budget della giornata e' gia' finito? Meglio saperlo PRIMA di
    # accendere l'emulatore e prendere il lock del device: cosi' la sessione a
    # vuoto non costa niente. Uscita 0, non un errore: il watchdog deve
    # aspettare il suo intervallo normale, non entrare in backoff da crash.
    motivo = _giornata_finita(config_path)
    if motivo and not args.ignora_budget:
        print(f"⏸️  Sessione saltata: {motivo}.")
        print("   Il conteggio si azzera a mezzanotte; con --ignora-budget si parte comunque.")
        return

    # Pre-flight ADB check: aspetta che il device specificato in config sia
    # 'device' e abbia finito il boot. Risolve la race condition per cui il
    # bot partiva mentre l'emulatore era ancora 'offline' e crashava con
    # "Connected devices via adb: 0. Cannot proceed".
    device_serial = _read_device_from_config(config_path)
    prendi_device(device_serial, config_path, forza=args.forza_device)

    if not args.skip_adb_check:
        if args.avd and device_serial and _adb_device_state(device_serial) == "missing":
            _start_emulator(args.avd, device_serial)
        if device_serial:
            print(f"🔌 Verifico che ADB device '{device_serial}' sia pronto...")
        else:
            print("🔌 Nessun 'device:' in config; verifico che almeno un device ADB sia pronto...")
        if not wait_for_adb_device(device_serial, timeout_s=args.adb_wait_timeout):
            print("❌ Abort: device ADB non pronto entro il timeout. "
                  "Avvia l'emulatore (o collega il telefono con USB-debug autorizzato) e riprova.")
            sys.exit(2)

    # Lancia il bot
    cmd = [sys.executable, "run.py", "--config", str(config_path)]
    print(f"\n🚀 Lancio: {' '.join(cmd)}\n")

    # Carica .env.local (IG_COMMENT_SPACE_KEY, ecc.) se presente.
    env = os.environ.copy()
    env_file = Path(".env.local")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            env[k.strip()] = v
        if env.get("IG_COMMENT_SPACE_KEY"):
            print("🔑 IG_COMMENT_SPACE_KEY caricata da .env.local")

    # GramAddict chiama `adb` per nome in decine di punti (utils.py,
    # device_facade.py, views.py): se platform-tools non e' nel PATH, il
    # pre-flight qui sopra passa (usa _adb_exe) ma run.py muore subito dopo
    # con "Connected devices via adb: 0. Cannot proceed." Mettere la cartella
    # in testa al PATH del figlio risolve per tutti, senza toccare il core.
    adb_dir = os.path.dirname(_adb_exe())
    if adb_dir:
        env["PATH"] = adb_dir + os.pathsep + env.get("PATH", "")

    risultato = subprocess.run(cmd, check=False, env=env)
    # L'exit code del bot va propagato: il watchdog distingue l'uscita pulita
    # (riparte dopo 15 min) dal crash (backoff). Ignorarlo faceva passare
    # "Cannot proceed" per una giornata finita regolarmente.
    sys.exit(risultato.returncode)


if __name__ == "__main__":
    main()





