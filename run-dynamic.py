#!/usr/bin/env python3
"""
Wrapper per GramAddict: genera working-hours dinamiche basate sull'ora di lancio.

Schema:
  - SEMPRE N sessioni (default 5), prima parte SUBITO
  - Durata: 90 min ciascuna (default), gap inizio-inizio: 3h con jitter +/-15 min
  - Le sessioni possono sforare oltre la mezzanotte (wraparound 24h): se al
    momento del lancio non ci stanno N sessioni entro le 23:59, le rimanenti
    vengono pianificate dopo le 00:00 (GramAddict aspetta naturalmente fino
    alla finestra successiva grazie al time_in_range con wraparound).
  - HARD LIMIT anti-ban: nessuna sessione prima delle 09:00 ne' dopo le
    EARLIEST_NEXT_DAY del giorno successivo (default: 03:00) -- evita di
    finire le sessioni in piena notte 04-08 quando IG flagga aggressivamente.

Esempio: lancio alle 22:00 -> 5 sessioni:
  22.00-23.30, ~01.10-02.40, ~04.20-05.50  ...  -> bloccate da hard limit
  In quel caso il loop si ferma prima delle 03:00 e il giorno dopo si rilancia.

Uso:
  python run-dynamic.py [--config <path>] [--sessions N] [--duration-min M] [--gap-h H]
"""
import argparse
import datetime as dt
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import GramAddict  # noqa: F401  # bootstrap runtime env for IDE launches

DEFAULT_CONFIG = "accounts/marramattia_fmgpro/config.yml"

# Quanto aspettiamo (al massimo) che il device ADB indicato in config diventi
# 'device' e abbia completato il boot. Se l'emulatore parte freddo serve
# tipicamente 30-90s; mettiamo un cap generoso. Se non si presenta entro il
# timeout, abortiamo prima di lanciare il bot per evitare l'errore
# "Connected devices via adb: 0. Cannot proceed".
ADB_WAIT_TIMEOUT_S = 180
ADB_POLL_INTERVAL_S = 2

# Inizio "umano" anti-ban (mai sessioni notturne 03-07)
EARLIEST_START = dt.time(7, 0)
# Hard limit late-night: oltre questa ora del giorno successivo NON pianifichiamo
# piu' sessioni, anche se il calcolo gap*N le richiederebbe. 03:00 e' il limite
# massimo "umano" per uso reale di IG -- oltre, l'attivita' diventa sospetta.
LATEST_NEXT_DAY = dt.time(3, 0)


def fmt(t: dt.time) -> str:
    """Formato GramAddict: HH.MM"""
    return f"{t.hour:02d}.{t.minute:02d}"


def _is_within_allowed(window_dt: dt.datetime, start_day: dt.date) -> bool:
    """True se l'inizio della finestra e' in fascia consentita.

    Regole:
      - se siamo nello stesso giorno di partenza: tutto ok (siamo gia' >= EARLIEST_START)
      - se siamo nel giorno successivo: deve essere < LATEST_NEXT_DAY (03:00)
      - oltre il giorno successivo: vietato (mai pianificare sessioni a 48h)
    """
    delta_days = (window_dt.date() - start_day).days
    if delta_days == 0:
        return True
    if delta_days == 1:
        return window_dt.time() < LATEST_NEXT_DAY
    return False


