"""Verifica su device reale che gli username Instagram di un config esistano.

PERCHE' ESISTE
    La regola del progetto e' "mai inventare username": una sorgente
    inesistente non da' errore, il bot ci prova, non trova niente e perde
    ~30 secondi a sessione finche' la quarantena automatica non la esclude.
    Con 60-100 sorgenti per account, controllarle a mano non si fa.

    Cercare su Google non basta: instagram.com risponde con un login wall sia
    per i profili veri sia per quelli inesistenti, quindi da fuori i due casi
    non si distinguono. L'unico controllo affidabile e' aprire il profilo
    nell'app gia' loggata sull'emulatore, che e' anche il modo in cui il bot
    stesso vedra' quel profilo.

COME
    Apre `instagram://user?username=<handle>` forzando il pacchetto Instagram
    (senza `-p` Android mostra il selettore Chrome/Instagram e il dump legge
    quello), aspetta, e guarda l'albero di accessibilita':

      - profilo esistente -> compaiono "Posts" / "Followers" / "Following"
      - profilo assente   -> resta solo l'username, senza nessun contatore

    Come effetto utile ricava anche follower, nome e categoria, che servono a
    decidere se un account va in `blogger` (grandi: solo commento pubblico)
    o in `blogger-followers` (si pescano i loro follower).

USO
    python tools/verify-ig-handles.py --device emulator-5554 --config accounts/rb.coach/config.yml
    python tools/verify-ig-handles.py --device emulator-5554 --handles a,b,c --out esito.json

    L'esito va su stdout in tabella e, con --out, in JSON.

NOTA
    Aprire un profilo e' l'azione piu' innocua che ci sia su Instagram (nessuna
    scrittura), ma resta traffico su un account vero: l'attesa fra un handle e
    l'altro non va azzerata.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Optional

import uiautomator2 as u2

# "Followers" e' il discriminante: un profilo inesistente non ha contatori.
MARCATORI_ESISTE = ("Followers", "Follower", "Seguaci")


def parse_conteggio(s: str) -> Optional[int]:
    """'63.2K' -> 63200, '1,617' -> 1617, '1.2M' -> 1200000."""
    if not s:
        return None
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([KkMm])?", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    suffisso = (m.group(2) or "").upper()
    if suffisso == "K":
        n *= 1_000
    elif suffisso == "M":
        n *= 1_000_000
    return int(n)


def leggi_handle_dal_config(path: Path) -> list:
    """Prende blogger-followers e blogger dal config, mantenendo l'ordine."""
    testo = path.read_text(encoding="utf-8")
    visti, ordinati = set(), []
    for chiave in ("blogger-followers", "blogger"):
        m = re.search(rf"^{re.escape(chiave)}:\s*\[(.*?)\]", testo, re.M | re.S)
        if not m:
            continue
        for h in m.group(1).split(","):
            h = h.strip()
            if h and h not in visti:
                visti.add(h)
                ordinati.append(h)
    return ordinati


def ispeziona(d, handle: str, attesa: float, tentativi: int = 2) -> dict:
    """Apre il profilo e legge l'albero, ritentando una seconda volta.

    Il ritentativo non e' una cortesia. Su un emulatore carico Instagram ci
    mette a volte oltre 20 secondi ad apparecchiare un profilo, e la schermata
    intermedia e' IDENTICA a quella di un profilo inesistente: solo l'username,
    nessun contatore. Senza secondo tentativo si marcano come morte delle
    sorgenti vive -- e' successo con ricette_ghisa, che ha 310mila follower.

    Fra un tentativo e l'altro l'app viene chiusa e riaperta: quando un profilo
    non compare, quasi sempre e' Instagram rimasta impiantata, non l'account a
    non esistere.
    """
    ultimo_errore = ""
    for n in range(1, tentativi + 1):
        try:
            if n > 1:
                d.shell("am force-stop com.instagram.android")
                time.sleep(2)
            # -p com.instagram.android: senza, Android apre il selettore di app
            # e il dump legge la finestra del selettore invece del profilo.
            d.shell(
                "am start -a android.intent.action.VIEW "
                f'-d "instagram://user?username={handle}" -p com.instagram.android'
            )
            time.sleep(attesa * n)
            xml = d.dump_hierarchy(compressed=True)
        except Exception as e:
            # Device offline o agente caduto: si prova a riagganciare, ma un
            # handle andato storto non deve MAI fermare la lista intera.
            ultimo_errore = str(e)[:150]
            try:
                d = u2.connect(d.serial)
                time.sleep(3)
            except Exception:
                pass
            continue

        ris = _leggi(xml, handle)
        if ris["esito"] == "esiste":
            return ris

    if ultimo_errore:
        return {"handle": handle, "esito": "errore", "nota": ultimo_errore}
    return {"handle": handle, "esito": "assente"}


