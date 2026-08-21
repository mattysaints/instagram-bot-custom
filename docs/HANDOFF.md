# Handoff — bot Instagram, stato al 21/08/2026

Documento per riprendere il lavoro da un'altra macchina o in una chat nuova.
Incollalo come primo messaggio, oppure fai `git pull` e aprilo.

---

## 1. Il progetto in breve

Fork di **GramAddict** che automatizza due account Instagram: like, follow,
unfollow, commenti generati da IA sotto post e reel, e risposte agli sticker
"box domande" nelle storie altrui.

| | |
|---|---|
| Repo | `github.com/mattysaints/instagram-bot-custom` |
| Branch di lavoro | **`robertobuonomo`** (cliente pagante) |
| Altri branch | `main`, `personalcoaching` |
| Linguaggio | Python + `uiautomator2` su ADB |
| Destinazione | mini PC Windows 11 Pro, h24 7/7, due emulatori Android |

I branch `robertobuonomo` e `personalcoaching` sono **git worktree** del clone
principale `instagram-bot-custom`, e **non hanno un virtualenv proprio**: usano
quello del clone principale. Vedi `IGBOT_PYTHON` più avanti.

## 2. I due account

Sono deliberatamente diversi: uno vende coaching, l'altro è un atleta.

| | `rb.coach` | `roberto_buonomo_ifbbpro` |
|---|---|---|
| Taglio | azienda di coaching (progressi clienti, stile di vita) | personale, bodybuilding agonistico (gare, preparazione, dieta) |
| Emulatore | `emulator-5554` (AVD `bot_rb`) | `emulator-5556` (AVD `bot_pers`) |
| Sessioni/giorno | 5 | 4 |
| Follow | 55-70% | 50-65% |
| Commenti | 25-35% | 25-35% |
| Like per profilo | 2 | 2 |
| Sorgenti | palestre Lombardia + competitor italiani, hashtag su trasformazione/Milano | IFBB pro italiani e internazionali (John Jewett, Kuba Cielen), hashtag su gara/peak week |

**Regola centrale della strategia**, decisa col cliente: like, follow e risposte
agli sticker **solo sui profili piccoli**; ai profili grandi **solo commenti
pubblici sotto post e reel** (non commenti alle storie). È implementata con
`blogger-comment-only: true` sul job `blogger` e `sticker-check-percentage:
60-75` sul flusso normale.

Il job dedicato `answer-story-stickers` esiste ma è **disattivato di proposito**:
gli sticker si fanno dentro il flusso normale, sui profili piccoli.

Lo **slider** nelle storie non è automatizzabile: è disegnato su canvas e non
compare nell'albero di accessibilità. Servirebbe visione artificiale. Box
domande e sondaggi invece funzionano, verificati end-to-end su device reale.

## 3. Generazione commenti — HuggingFace Space

Space `instagram_bot` (utente `mattysaints`), SDK **Docker** (non Gradio: su
Python 3.13 Gradio 4.x si rompe perché `audioop` è stato rimosso).

Backend **Groq**, non più Gemini né HF Inference (l'abbonamento PRO include
0 $ di credito Inference). Cascata di modelli con parametri per modello:

```
openai/gpt-oss-120b   reasoning_effort: low
qwen/qwen3.6-27b      reasoning_effort: none
openai/gpt-oss-20b    reasoning_effort: low
```

Endpoint: `/api/generate`, `/api/generate_dm`, `/api/answer_question`,
`/api/reply_to_sticker`, `/api/health`.

Accorgimenti già presenti: rimozione dei blocchi `<think>`, strip del markdown,
chiusura sull'ultima frase intera per non troncare a metà parola, rilevamento
lingua per stopword (l'`auto` non funzionava, l'italiano dominava sempre), e
rifiuto degli argomenti medici.

> **Da fare**: la chiave API Groq è passata in chat, va **ruotata**.