def build_windows(
    start_dt: dt.datetime,
    n_sessions: int,
    duration_min: int,
    gap_h: float,
) -> list[str]:
    """Genera fino a n_sessions finestre. Permette wraparound dopo mezzanotte
    fino al hard limit LATEST_NEXT_DAY del giorno successivo.

    Vincoli:
      - inizio sessione in fascia consentita (vedi _is_within_allowed)
      - FINE sessione non oltre LATEST_NEXT_DAY del giorno successivo
        (altrimenti scarta la sessione: meglio averne meno che notturne)
    """
    windows: list[str] = []
    cur = start_dt
    start_day = start_dt.date()
    hard_end = dt.datetime.combine(start_day + dt.timedelta(days=1), LATEST_NEXT_DAY)

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
        max_pos_jitter = max(0, min(15, slack_min))
        max_neg_jitter = max(0, min(15, gap_min - duration_min))
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
    LATEST_NEXT_DAY del giorno successivo SENZA sovrapporsi.

    Vincoli:
      - (N-1)*G + D <= minutes_available  (l'ultima sessione finisce entro il limite)
      - G >= D                              (no overlap tra sessioni consecutive)

    Restituisce 0 se impossibile.
    """
    end_of_window = dt.datetime.combine(start_dt.date() + dt.timedelta(days=1), LATEST_NEXT_DAY)
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
            ["adb", "devices"], capture_output=True, text=True, timeout=10
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
            ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
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
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return None
    for line in out.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="Path al config.yml")
    ap.add_argument("--sessions", type=int, default=5, help="Numero massimo di sessioni nella giornata")
    ap.add_argument("--duration-min", type=int, default=90, help="Durata di ogni sessione (minuti)")
    ap.add_argument("--gap-h", type=float, default=3.0, help="Distanza inizio-inizio tra sessioni (ore)")
    ap.add_argument("--dry-run", action="store_true", help="Calcola e stampa senza lanciare il bot")
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
    args = ap.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ Config non trovata: {config_path}", file=sys.stderr)
        sys.exit(1)

    now = dt.datetime.now()

    # Vincolo: prima di EARLIEST_START -> sposta a EARLIEST_START di oggi (con jitter umano)
    if now.time() < EARLIEST_START:
        now = now.replace(hour=EARLIEST_START.hour, minute=random.randint(0, 30), second=0, microsecond=0)
        print(f"ℹ️  Prima delle {EARLIEST_START.strftime('%H:%M')} -> prima sessione spostata alle {now.strftime('%H:%M')}")

    # Vincolo: tra LATEST_NEXT_DAY (03:00) e EARLIEST_START (07:00) di OGGI ->
    # non possiamo lanciare ne' subito ne' la stessa "notte"; sposta a oggi EARLIEST_START.
    # (Se ti svegli alle 4 e lanci, NON vogliamo sessioni alle 4-7.)
    if LATEST_NEXT_DAY <= now.time() < EARLIEST_START:
        now = now.replace(hour=EARLIEST_START.hour, minute=random.randint(0, 30), second=0, microsecond=0)
        print(f"ℹ️  Sei nella fascia notte ({LATEST_NEXT_DAY.strftime('%H:%M')}-{EARLIEST_START.strftime('%H:%M')}). Prima sessione spostata alle {now.strftime('%H:%M')}.")

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
        if new_gap >= duration_min / 60.0:
            print(f"ℹ️  Gap originale ({gap_h:.1f}h) troppo ampio entro le {LATEST_NEXT_DAY.strftime('%H:%M')} di domani. Comprimo a ~{new_gap:.2f}h.")
            gap_h = new_gap
            windows = build_windows(now, args.sessions, duration_min, gap_h)

    # tentativo 3: comprimi anche la durata mantenendo N (priorita': N=5 sempre)
    # vincolo: tempo totale = (N-1)*G + D <= window; G >= D (no overlap)
    # => G_min = D, quindi (N-1)*D + D = N*D <= window => D <= window/N
    if len(windows) < args.sessions:
        end_of_window = dt.datetime.combine(now.date() + dt.timedelta(days=1), LATEST_NEXT_DAY)
        minutes_available = (end_of_window - now).total_seconds() / 60
        # margine -2min per assorbire jitter accumulato e arrotondamenti
        max_duration_for_n = int((minutes_available - 2) // args.sessions)
        if max_duration_for_n >= args.min_duration_min:
            new_duration = min(duration_min, max_duration_for_n)
            new_gap = shrink_to_fit(now, args.sessions, new_duration)
            if new_gap >= new_duration / 60.0:
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
            if new_gap < args.duration_min / 60.0:
                continue
            candidate = build_windows(now, n_eff, args.duration_min, new_gap)
            if len(candidate) >= n_eff:
                print(f"ℹ️  Riduco da {args.sessions} a {n_eff} sessioni di {args.duration_min}min (gap ~{new_gap:.2f}h, fine entro {LATEST_NEXT_DAY.strftime('%H:%M')} di domani).")
                windows = candidate
                gap_h = new_gap
                duration_min = args.duration_min
                break

    # tentativo 5: ultima spiaggia, 1 sessione di durata ridotta
    if not windows:
        end_of_window = dt.datetime.combine(now.date() + dt.timedelta(days=1), LATEST_NEXT_DAY)
        minutes_left = int((end_of_window - now).total_seconds() / 60)
        if minutes_left >= args.min_duration_min:
            shrunk = min(args.duration_min, minutes_left)
            print(f"ℹ️  Solo {minutes_left}min disponibili: genero 1 sessione di {shrunk}min.")
            windows = build_windows(now, 1, shrunk, gap_h)
            duration_min = shrunk
        else:
            print(f"⚠️  Restano solo {minutes_left}min entro le {LATEST_NEXT_DAY.strftime('%H:%M')} di domani "
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

    if args.dry_run:
        print("\n(--dry-run: bot non lanciato)")
        return

    # Pre-flight ADB check: aspetta che il device specificato in config sia
    # 'device' e abbia finito il boot. Risolve la race condition per cui il
    # bot partiva mentre l'emulatore era ancora 'offline' e crashava con
    # "Connected devices via adb: 0. Cannot proceed".
    if not args.skip_adb_check:
        device_serial = _read_device_from_config(config_path)
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

    # Carica .env.local (GEMINI_API_KEY, ecc.) se presente.
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
        if "GEMINI_API_KEY" in env and env["GEMINI_API_KEY"]:
            print("🔑 GEMINI_API_KEY caricata da .env.local")

    subprocess.run(cmd, check=False, env=env)


if __name__ == "__main__":
    main()