def _leggi(xml: str, handle: str) -> dict:
    """Ricava l'esito da un albero di accessibilita' gia' scaricato."""
    # unescape: il dump XML riporta "Sports &amp; Fitness", e senza questo la
    # categoria finirebbe nel config con l'entita' dentro.
    testi = [html.unescape(t) for t in re.findall(r'text="([^"]{1,120})"', xml) if t.strip()]

    esiste = any(m in testi for m in MARCATORI_ESISTE)
    ris = {"handle": handle, "esito": "esiste" if esiste else "assente"}
    if not esiste:
        return ris

    # I contatori stanno subito PRIMA della loro etichetta: [.., '63.2K',
    # 'Followers', ..]. Leggerli per posizione evita di dipendere dai
    # resource-id, che cambiano a ogni versione di Instagram.
    for etichetta, campo in (("Posts", "post"), ("Followers", "follower"), ("Following", "seguiti")):
        if etichetta in testi:
            i = testi.index(etichetta)
            if i > 0:
                ris[campo] = parse_conteggio(testi[i - 1])

    # Il nome e la categoria vengono dopo il blocco dei contatori.
    if "Following" in testi:
        coda = testi[testi.index("Following") + 1:]
        # il badge di Threads ("100M+") sta fra i contatori e il nome: va saltato
        coda = [t for t in coda if not re.fullmatch(r"\d+[KM]?\+", t)]
        if coda:
            ris["nome"] = coda[0]
        for t in coda[1:4]:
            if t and not any(ch.isdigit() for ch in t) and len(t) < 40:
                ris["categoria"] = t
                break

    ris["privato"] = "This account is private" in " ".join(testi)
    return ris