## 4. Deploy h24 sul mini PC — il lavoro di questa sessione

Tutto in `deploy/`, documentato in [deploy/README.md](../deploy/README.md).

| File | Ruolo |
|---|---|
| `check-machine.ps1` | **da lanciare per primo**: dice se la macchina ce la fa |
| `start-account.ps1` | avvia l'AVD, aspetta `sys.boot_completed`, lancia il bot |
| `watchdog.ps1` | supervisiona i due account, backoff sui crash |
| `install-autostart.ps1` | registra i task pianificati |
| `remote_control.py` | bot Telegram: status/stop/start/log/shot/cmd |
| `install-remote-shell.ps1` | OpenSSH + Tailscale |
| `common.ps1` | ricerca del virtualenv |

### Le tre decisioni non ovvie

**1. I task partono AL LOGON, non all'avvio.** L'emulatore Android ha bisogno
di una sessione desktop interattiva: nella sessione 0 non parte e non dà
nemmeno un errore chiaro. Di conseguenza serve l'**autologon di Windows**
(consigliato Sysinternals Autologon, che cifra la password), altrimenti dopo un
blackout il PC resta alla schermata di accesso e non riparte niente.

**2. I due account sono sfalsati di 90 minuti** (`OffsetMin` in `watchdog.ps1`).
Le sessioni durano 90 minuti e distano 3 ore, quindi ogni account lavora metà
del tempo: sfalsandoli si alternano invece di contendersi i 4 core.
Non serve toccare `working-hours` nei config: **`run-dynamic.py` riscrive quella
riga a ogni lancio**, calcolandola sull'ora di partenza.

**3. Backoff esponenziale sui crash** (30 min → 1 h → 2 h). Se Instagram ha
messo un action-block, ritentare ogni due minuti peggiora la situazione.

Lo stop da Telegram non uccide il watchdog: scrive un file `<account>.stop` che
il watchdog rilegge a ogni giro, quindi sopravvive a un riavvio.

### Controllo da remoto

Due strade, per scopi diversi:

- **`/cmd` dal bot Telegram** — comando al volo dal telefono. È **spento di
  default** (`allow-shell: false`): è esecuzione di comandi arbitrari, chi entra
  nell'account Telegram si prende la macchina. Timeout 2 min, output troncato,
  ogni comando loggato.
- **SSH + Tailscale** (`install-remote-shell.ps1`) — terminale vero. Usa
  l'OpenSSH già incluso in Windows 11; Tailscale rende il mini PC raggiungibile
  da fuori **senza aprire porte sul router**.

Trappola gestita nello script: per gli utenti **amministratori** Windows non
legge `~/.ssh/authorized_keys` ma
`%ProgramData%\ssh\administrators_authorized_keys`, con permessi ristretti.
Sbagliare posto o permessi fa fallire l'accesso **in silenzio**.

## 5. Bug trovati eseguendo, non rileggendo

Vale la pena conservarli: sono tutti casi in cui il codice *sembrava* giusto.

| Bug | Effetto | Perché sfuggiva |
|---|---|---|
| `run-dynamic.py` stampa emoji, stdout rediretto | **UnicodeEncodeError, il bot non partiva mai** sotto il watchdog | in console funziona; solo in pipe Windows usa cp1252 |
| `Where-Object` con un solo risultato | `$roots[0]` dava `"C"`, il primo carattere | `.Count` vale 1 anche per una stringa |
| `emulator -accel-check` | si pianta e lascia processi `emulator-check` appesi | a volte risponde, a volte no |
| regex su `powercfg` | il controllo non leggeva nulla | l'etichetta è tradotta: "Indice impostazione **alimentazione** CA corrente" |
| SLAT e virtualizzazione via WMI | falso allarme | con un hypervisor già attivo Windows riporta `false` |
| venv cercato solo in `<repo>\.venv` | non esiste nei worktree | risolto con `IGBOT_PYTHON` |

