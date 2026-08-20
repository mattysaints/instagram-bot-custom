# Deploy h24 su mini PC

Come tenere in piedi i due account 7 giorni su 7 su una macchina Windows, con
ripartenza automatica dopo un blackout e controllo da telefono.

| File | A cosa serve |
|---|---|
| `start-account.ps1` | avvia un emulatore, aspetta il boot vero, lancia il bot |
| `watchdog.ps1` | tiene vivi i due account e li rilancia quando escono |
| `remote_control.py` | bot Telegram: stato, stop/start, log, screenshot |
| `install-autostart.ps1` | registra i due task pianificati |
| `common.ps1` | funzioni condivise (ricerca del virtualenv) |

## Il vincolo da cui dipende tutto

**L'emulatore Android ha bisogno di una sessione desktop.** Un task pianificato
con "Esegui anche se l'utente non ha effettuato l'accesso" gira nella sessione 0,
che non ha desktop: l'emulatore non parte e non dà nemmeno un errore chiaro.

Per questo i task partono **al logon**, e serve che Windows faccia il login da
solo dopo un riavvio. Senza autologon, dopo un blackout il PC resta alla
schermata di accesso e il bot non riparte.

## Installazione

**1. Virtualenv e dipendenze** — vedi [SETUP.md](../SETUP.md). Gli script si
aspettano `.venv\` (o `venv\`) nella radice del repo. Se stai lavorando in un
**git worktree**, che non ha un venv proprio, indica quello del clone
principale:

```powershell
setx IGBOT_PYTHON "C:\...\instagram-bot-custom\.venv\Scripts\python.exe"
```

Non c'è fallback sul Python di sistema: girerebbe senza `uiautomator2` e
fallirebbe a metà sessione invece che subito, che su una macchina in un'altra
stanza è molto peggio da capire.

**2. Due AVD, uno per account.** Da Android Studio (Device Manager) o da riga
di comando. Su un mini PC con 4 core e 16 GB conviene tenerli leggeri:

| Impostazione | Valore | Perché |
|---|---|---|
| RAM per AVD | **2048 MB** | 2 × 2 GB lascia respiro a Windows e ai due Python |
| CPU cores | **2** | sono 4 in tutto: due a testa e nulla per il resto è troppo |
| Risoluzione | **720 × 1280** | meno pixel da comporre; il bot legge la UI, non guarda il video |
| Density | 320 dpi | coerente con 720p |
| Graphics | Software (`swiftshader_indirect`) | senza GPU dedicata è l'unica affidabile |
| Snapshot | disattivato | si riparte sempre da uno stato noto |

I nomi degli AVD attesi da `watchdog.ps1` sono `bot_rb` e `bot_pers`. Se ne usi
altri, cambiali nell'elenco `$Accounts` in cima al file.

**3. Allinea i serial.** Il primo emulatore avviato prende `emulator-5554`, il
secondo `emulator-5556`. Questi valori devono comparire nel campo `device:` dei
rispettivi config:

```
accounts/rb.coach/config.yml                 device: emulator-5554
accounts/roberto_buonomo_ifbbpro/config.yml  device: emulator-5556
```

**4. Instagram su ogni emulatore**, con l'account giusto già loggato, e la
tastiera FastInputIME impostata come predefinita (il bot la usa per scrivere).

**5. Controllo remoto Telegram:**

```
copy deploy\telegram_control.example.yml deploy\telegram_control.yml
```

Poi compila il file: token da [@BotFather](https://t.me/botfather), chat-id da
[@myidbot](https://t.me/myidbot). Solo quel chat-id può dare comandi.

**6. Registra l'autostart** (PowerShell **come amministratore**):

```powershell
cd deploy
.\install-autostart.ps1
```

**7. Autologon** — da fare a mano, una volta. Consigliato
[Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon),
che salva la password cifrata invece che in chiaro nel registro.

**8. Disattiva sospensione e ibernazione**, altrimenti gli emulatori si fermano:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

Lo schermo può spegnersi: è la sospensione del sistema che va evitata.

## Comandi Telegram

| Comando | Cosa fa |
|---|---|
| `/status` | stato dei due account, da quanto girano, errori consecutivi |
| `/stop rb` | mette in pausa un account |
| `/start rb` | lo riattiva |
| `/log rb 40` | ultime 40 righe di log |
| `/shot rb` | screenshot dell'emulatore |

Alias: `rb` → `rb.coach`, `pers` → `roberto_buonomo_ifbbpro`.

`/shot` è il comando che risolve più dubbi: se il bot sembra fermo, una
schermata dice subito se l'emulatore è bloccato, se Instagram ha aperto un
popup o se sta semplicemente scorrendo una lista.

Lo stop **non** uccide il watchdog: scrive un file `<account>.stop` che il
watchdog controlla a ogni giro. Sopravvive quindi anche a un riavvio: l'account
resta in pausa finché non mandi `/start`.

## Come si comporta quando qualcosa va storto

| Situazione | Reazione |
|---|---|
| Bot finisce le sessioni della giornata | riparte dopo 15 minuti |
| Bot crasha | backoff esponenziale: 30 min, 1 h, 2 h (tetto) |
| Emulatore piantato | `start-account.ps1` aspetta `sys.boot_completed`, se non arriva esce e il watchdog riprova |
| Blackout | autologon → task al logon → watchdog → tutto riparte |
| Instagram blocca l'account | i crash ripetuti allungano la pausa invece di insistere |

Il backoff è voluto: se Instagram ha messo un action-block, ritentare ogni due
minuti peggiora la situazione.

## Verifica senza aspettare un riavvio

```powershell
Start-ScheduledTask -TaskName IGBot-Watchdog
Start-ScheduledTask -TaskName IGBot-RemoteControl
Get-Content logs\deploy\watchdog.log -Wait -Tail 20
```

Poi da Telegram: `/status`, e `/shot rb` per vedere l'emulatore.

Per fermare tutto:

```powershell
Stop-ScheduledTask -TaskName IGBot-Watchdog
```

## Sul dimensionamento

Il mini PC monta un **Ryzen 5 3500U** (4 core / 8 thread, 2019) con 16 GB.
Con i parametri qui sopra due emulatori ci stanno, ma **è il limite della
macchina**, non una configurazione comoda: sono 2 core per emulatore e quasi
niente per Windows, i due processi Python e ADB. Aspettati sessioni più lente
di quelle su un telefono vero, e qualche timeout in più sulla lettura della UI.

**Gli account sono già sfalsati.** In `watchdog.ps1` il secondo parte 90 minuti
dopo il primo (`OffsetMin`). Le sessioni durano 90 minuti e distano 3 ore, cioè
ogni account lavora metà del tempo: con quello scarto si alternano invece di
pestarsi i piedi.

Non serve invece toccare `working-hours` nei config: quella riga viene
**riscritta da `run-dynamic.py` a ogni lancio**, calcolata sull'ora di
partenza. Conta quando il watchdog lancia, non cosa c'è scritto nel file.

Se l'uso h24 desse comunque problemi, in ordine di efficacia:

1. alzare `OffsetMin` del secondo account se vedi che si accavallano
2. scendere a **1 core per AVD**, più lento ma più stabile
3. passare a due telefoni Android fisici collegati in USB: il mini PC
   diventerebbe solo il controller e il carico crollerebbe
