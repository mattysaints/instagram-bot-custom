#!/usr/bin/env python3
"""
ai_comment_bench.py - Banco di prova per i commenti AI, senza device.

Manda allo Space HF la STESSA richiesta che manda il bot (caption, media_type,
target_username, hint, language) per un set di caption reali prese dai log e
stratificate per contesto (fitness, viaggi, cibo, famiglia, solo emoji, vuote,
troncate da IG, in lingua straniera, casi limite come lutto o nascita), e
applica gli stessi filtri lato client del bot:

  - clean_caption      -> via "… more" e handle del poster incollato davanti
  - caption_is_usable  -> se la caption non ha almeno N parole vere, il bot
                          NON chiama l'AI (il modello non vede la foto)
  - comment_is_acceptable -> scarta frammenti e commenti che nominano l'autore

Cosi' si vede, per ogni caption, cosa avrebbe scritto il bot con un certo
`ai-comments-prompt-hint` PRIMA di metterlo in produzione.

Uso
---
    python tools/ai_comment_bench.py --variant attuale            # hint dal config.yml
    python tools/ai_comment_bench.py --variant nuovo --hint-file tools/hints/nuovo.txt
    python tools/ai_comment_bench.py --variant nuovo --hint-file ... --no-gate  # anche caption vuote

Output: <out>/<variant>.json (tutti i campi) e <out>/<variant>.md (tabella).
La bearer key dello Space viene letta da .env.local (IG_COMMENT_SPACE_KEY).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from GramAddict.core import ai_comment  # noqa: E402  (carica anche .env.local)

DEFAULT_CASES = REPO / "tools" / "ai_comment_bench_cases.json"
DEFAULT_CONFIG = REPO / "accounts" / "marramattia_fmgpro" / "config.yml"


def _read_hint_from_config(config_path: Path) -> str:
    text = config_path.read_text(encoding="utf-8")
    m = re.search(r'^ai-comments-prompt-hint:\s*"(.*)"\s*$', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"ai-comments-prompt-hint non trovato in {config_path}")
    return m.group(1)


def _read_language_from_config(config_path: Path) -> str:
    text = config_path.read_text(encoding="utf-8")
    m = re.search(r"^ai-comments-language:\s*([^\s#]+)", text, re.MULTILINE)
    return m.group(1) if m else "Italian"


def run(
    cases: list[dict],
    hint: str,
    language: str,
    variant: str,
    out_dir: Path,
    gate: bool,
    author_name: str | None,
    min_words: int,
    pause_s: float,
) -> list[dict]:
    space_url = ai_comment._DEFAULT_SPACE_URL
    space_key = ai_comment._get_space_key(argparse.Namespace())
    if not space_key:
        print("⚠️  IG_COMMENT_SPACE_KEY assente: lo Space potrebbe rispondere 401.")

    rows = []
    for case in cases:
        raw_caption = case["caption"]
        caption = ai_comment.clean_caption(raw_caption, case.get("target_username"))
        usable = ai_comment.caption_is_usable(caption, min_words)
        row = {
            "id": case["id"],
            "category": case["category"],
            "caption_raw": raw_caption,
            "caption_clean": caption,
            "usable": usable,
            "decision": None,
            "comment_raw": None,
            "comment_final": None,
            "reject_reason": None,
            "model": None,
            "latency_ms": None,
        }
        if gate and not usable:
            row["decision"] = f"SKIP ({ai_comment.caption_skip_reason(caption, min_words)}: il bot non chiama l'AI)"
            rows.append(row)
            print(f"[{case['id']:2d}] {case['category']:20s} SKIP  {caption[:50]!r}")
            continue

        payload = {
            "caption": caption,
            "media_type": "photo",
            "target_username": case.get("target_username"),
            "hint": ai_comment.build_hint_for_call(hint),  # come in produzione: + variazione casuale
            "language": language,
        }
        t0 = time.time()
        comment = ai_comment._call_space(space_url, space_key, payload)
        row["latency_ms"] = int((time.time() - t0) * 1000)
        row["model"] = ai_comment.last_model_used() if comment is not None else None
        row["comment_raw"] = comment
        if comment is None:
            row["decision"] = "AI FALLITA (lo Space non ha risposto) -> nessun commento"
        else:
            comment = ai_comment.polish_comment(comment)
            row["comment_raw"] = comment
            ok, why = ai_comment.comment_is_acceptable(
                comment, author_name, caption=caption, language=language
            )
            if ok:
                row["decision"] = "OK"
                row["comment_final"] = comment
            else:
                row["decision"] = "SCARTATO dal validatore -> il bot salta il commento"
                row["reject_reason"] = why
        rows.append(row)
        print(f"[{case['id']:2d}] {case['category']:20s} {row['decision'][:9]:9s} {(row['model'] or '?')[-12:]:12s} {caption[:36]!r:40s} -> {comment!r}")
        time.sleep(pause_s)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{variant}.json").write_text(
        json.dumps({"variant": variant, "hint": hint, "language": language, "rows": rows},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    md = [f"# Variante `{variant}`\n", f"hint: {hint}\n", "| # | categoria | caption | decisione | commento |", "|---|---|---|---|---|"]
    for r in rows:
        md.append(
            f"| {r['id']} | {r['category']} | {r['caption_clean'][:60].replace('|','/')} | "
            f"{r['decision'].split(' (')[0]} | {(r['comment_raw'] or '').replace('|','/')} |"
        )
    (out_dir / f"{variant}.md").write_text("\n".join(md), encoding="utf-8")
    n_ok = sum(1 for r in rows if r["decision"] == "OK")
    n_skip = sum(1 for r in rows if r["decision"].startswith("SKIP"))
    n_rej = sum(1 for r in rows if r["decision"].startswith("SCARTATO"))
    print(f"\n{variant}: {len(rows)} casi | OK {n_ok} | SKIP {n_skip} | SCARTATI {n_rej} | salvato in {out_dir}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True, help="nome della variante (usato per i file di output)")
    ap.add_argument("--hint-file", default=None, help="file di testo con l'hint; default: quello in config.yml")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--out", default=str(REPO / "tools" / "bench_out"))
    ap.add_argument("--no-gate", action="store_true", help="chiama l'AI anche su caption inutilizzabili (per vedere cosa inventa)")
    ap.add_argument("--author-name", default="Mattia", help="nome di chi scrive: i commenti che lo contengono vengono scartati")
    ap.add_argument("--min-caption-words", type=int, default=3)
    ap.add_argument("--pause", type=float, default=0.4, help="pausa tra le chiamate (rate limit Groq)")
    ap.add_argument("--only", default=None, help="id separati da virgola, per rilanciare solo alcuni casi")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    hint = Path(args.hint_file).read_text(encoding="utf-8").strip() if args.hint_file else _read_hint_from_config(Path(args.config))
    language = _read_language_from_config(Path(args.config))
    run(cases, hint, language, args.variant, Path(args.out), gate=not args.no_gate,
        author_name=args.author_name, min_words=args.min_caption_words, pause_s=args.pause)
    return 0


if __name__ == "__main__":
    sys.exit(main())