Prova A/B sul primo: senza fix `exit=1`, con fix `exit=0`.

## 6. Correzioni di merito emerse da una verifica avversariale

Tre cose date per buone che **non lo erano**:

1. **La CPU del mini PC non è verificata.** L'inserzione italiana (ASIN
   `B0DTHW462L`) dice "Huidun 2026 Mini PC Ryzen 5 **H80**" — H80 è il nome del
   prodotto, non un modello AMD. Il Ryzen 5 3500U viene dai listing gemelli
   USA, per deduzione. Da verificare con CPU-Z controllando che la **cache L3
   sia 4 MB**. Precedente rilevante: a marzo 2026 Chuwi e Ninkear hanno venduto
   macchine con firmware manipolato che faceva mostrare a CPU-Z una sigla falsa.

2. **AEHD non è più la strada giusta.** Google lo dismette il **31/12/2026** e
   indica **WHPX**; HAXM è fuori dall'emulatore da ottobre 2025.

3. **Le immagini API 37+ impongono 4 GB per AVD e riscrivono la configurazione
   senza dirlo.** Restare ad **API ≤ 36**, altrimenti i 2048 MB diventano 4096
   e due emulatori non entrano in 16 GB.

Inoltre: se i 16 GB sono su **un solo modulo**, la macchina va in single channel
e dimezza la banda verso la iGPU — che è proprio ciò che l'emulatore usa.
`check-machine.ps1` lo rileva.

Sul ban, per onestà: **non esiste nessuna prova pubblica** che Instagram
rilevi gli emulatori più dei telefoni fisici. Tutte le fonti che lo sostengono
vendono l'alternativa.

## 7. Cosa resta da fare

Sul mini PC, quando arriva:

- [ ] `emulator -accel-check` → deve dare `accel: 0`. Se fallisce e nel BIOS non
      c'è **SVM Mode**, il piano a due emulatori decade: **rendere il PC**
- [ ] `.\deploy\check-machine.ps1` e risolvere quello che segnala
- [ ] creare gli AVD **`bot_rb`** e **`bot_pers`** (2 GB, 2 core, 720×1280,
      API ≤ 36, senza Google Play, snapshot off)
- [ ] installare Instagram su entrambi, login fatto, **FastInputIME** come
      tastiera predefinita
- [ ] `IGBOT_PYTHON` se si lavora da un worktree
- [ ] `deploy/telegram_control.yml` da `telegram_control.example.yml`
- [ ] `.\deploy\install-autostart.ps1` da amministratore
- [ ] **autologon** di Windows (a mano)
- [ ] `powercfg /change standby-timeout-ac 0` e `hibernate-timeout-ac 0`
- [ ] verificare con CPU-Z: cache L3, canali di memoria, slot liberi

Sul codice:

- [ ] **ruotare la chiave API Groq** (passata in chat)
- [ ] opzionali mai attivati: `--analytics`, `--telegram-reports`,
      `--scrape-to-file`

## 8. Come lavoro su questo progetto

Convenzioni da mantenere:

- **Mai inventare username Instagram o resource-id**: si verificano con una
  ricerca o dumpando il device reale.
- **Testare l'artefatto, non il ragionamento**: quasi tutti i bug della
  sezione 5 sono stati trovati eseguendo il codice, non rileggendolo.
- **Niente riferimenti all'IA nei commit** (no `Co-Authored-By`, no Claude).
- Commenti e documentazione **in italiano**, come il resto del repo.
- I commenti spiegano *perché*, non *cosa*.

---

**Commit di questa sessione**, tutti su `robertobuonomo` e pushati:

```
34e37da  deploy: correzioni dopo la verifica sul mini PC di destinazione
be5bf96  deploy: diagnosi della macchina e accesso remoto a riga di comando
08c0733  deploy: fix encoding, ricerca venv, sfalsamento dei due account
dd908b1  deploy: infrastruttura per far girare i due account h24 sul mini PC
```
