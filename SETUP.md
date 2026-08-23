# Setup del fork (clone → bot funzionante)

Branch `robertobuonomo`, dedicato all'account **@rb.coach** (Roberto Buonomo /
RB Coaching, Milano). Per la doc generale di GramAddict vedi [README.md](README.md)
e https://docs.gramaddict.org.

Il contesto sul coach (palmares, servizi, tono di voce, fonti) sta in
[accounts/rb.coach/KB.md](accounts/rb.coach/KB.md): serve a rigenerare i prompt
AI se cambia qualcosa.

## 1. Dipendenze

```bash
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

Python 3.11-3.14. Le pin in `requirements.txt` sono necessarie: `setuptools<81`
(uiautomator2 2.16.x usa `pkg_resources`) e `emoji==1.6.1`.

> **Con queste pin l'intervallo utile è 3.11-3.12, non 3.14.** `PyYAML==6.0.1`
> pubblica wheel fino a cp312: su 3.13+ pip prova a compilarla e si ferma su
> *«Microsoft Visual C++ 14.0 or greater is required»*. Le alternative sono
> installare i Build Tools di Visual Studio o salire a `PyYAML==6.0.2` (la
> prima con le wheel cp313), che vuol dire toccare una pin per un motivo che
> con Python 3.12 non esiste. **Il mini PC gira su 3.12.10**, verificato.

## 2. Segreti

```bash
cp .env.example .env.local
```

Metti in `.env.local` il valore vero di `IG_COMMENT_SPACE_KEY` (bearer token
dello Space HF che genera i commenti — nei Secrets di
https://huggingface.co/spaces/mattysaints/instagram_bot).

Col placeholder il bot parte lo stesso: i commenti AI vengono saltati e si usa
il fallback `accounts/rb.coach/comments_list.txt`.

## 3. Device

```bash
adb devices -l
```

Allinea il campo `device:` nei config di `accounts/rb.coach/` al serial reale.
Ora è impostato su `f2487e11` (ereditato da main): **va cambiato** col device
che userà Roberto.

Sul telefono servono: **Debug USB** attivo, PC autorizzato, modalità USB
"Trasferimento file", schermo sbloccato.

## 4. Warm-up (importante, non saltarlo)

Se l'account non ha mai usato automazione, partire ai cap pieni è il modo più
veloce per prendere un action-block. Per le prime due settimane:

| Periodo | follow/g | like/g | commenti/g |
|---|---|---|---|
| Giorni 1-4 | 15-20 | 40-50 | 0 |
| Giorni 5-9 | 25-35 | 60-80 | 5-10 |
| Giorni 10-14 | 40-50 | 90-110 | 15-25 |
| Da giorno 15 | 50-60 | 100-130 | 25-40 |

Si regola con `daily-follows-cap`, `daily-likes-cap`, `daily-comments-cap` in
`accounts/rb.coach/config.yml`. I valori committati sono già quelli **a regime**:
abbassali per la fase iniziale.

In warm-up conviene usare `config-once.yml` (una sessione alla volta, lanciata
a mano) invece del loop.

## 5. Lancio

```bash
# sessione singola manuale (consigliata in warm-up)
python run.py --config accounts/rb.coach/config-once.yml

# loop giornaliero, 5 micro-sessioni con orari generati dinamicamente
python run-dynamic.py --config accounts/rb.coach/config.yml

# solo unfollow di chi non ha ricambiato dopo 2 giorni (`unfollow-delay`)
python run.py --config accounts/rb.coach/config-unfollow.yml
```

Strategia consigliata: giorni dispari `config.yml` (follow + like + commenti),
giorni pari `config-unfollow.yml`. Mescolare follow e unfollow nella stessa
sessione è un pattern che IG riconosce facilmente.

## 6. Cosa fa il bot

| Feature | Stato | Dove si configura |
|---|---|---|
| Commenti AI contestuali | ✅ attivo | `ai-comments-*` in `config.yml` |
| Follow | ✅ attivo | `follow-percentage`, `follow-limit`, `daily-follows-cap` |
| Unfollow non-followers | ✅ attivo | `config-unfollow.yml` |
| Like | ✅ attivo | `likes-count`, `likes-percentage` |
| Risposte a sondaggi / box domande nelle storie | ❌ **non supportato** | — |
| DM | ⛔ disattivato di proposito | `total-pm-limit: 0-0` |

### Sui sondaggi e i box domande

GramAddict non ha nessun job per interagire con gli sticker delle storie: le
storie si possono solo *guardare*, e su questa versione di Instagram nemmeno
quello funziona in modo affidabile (`Failed to open the story container`), per
cui sono disattivate (`stories-count: 0`).

Aggiungerla vorrebbe dire scrivere un plugin nuovo che apre le storie,
riconosce il tipo di sticker (poll a 2 opzioni, quiz a 4, box domande con campo
testo, slider con emoji) e interagisce di conseguenza. È fattibile con
uiautomator2 ma è codice fragile — dipende dai resource-id interni di
Instagram, che cambiano a ogni aggiornamento — e l'interazione ripetuta con le
storie è molto tracciata lato anti-spam. Prima di provarci va risolto il
problema a monte: le storie non si aprono.

## 7. Sorgenti

`blogger-followers` in `config.yml` contiene federazioni, atleti IFBB PRO,
coach e community nazionali. Le sorgenti Torino/Piemonte che c'erano su `main`
sono state tolte: erano tarate su un altro account.

Per il bacino locale Milano il lavoro lo fanno gli hashtag
(`palestramilano`, `personaltrainermilano`, `bodybuildingmilano`, …).

Se aggiungi palestre o profili milanesi a `blogger-followers`, **verifica prima
che l'username esista** aprendo `instagram.com/USERNAME`: una sorgente
inesistente fa perdere ~30s a sessione finché la quarantena automatica non la
esclude.

Il bot ricalcola da solo il follow-back-rate per sorgente
(`auto-fbr-refresh: true`) e pesca più spesso da quelle che convertono.

## 8. Commenti AI

Generati da uno HuggingFace Space (`mattysaints/instagram_bot`) su **Groq**
(free tier: 14.400 richieste/giorno), cascata `llama-3.3-70b-versatile` →
`llama-3.1-8b-instant` → `gemma2-9b-it`.

Il prompt (`ai-comments-prompt-hint`) è tarato su Roberto: registro tecnico
**solo quando la caption parla davvero di allenamento o gara**, altrimenti si
adatta al contenuto reale (viaggio, cibo, famiglia). Mai vendita, mai menzione
di FIT LAB o dei servizi, mai emoji/hashtag/punti esclamativi.

Su qualunque errore si cade su `comments_list.txt`: nessun crash.
