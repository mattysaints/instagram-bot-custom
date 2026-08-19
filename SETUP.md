# Setup del fork (clone → bot funzionante)

Istruzioni specifiche di questo fork, branch `personalcoaching`. Per la doc
generale di GramAddict vedi [README.md](README.md) e https://docs.gramaddict.org.

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

## 2. Segreti

```bash
cp .env.example .env.local
```

Poi apri `.env.local` e metti il valore vero di `IG_COMMENT_SPACE_KEY`
(bearer token dello Space HF che genera commenti e DM — lo trovi nei Secrets
di https://huggingface.co/spaces/mattysaints/instagram_bot).

Se lasci il placeholder il bot parte lo stesso: commenti e DM AI vengono
saltati e si usano i fallback `comments_list.txt` / `pm_list.txt`.

## 3. Device

```bash
adb devices -l
```

- `accounts/simonebestagno/` non specifica `device:` → GramAddict usa l'unico
  device connesso. Se ne hai più di uno, aggiungi la riga `device: <serial>`.
- `accounts/marramattia_fmgpro/` è configurato su `f2487e11` (OnePlus 9 Pro).

Sul telefono servono: **Debug USB** attivo, PC autorizzato, modalità USB
"Trasferimento file", schermo sbloccato.

## 4. Lancio

```bash
# alterna follow/unfollow a giorni alterni (account simonebestagno)
./run-bot.sh

# forza una modalita'
./run-bot.sh follow
./run-bot.sh unfollow
./run-bot.sh unfollow-followers
./run-bot.sh unfollow-old

# loop giornaliero con working-hours generate dinamicamente
python run-dynamic.py --config accounts/simonebestagno/config.yml
```

Da PyCharm ci sono già le run configuration in `.idea/runConfigurations/`.

## 5. Commenti e DM AI

Generati da uno HuggingFace Space (`mattysaints/instagram_bot`) che gira su
**Groq** (free tier: 14.400 req/giorno) con cascata
`llama-3.3-70b-versatile` → `llama-3.1-8b-instant` → `gemma2-9b-it`.

| Endpoint | Usato da | Regole |
|---|---|---|
| `POST /api/generate` | commenti sotto ai post | no emoji, no hashtag, no `!`, tono adattato al contenuto della caption |
| `POST /api/generate_dm` | job `dm-followback` | 2-3 frasi, saluto + 1 domanda aperta, max 1 emoji leggera, no link/CTA |

Su qualunque errore (rete, 5xx, rate-limit, guardrail) il bot ricade sui file
di fallback: nessun crash.

Config rilevanti (in `accounts/<username>/config.yml`):

```yaml
ai-comments-enabled: true
ai-comments-space-url: https://mattysaints-instagram-bot.hf.space
# la key NON va qui: sta in .env.local come IG_COMMENT_SPACE_KEY

ai-dm-enabled: false          # DM AI: ereditano URL/key da ai-comments-* se non specificati
ai-dm-allow-emoji: true
```

`ai_dm.py` importa da `ai_comment.py` i simboli condivisi (autoload `.env.local`,
circuit breaker, rilevamento rete down): se tocchi uno, controlla l'altro.

## 6. Differenze tra i branch

| Branch | Account principale | Feature extra |
|---|---|---|
| `main` | `marramattia_fmgpro` | commenti AI, no DM |
| `personalcoaching` | `simonebestagno` | commenti AI **+ DM AI** (job `dm-followback`) |
