#!/usr/bin/env python3
"""
rotate-bloggers.py - Rotazione automatica della lista `blogger-followers`.

Scopo
-----
La lista nel `config.yml` cresce nel tempo (>90 sorgenti). Con
`truncate-sources: 10-12` solo i PRIMI 10-12 della lista vengono lavorati ogni
sessione: se in testa ci sono sorgenti esaurite o saturate, le sessioni
producono basso yield.

Questo script riordina la lista in base a yield reale (file di stato del bot):

  Score per ogni blogger =
    + recenti_follows_done   (peso forte se < 7gg, decresce dopo)
    - exhausted_penalty      (se exhausted_at recente, lo manda in coda)
    + freshness_bonus        (se mai toccato o toccato >14gg fa)
    - oversaturation_penalty (se > 100 follow gia' fatti, possibile esaurimento
                              imminente)

Output
------
- Riordina la riga `blogger-followers: [...]` mantenendo tutti gli elementi
  (nessuno viene buttato via, solo riordinato).
- Backup automatico del config in `config.yml.bak-YYYYMMDD-HHMMSS` prima di
  scrivere.
- Stampa la classifica con score, follows_done, exhausted_status.

Uso
---
    python tools/rotate-bloggers.py [--config PATH] [--dry-run]

Default: opera su `accounts/marramattia_fmgpro/config.yml`.

Sicurezza
---------
- NON tocca altre chiavi del YAML (parsing line-based con regex su
  `blogger-followers:`).
- Non rimuove username: solo riordina. Se vuoi rimuovere, fallo a mano.
- Idempotente: rilanciarlo con stessi dati produce lo stesso ordinamento.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pesi della funzione di scoring. Affinabili senza toccare la logica.
# ---------------------------------------------------------------------------
W_RECENT_FOLLOW = 10.0       # per ogni follow nei primi 7gg
W_OLD_FOLLOW = 2.0           # per ogni follow tra 7 e 30gg
W_VERY_OLD_FOLLOW = 0.2      # per ogni follow oltre 30gg (quasi 0)
W_FRESH_NEVER_TOUCHED = 30.0 # bonus se sorgente mai usata
W_FRESH_DORMANT = 20.0       # bonus se ultima interazione > 14gg fa
P_EXHAUSTED_RECENT = 200.0   # penalita' se exhausted_at < 7gg fa (manda in fondo)
P_EXHAUSTED_OLD = 50.0       # penalita' se exhausted_at 7-30gg fa
P_OVERSATURATED = 30.0       # penalita' fissa se follows_done > 100 (pool quasi esaurito)
P_HUGE_SATURATION = 80.0     # penalita' aggiuntiva se follows_done > 200


def parse_iso(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts)
    except ValueError:
        return None


def days_since(ts: str | None, now: dt.datetime) -> float | None:
    parsed = parse_iso(ts)
    if not parsed:
        return None
    return (now - parsed).total_seconds() / 86400.0


def compute_score(
    username: str,
    source_stats: dict,
    explored_segments: dict,
    now: dt.datetime,
) -> tuple[float, dict]:
    """Calcola lo score di un blogger. Ritorna (score, breakdown_dict per logging)."""
    key = f"blogger-followers|{username}"
    stat = source_stats.get(key, {})
    seg = explored_segments.get("blogger-followers", {}).get(username, {})

    follows_done = int(stat.get("follows_done", 0) or 0)
    last_follow_days = days_since(stat.get("last_follow_at"), now)
    exhausted_days = days_since(seg.get("exhausted_at"), now)
    last_seen_days = days_since(seg.get("last_seen_at"), now)

    score = 0.0
    breakdown: dict[str, float] = {}

    # 1) ricompensa follows recenti (peso decrescente con eta')
    if follows_done > 0 and last_follow_days is not None:
        if last_follow_days < 7:
            v = follows_done * W_RECENT_FOLLOW
        elif last_follow_days < 30:
            v = follows_done * W_OLD_FOLLOW
        else:
            v = follows_done * W_VERY_OLD_FOLLOW
        score += v
        breakdown["follows_value"] = round(v, 1)

    # 2) bonus freshness
    if last_seen_days is None:
        # mai visto -> e' una sorgente nuova, da provare
        score += W_FRESH_NEVER_TOUCHED
        breakdown["fresh_never"] = W_FRESH_NEVER_TOUCHED
    elif last_seen_days > 14:
        score += W_FRESH_DORMANT
        breakdown["fresh_dormant"] = W_FRESH_DORMANT

    # 3) penalita' esaurita
    if exhausted_days is not None:
        if exhausted_days < 7:
            score -= P_EXHAUSTED_RECENT
            breakdown["exhausted_recent"] = -P_EXHAUSTED_RECENT
        elif exhausted_days < 30:
            score -= P_EXHAUSTED_OLD
            breakdown["exhausted_old"] = -P_EXHAUSTED_OLD
        # > 30gg: pool potrebbe essersi rinnovato, niente penalita'

    # 4) penalita' oversaturazione (gia' battuto molto, pool quasi esaurito)
    if follows_done > 100:
        score -= P_OVERSATURATED
        breakdown["oversaturated"] = -P_OVERSATURATED
    if follows_done > 200:
        score -= P_HUGE_SATURATION
        breakdown["huge_saturation"] = -P_HUGE_SATURATION

    return score, breakdown


def extract_blogger_list(yaml_text: str) -> tuple[str, list[str], int]:
    """Estrae la lista `blogger-followers: [a, b, c]` dal config.

    Ritorna: (linea_originale, lista_username, indice_riga).
    Solleva ValueError se non trovata o se sintassi non e' inline [].
    """
    lines = yaml_text.splitlines()
    pattern = re.compile(r"^\s*blogger-followers\s*:\s*\[(.*)\]\s*(?:#.*)?$")
    for idx, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            inner = m.group(1).strip()
            if not inner:
                return line, [], idx
            usernames = [u.strip() for u in inner.split(",") if u.strip()]
            return line, usernames, idx
    raise ValueError(
        "Riga `blogger-followers: [...]` non trovata. "
        "Verifica che sia in formato inline su una riga sola."
    )


def write_new_list(yaml_text: str, line_idx: int, original_line: str, new_list: list[str]) -> str:
    """Sostituisce la riga blogger-followers preservando indentazione e
    commento di coda (se presente)."""
    # preserva eventuale commento di coda
    trailing_comment = ""
    if "#" in original_line:
        comment_idx = original_line.index("#")
        trailing_comment = "   " + original_line[comment_idx:]
    # preserva indentazione
    indent_match = re.match(r"^(\s*)", original_line)
    indent = indent_match.group(1) if indent_match else ""
    new_line = f"{indent}blogger-followers: [{', '.join(new_list)}]{trailing_comment}"
    lines = yaml_text.splitlines()
    lines[line_idx] = new_line
    return "\n".join(lines) + ("\n" if yaml_text.endswith("\n") else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--config",
        default="accounts/marramattia_fmgpro/config.yml",
        help="Path al config.yml",
    )
    ap.add_argument(
        "--source-stats",
        default=None,
        help="Path a source_stats.json (default: accanto al config)",
    )
    ap.add_argument(
        "--explored",
        default=None,
        help="Path a explored_segments.json (default: accanto al config)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Mostra il nuovo ordine senza scrivere")
    ap.add_argument(
        "--top",
        type=int,
        default=20,
        help="Quanti blogger mostrare in classifica dettagliata (default 20)",
    )
    args = ap.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ Config non trovato: {config_path}", file=sys.stderr)
        return 1
    account_dir = config_path.parent
    source_stats_path = Path(args.source_stats) if args.source_stats else account_dir / "source_stats.json"
    explored_path = Path(args.explored) if args.explored else account_dir / "explored_segments.json"

    yaml_text = config_path.read_text()
    original_line, usernames, line_idx = extract_blogger_list(yaml_text)
    if not usernames:
        print("⚠️  Lista vuota, niente da rotare.")
        return 0

    print(f"📋 Trovati {len(usernames)} blogger nella lista.")

    source_stats: dict = {}
    if source_stats_path.exists():
        try:
            source_stats = json.loads(source_stats_path.read_text()).get("sources", {})
            print(f"✅ source_stats.json letto ({len(source_stats)} entries).")
        except Exception as e:
            print(f"⚠️  source_stats.json non leggibile: {e}")
    else:
        print(f"⚠️  source_stats.json non trovato in {source_stats_path}")

    explored_segments: dict = {}
    if explored_path.exists():
        try:
            explored_segments = json.loads(explored_path.read_text()).get("sources", {})
            n_bf = len(explored_segments.get("blogger-followers", {}))
            print(f"✅ explored_segments.json letto ({n_bf} blogger-followers tracked).")
        except Exception as e:
            print(f"⚠️  explored_segments.json non leggibile: {e}")
    else:
        print(f"⚠️  explored_segments.json non trovato in {explored_path}")

    now = dt.datetime.now()
    scored = []
    for u in usernames:
        score, breakdown = compute_score(u, source_stats, explored_segments, now)
        scored.append((u, score, breakdown))

    # Ordina decrescente per score; tie-breaker: ordine originale (stabile)
    scored.sort(key=lambda x: -x[1])

    print("")
    print(f"┌─ Top {min(args.top, len(scored))} blogger (score decrescente) ─")
    for i, (u, s, br) in enumerate(scored[: args.top], 1):
        key = f"blogger-followers|{u}"
        stat = source_stats.get(key, {})
        seg = explored_segments.get("blogger-followers", {}).get(u, {})
        fd = stat.get("follows_done", 0)
        lf = stat.get("last_follow_at", "—")[:10] if stat.get("last_follow_at") else "—"
        exh = seg.get("exhausted_at")
        exh_str = f"exh={exh[:10]}" if exh else "exh=no"
        print(f"│ {i:3d}. {u:<40s} score={s:7.1f}  follows={fd:4d}  last={lf}  {exh_str}")
    print("└─")

    if len(scored) > args.top:
        print(f"\n┌─ Bottom 10 blogger (peggio classificati) ─")
        for i, (u, s, br) in enumerate(scored[-10:], len(scored) - 9):
            key = f"blogger-followers|{u}"
            stat = source_stats.get(key, {})
            seg = explored_segments.get("blogger-followers", {}).get(u, {})
            fd = stat.get("follows_done", 0)
            lf = stat.get("last_follow_at", "—")[:10] if stat.get("last_follow_at") else "—"
            exh = seg.get("exhausted_at")
            exh_str = f"exh={exh[:10]}" if exh else "exh=no"
            print(f"│ {i:3d}. {u:<40s} score={s:7.1f}  follows={fd:4d}  last={lf}  {exh_str}")
        print("└─")

    new_order = [u for u, _, _ in scored]
    if new_order == usernames:
        print("\n✅ Lista gia' nell'ordine ottimale. Niente da fare.")
        return 0

    # Diff conciso: chi sale e chi scende di posizione
    movements = []
    for new_pos, u in enumerate(new_order):
        old_pos = usernames.index(u)
        delta = old_pos - new_pos
        if abs(delta) >= 3:  # mostra solo movimenti significativi
            movements.append((u, old_pos + 1, new_pos + 1, delta))
    movements.sort(key=lambda x: -abs(x[3]))
    if movements:
        print("\n┌─ Movimenti significativi (>= 3 posizioni) ─")
        for u, old_p, new_p, delta in movements[:15]:
            arrow = "↑" if delta > 0 else "↓"
            print(f"│ {arrow} {u:<40s} {old_p:3d} -> {new_p:3d}  ({delta:+d})")
        print("└─")

    if args.dry_run:
        print("\n(--dry-run: config NON modificato)")
        return 0

    # Backup
    ts = now.strftime("%Y%m%d-%H%M%S")
    backup = config_path.with_suffix(config_path.suffix + f".bak-{ts}")
    shutil.copy2(config_path, backup)
    print(f"\n💾 Backup creato: {backup.name}")

    new_text = write_new_list(yaml_text, line_idx, original_line, new_order)
    config_path.write_text(new_text)
    print(f"✅ {config_path.name} aggiornato con il nuovo ordine.")
    print(f"   I primi 10-12 verranno lavorati nella prossima sessione (truncate-sources).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