def ispeziona_hashtag(d, tag: str, attesa: float, tentativi: int = 2) -> dict:
    """Apre la pagina di un hashtag e controlla che esista e abbia contenuti.

    Serve perche' gli hashtag "da coach" (#schedapersonalizzata,
    #definizionemuscolare) li scrive chi li inventa e nessuno li cerca: il
    bot apre la tab Recenti, la trova vuota e brucia la sessione. Da fuori
    non si puo' sapere, instagram.com/explore/tags risponde col login.

    Lo schema instagram://tag?name= NON apre l'app su questa versione di
    Instagram (300.x): resta sulla home. Funziona invece l'URL web forzato
    sul pacchetto. Il numero di post non compare nell'albero di accessibilita';
    compaiono pero' le celle della griglia ("Reel by X at row 1, column 2"),
    quindi si conta quante ce ne sono nella prima schermata.
    """
    ultimo_errore = ""
    for n in range(1, tentativi + 1):
        try:
            if n > 1:
                d.shell("am force-stop com.instagram.android")
                time.sleep(2)
            d.shell(
                "am start -a android.intent.action.VIEW "
                f'-d "https://www.instagram.com/explore/tags/{tag}/" -p com.instagram.android'
            )
            time.sleep(attesa * n)
            xml = d.dump_hierarchy(compressed=True)
        except Exception as e:
            ultimo_errore = str(e)[:150]
            try:
                d = u2.connect(d.serial)
                time.sleep(3)
            except Exception:
                pass
            continue
        testi = [html.unescape(t) for t in re.findall(r'text="([^"]{1,120})"', xml) if t.strip()]
        desc = [html.unescape(t) for t in re.findall(r'content-desc="([^"]{1,160})"', xml) if t.strip()]
        celle = [x for x in desc if re.search(r"(by|di).*at row \d+, column \d+", x, re.I)]
        intestazione = any(x.strip().lower() == f"#{tag}".lower() for x in testi)
        # Dopo un'apertura a freddo l'intestazione "#tag" compare a ~14 s ma la
        # griglia dei contenuti solo a ~24 s: una pagina "aperta e vuota" e'
        # quasi sempre una pagina ancora in caricamento, non un tag senza post.
        if intestazione and not celle:
            try:
                time.sleep(12)
                xml = d.dump_hierarchy(compressed=True)
                desc = [html.unescape(t) for t in re.findall(r'content-desc="([^"]{1,160})"', xml) if t.strip()]
                celle = [x for x in desc if re.search(r"(by|di).*at row \d+, column \d+", x, re.I)]
            except Exception:
                pass
        if celle:
            return {"handle": tag, "esito": "esiste", "tipo": "hashtag",
                    "celle_visibili": len(celle), "esempio": celle[0][:80]}
        if intestazione and n == tentativi:
            # pagina aperta ma griglia vuota anche al secondo giro: tag senza post
            return {"handle": tag, "esito": "assente", "tipo": "hashtag",
                    "nota": "pagina aperta ma nessun contenuto"}
    if ultimo_errore:
        return {"handle": tag, "esito": "errore", "nota": ultimo_errore}
    return {"handle": tag, "esito": "assente", "tipo": "hashtag"}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=True, help="serial ADB, es. emulator-5554")
    ap.add_argument("--config", help="config.yml da cui leggere blogger e blogger-followers")
    ap.add_argument("--handles", help="elenco separato da virgole, in alternativa a --config")
    ap.add_argument("--out", help="file JSON con l'esito completo")
    ap.add_argument("--hashtag", action="store_true",
                    help="gli elementi di --handles sono HASHTAG (senza #), non username")
    ap.add_argument("--attesa", type=float, default=18.0,
                    help="secondi di attesa dopo l'apertura (default 18; il "
                         "secondo tentativo raddoppia)")
    ap.add_argument("--max-vuoti", dest="max_vuoti", type=int, default=6,
                    help="quante schermate vuote di fila prima di fermarsi, "
                         "per non scambiare un rallentamento di Instagram per "
                         "una raffica di account inesistenti (default 6)")
    args = ap.parse_args()

    if args.config:
        handles = leggi_handle_dal_config(Path(args.config))
    elif args.handles:
        handles = [h.strip() for h in args.handles.split(",") if h.strip()]
    else:
        print("Serve --config oppure --handles", file=sys.stderr)
        return 1

    d = u2.connect(args.device)
    print(f"device {args.device}: {len(handles)} handle da verificare "
          f"(~{len(handles) * args.attesa / 60:.0f} min)\n", flush=True)

    risultati = []
    vuoti_di_fila = 0
    for i, h in enumerate(handles, 1):
        try:
            r = (ispeziona_hashtag if args.hashtag else ispeziona)(d, h, args.attesa)
        except Exception as e:
            # rete di sicurezza: nessun handle puo' interrompere la lista
            r = {"handle": h, "esito": "errore", "nota": str(e)[:150]}
        risultati.append(r)

        # Una raffica di schermate vuote di fila non significa che quegli
        # account siano spariti tutti insieme: significa quasi sempre che
        # Instagram ha rallentato l'app dopo troppe aperture ravvicinate.
        # Continuare a martellare peggiora la situazione e riempie l'esito di
        # falsi "assente", che poi cancellerebbero sorgenti buone dal config.
        vuoti_di_fila = vuoti_di_fila + 1 if r["esito"] != "esiste" else 0
        if vuoti_di_fila >= args.max_vuoti:
            print(f"\nFERMO: {vuoti_di_fila} schermate vuote di fila. "
                  "Molto probabilmente e' Instagram che sta rallentando l'app, "
                  "non sono account inesistenti.\n"
                  "Aspetta qualche minuto e rilancia sui soli handle rimasti, "
                  "oppure alza --attesa.", flush=True)
            break
        if r["esito"] == "esiste" and args.hashtag:
            print(f"{i:3}/{len(handles)} OK       #{h:33} "
                  f"{r.get('celle_visibili', 0)} contenuti in prima schermata  "
                  f"es. {r.get('esempio', '')}", flush=True)
        elif r["esito"] == "esiste":
            f = r.get("follower")
            print(f"{i:3}/{len(handles)} OK       {h:34} "
                  f"{'' if f is None else format(f, ',')} follower  "
                  f"{r.get('nome', '')} | {r.get('categoria', '')}", flush=True)
        else:
            print(f"{i:3}/{len(handles)} {r['esito'].upper():8} {h}", flush=True)
        if args.out:
            Path(args.out).write_text(
                json.dumps(risultati, indent=1, ensure_ascii=False), encoding="utf-8"
            )

    assenti = [r["handle"] for r in risultati if r["esito"] == "assente"]
    errori = [r["handle"] for r in risultati if r["esito"] == "errore"]
    print(f"\nesistenti: {len(risultati) - len(assenti) - len(errori)}"
          f" | assenti: {len(assenti)} | errori: {len(errori)}")
    if assenti:
        print("DA TOGLIERE DAL CONFIG: " + ", ".join(assenti))
    if errori:
        print("da ricontrollare: " + ", ".join(errori))
    return 0


if __name__ == "__main__":
    sys.exit(main())
