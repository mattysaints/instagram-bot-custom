# Setup del fork (clone → bot funzionante)

Istruzioni specifiche di questo fork. Per la doc generale di GramAddict vedi
[README.md](README.md) e https://docs.gramaddict.org.

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
(bearer token dello Space HF che genera i commenti AI — lo trovi nei Secrets
di https://huggingface.co/spaces/mattysaints/instagram_bot).

Se lo lasci col placeholder il bot parte lo stesso: i commenti AI vengono
saltati e si usa il fallback `comments_list.txt`.

## 3. Device

Il `device:` nei config punta a un serial ADB preciso. Verifica il tuo:

```bash
adb devices -l
```

e allinea il campo `device:` nei config di `accounts/<username>/`.

Serial attualmente configurati:
- branch `main` → `f2487e11` (OnePlus 9 Pro fisico)
- branch `personalcoaching` → `f2487e11`

Sul telefono servono: **Debug USB** attivo, PC autorizzato, modalità USB
"Trasferimento file", schermo sbloccato.

## 4. Lancio

```bash
# sessione singola manuale
python run.py --config accounts/marramattia_fmgpro/config-once.yml

# loop giornaliero con working-hours generate dinamicamente
python run-dynamic.py --config accounts/marramattia_fmgpro/config.yml

# solo unfollow
python run.py --config accounts/marramattia_fmgpro/config-unfollow.yml

# sorgente culturismoitaliano con filtro follower 5k-10k temporaneo
./run-culturismo-following.sh
```

Da PyCharm ci sono già le run configuration pronte in `.idea/runConfigurations/`.

## 5. Commenti e DM AI

Generati da uno HuggingFace Space (`mattysaints/instagram_bot`) che gira su
**Groq** (free tier: 14.400 req/giorno) con cascata
`llama-3.3-70b-versatile` → `llama-3.1-8b-instant` → `gemma2-9b-it`.

- Codice dello Space: repo separato, non incluso qui.
- Endpoint: `POST /api/generate` (commenti), `POST /api/generate_dm` (DM,
  usati solo dal branch `personalcoaching`).
- Su qualunque errore (rete, 5xx, rate-limit, guardrail) il bot ricade sui
  file `comments_list.txt` / `pm_list.txt`: nessun crash.

Config rilevanti (in `accounts/<username>/config.yml`):

```yaml
ai-comments-enabled: true
ai-comments-space-url: https://mattysaints-instagram-bot.hf.space
# la key NON va qui: sta in .env.local come IG_COMMENT_SPACE_KEY
```

## 6. Differenze tra i branch

| Branch | Account principale | Feature extra |
|---|---|---|
| `main` | `marramattia_fmgpro` | commenti AI, no DM |
| `personalcoaching` | `simonebestagno` | commenti AI **+ DM AI** (job `dm-followback`) |
