# Deploy h24 su mini PC

Come tenere in piedi i due account 7 giorni su 7 su una macchina Windows, con
ripartenza automatica dopo un blackout e controllo da telefono.

| File | A cosa serve |
|---|---|
| `check-machine.ps1` | **da lanciare per primo**: dice se la macchina ce la fa |
| `install-instagram.ps1` | installa sugli emulatori la versione di Instagram su cui il bot è tarato |
| `start-account.ps1` | avvia un emulatore, aspetta il boot vero, lancia il bot |
| `watchdog.ps1` | tiene vivi i due account e li rilancia quando escono |
| `remote_control.py` | bot Telegram: stato, stop/start, log, screenshot, `/cmd` |
| `install-autostart.ps1` | registra i due task pianificati |
| `install-remote-shell.ps1` | SSH + Tailscale: terminale vero da remoto |
| `install-remote-desktop.ps1` | desktop remoto (RDP) limitato alla rete privata |
| `view-emulator.ps1` | apre lo schermo di un emulatore e lo rende cliccabile |
| `status.ps1` | riassunto in una schermata: task, emulatori, account, log |
| `common.ps1` | funzioni condivise (virtualenv, strumenti dell'SDK) |

## Il vincolo da cui dipende tutto

**L'emulatore Android ha bisogno di una sessione desktop.** Un task pianificato
con "Esegui anche se l'utente non ha effettuato l'accesso" gira nella sessione 0,
che non ha desktop: l'emulatore non parte e non dà nemmeno un errore chiaro.

Per questo i task partono **al logon**, e serve che Windows faccia il login da
solo dopo un riavvio. Senza autologon, dopo un blackout il PC resta alla
schermata di accesso e il bot non riparte.

### Il nodo password contro autologon

I due requisiti — *entrare da remoto* e *ripartire da solo* — tirano la
password in direzioni opposte, e conviene deciderlo prima di installare
qualsiasi cosa.

| | account **senza** password | account **con** password |
|---|---|---|
| Logon automatico all'avvio | sì, Windows lo fa da solo | no, va configurato |
| RDP | **rifiutato da Windows** | funziona |
| SSH con password | rifiutato | funziona |
| SSH con chiave | funziona | funziona |

Windows blocca gli accessi *di rete* agli account con password vuota: è una
policy predefinita, e il collegamento fallisce **senza spiegare il motivo**.
Chi cerca la causa nel firewall o in Tailscale ci perde un pomeriggio.

La configurazione da tenere è quindi **password + autologon**, in
quest'ordine, mai una sola delle due:

```powershell
net user %USERNAME% *          # 1. metti la password
                               # 2. Sysinternals Autologon, subito dopo
```

[Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon)
la salva cifrata (LSA secret) invece che in chiaro nel registro, che è quello
che farebbe `netplwiz`.

Se salti il passo 2, dopo un blackout il mini PC si riaccende ma resta fermo
alla schermata di accesso: nessuna sessione desktop, nessun emulatore, e il bot
non lavora finché qualcuno non entra fisicamente o via RDP.

E c'è un gradino ancora prima, che nessuno script può toccare: **l'accensione
automatica al ritorno della corrente** si imposta nel BIOS/UEFI, voce *Restore
on AC Power Loss* / *AC Back*, da mettere su **Power On**. Senza quella, dopo
un blackout il mini PC resta semplicemente spento.

## Prima di tutto: la macchina ce la fa?

Appena hai il mini PC in mano, prima di installare qualsiasi cosa:

```powershell
.\deploy\check-machine.ps1
```

Legge e basta, non modifica niente. Controlla CPU, RAM, disco, **virtualizzazione
abilitata nel BIOS**, edizione di Windows, impostazioni di sospensione e
strumenti Android, e alla fine ti dà un elenco di cosa risolvere.

Il controllo che conta più di tutti è la virtualizzazione: se è spenta nel
BIOS/UEFI (voce **SVM Mode** su AMD, **Intel VT-x** su Intel), l'emulatore
ripiega sull'emulazione software ed è di fatto inutilizzabile. È una cosa che
dalla scheda del venditore non si può sapere.

## Installazione

**1. Virtualenv e dipendenze** — vedi [SETUP.md](../SETUP.md). Sul mini PC è
già fatto: **Python 3.12.10** e `.venv` nella radice del repo. Non salire a
3.13+ senza prima leggere la nota sulle pin in SETUP.md. Gli script si
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
| **Livello API** | **36 o inferiore** | vedi l'avvertenza qui sotto: è la scelta più importante |
| RAM per AVD | **2048 MB** | 2 × 2 GB lascia respiro a Windows e ai due Python |
| CPU cores | **2** | sono 4 in tutto: due a testa e nulla per il resto è troppo |
| Risoluzione | **720 × 1280** | meno pixel da comporre; il bot legge la UI, non guarda il video |
| Density | 320 dpi | coerente con 720p |
| Graphics | Software (`swiftshader_indirect`) | senza GPU dedicata è l'unica affidabile |
| Snapshot | disattivato | si riparte sempre da uno stato noto |
| Immagine | **senza Google Play** | i Play Services consumano RAM e CPU anche da fermi |

> **Non usare immagini API 37 o superiori.** Da quel livello l'emulatore impone
> un minimo di **4 GB di RAM per AVD** e **riscrive la tua configurazione senza
> dirtelo**: i 2048 MB che hai messo diventano 4096, e due emulatori non ci
> stanno più in 16 GB. È il tipo di problema che si manifesta come lentezza
> inspiegabile, non come errore.

Metti in conto anche il disco: una cartella AVD in esercizio arriva
tranquillamente a **~30 GB**, quindi due sono ~60 GB. E l'emulatore si rifiuta
di partire con meno di 5 GB liberi.

> **Sul mini PC gli AVD esistono già e si chiamano `rbcoach` (→ `rb.coach`,
> `emulator-5554`) e `robertobuo` (→ `roberto_buonomo_ifbbpro`,
> `emulator-5556`).** `watchdog.ps1` è già allineato a questi nomi. Su
> entrambi c'è Instagram 300.0.0.29.110, il login è fatto e FastInputIME è la
> tastiera predefinita. Il 22/08/2026 sono stati riportati ai valori della
> tabella (2 core, 720×1280 a 320 dpi, `swiftshader_indirect`, cold boot): con
> 4 core e 1080×2280 l'uno, i due insieme chiedevano il doppio della CPU
> disponibile e il 5556 era morto da solo mentre si limitava ad aprire profili.
> Dopo la modifica il boot di entrambi in parallelo è sceso da 3-4 minuti a
> 80 secondi. I vecchi `config.ini` sono salvati accanto come
> `config.ini.bak-2026-08-22`.

I nomi degli AVD attesi da `watchdog.ps1` erano `bot_rb` e `bot_pers`. Se ne usi
altri, cambiali nell'elenco `$Accounts` in cima al file.

**3. Allinea i serial.** Il primo emulatore avviato prende `emulator-5554`, il
secondo `emulator-5556`. Questi valori devono comparire nel campo `device:` dei
rispettivi config:

```
accounts/rb.coach/config.yml                 device: emulator-5554
accounts/roberto_buonomo_ifbbpro/config.yml  device: emulator-5556
```

**4. Instagram su ogni emulatore** — e dev'essere **la versione giusta**, non
l'ultima.

Il bot legge la UI di Instagram tramite i resource-id interni dell'app, che
cambiano a ogni aggiornamento. I resource-id di questo fork sono verificati su
**300.0.0.29.110** (`__tested_ig_version__` in `GramAddict/__init__.py`). Con la
428.0.0.0.4 il bot non riusciva nemmeno a leggere un profilo — è la prima riga
di `COMMIT_CHANGELOG.txt`.

L'APK va scaricato a mano da [APKMirror](https://www.apkmirror.com/apk/instagram/instagram-instagram/),
scegliendo la variante **nodpi** con l'**architettura dell'emulatore**
(`adb shell getprop ro.product.cpu.abilist`; su un'immagine x86 a 32 bit non
c'è nessun ponte ARM, quindi un APK arm non gira). Lo script non scarica nulla
di proposito: Instagram riceve le credenziali del cliente, e un APK preso da
una fonte qualsiasi è il modo più diretto per farsi rubare l'account.

Poi, con gli emulatori accesi:

```powershell
.\deploy\install-instagram.ps1 -Apk C:\percorso\instagram-300-x86.apk -BloccaAggiornamenti
```

Lo script controlla package, versione, architettura e livello API prima di
provare a installare, stampa l'impronta SHA-256 della firma (da confrontare con
quella che APKMirror mostra sulla pagina di download) e verifica la versione sul
device a installazione fatta. Senza `-Apk` cerca in `deploy\apk\` e in Download.

`-BloccaAggiornamenti` disattiva il Play Store sul device. Serve solo sulle
immagini `google_apis_playstore`, dove altrimenti Instagram si aggiorna da solo
e il giorno dopo il bot non trova più i resource-id: un guasto che si presenta
come «ha smesso di funzionare da solo», senza nessun errore che punti alla causa.

Restano da fare a mano, su ogni emulatore: il **login** con l'account giusto e
la tastiera **FastInputIME** come predefinita (il bot la usa per scrivere).

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
(`install-remote-desktop.ps1` fa già questi tre comandi.)

**9. Accesso da remoto**, in ordine di utilità:

```powershell
.\deploy\install-remote-shell.ps1   -InstallTailscale   # terminale (SSH)
.\deploy\install-remote-desktop.ps1                     # schermo (RDP)
.\deploy\view-emulator.ps1 -Account rb -Installa        # schermo dell'emulatore
```

Il primo dei due che installa Tailscale basta: il login va poi fatto a mano
dall'app, con lo **stesso account** anche sull'altro PC. Dettagli e scelta
dello strumento giusto nella sezione
[Vedere e toccare il mini PC dall'altro PC](#vedere-e-toccare-il-mini-pc-dallaltro-pc).

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

## Lanciare comandi sul mini PC da remoto

Due strade, che servono a cose diverse.

### `/cmd` da Telegram — per il comando al volo

Comodo dal telefono, zero installazione. **Spento di default**: si accende in
`deploy/telegram_control.yml` con `allow-shell: true`.

```
/cmd adb devices
/cmd Get-Process emulator
/cmd git -C C:\...\instagram-bot pull
```

Gira attraverso PowerShell, con la directory del repo come cartella corrente.
Tetto di 2 minuti, output tagliato prima del limite di Telegram, ogni comando
scritto nel log.

**Cosa stai accendendo**: è esecuzione di comandi arbitrari sulla macchina.
Solo il tuo chat-id è autorizzato, ma chi entra nel tuo account Telegram si
prende il mini PC. Se la cosa non ti convince, lascialo spento e usa SSH.

### SSH — per il terminale vero

`/cmd` non è un terminale: niente comandi interattivi, niente output in tempo
reale, niente copia di file. Per quello:

```powershell
.\deploy\install-remote-shell.ps1 -InstallTailscale
```

Installa il server **OpenSSH già incluso in Windows 11** (non scarica niente da
terzi), lo mette in avvio automatico, apre la porta 22 **solo sul firewall
locale**, e installa **Tailscale**.

Tailscale è il pezzo che risolve il problema vero: crea una rete privata tra i
tuoi dispositivi, così il mini PC è raggiungibile da fuori casa **senza aprire
porte sul router** e senza IP pubblico. Fai il login con lo stesso account su
mini PC e telefono, e poi:

```
ssh utente@nome-del-minipc
```

Da telefono va bene Termius o JuiceSSH.

**Non aprire la porta 22 sul router.** SSH esposto su internet raccoglie
tentativi di accesso automatici in continuazione.

Meglio ancora, con le chiavi invece della password:

```powershell
.\deploy\install-remote-shell.ps1 -PublicKey "ssh-ed25519 AAAA... tuo@portatile"
```

Lo script mette la chiave nel posto giusto — per gli utenti amministratori
Windows **non** usa `~/.ssh/authorized_keys` ma
`%ProgramData%\ssh\administrators_authorized_keys`, con permessi ristretti;
sbagliare posto o permessi fa fallire l'accesso **in silenzio**, ed è l'errore
più comune. Solo **dopo** aver verificato che entri senza password, aggiungi
`-DisablePasswordAuth` (senza una chiave autorizzata lo script si rifiuta di
farlo, per non chiuderti fuori).

## Vedere e toccare il mini PC dall'altro PC

SSH e Telegram coprono i *comandi*. Restano fuori le cose che si fanno solo con
le mani: un popup di Instagram da chiudere, un login scaduto da rifare, un
aggiornamento da rifiutare. Per quelle servono due strumenti diversi, perché
sono due schermi diversi.

### Il desktop di Windows — `install-remote-desktop.ps1`

```powershell
.\deploy\install-remote-desktop.ps1 -InstallaTailscale
```

Accende il Desktop remoto già incluso in Windows 11 Pro, tiene attiva
l'autenticazione a livello di rete (NLA), **limita le regole del firewall alla
rete locale e alla rete Tailscale** (`100.64.0.0/10`) e disattiva sospensione,
ibernazione e spegnimento dei dischi. Prima di tutto questo controlla che
l'account abbia una password: senza, si ferma e spiega perché (vedi il nodo
password ↔ autologon qui sopra).

Poi, dall'altro PC:

```powershell
mstsc /v:100.x.y.z          # indirizzo Tailscale del mini PC
```

Perché RDP e non VNC: è già dentro Windows, il traffico è cifrato, il client
c'è ovunque (anche come app Android e iOS). L'unica differenza da sapere è che
collegandosi la sessione viene *portata via* dal monitor fisico, che torna alla
schermata di accesso — sul mini PC non è un problema, nessuno ci sta davanti, e
i processi continuano a girare anche dopo che ti scolleghi.

Quando il collegamento via Tailscale funziona, si può stringere:

```powershell
.\deploy\install-remote-desktop.ps1 -SoloTailscale
```

che toglie anche la rete locale dall'ambito. **La 3389 non va aperta sul
router** in nessun caso.

### Lo schermo dell'emulatore — `view-emulator.ps1`

Il desktop remoto da solo non basta, e per un motivo che sfugge: in produzione
gli emulatori partono con `-no-window`, quindi **non c'è nessuna finestra da
guardare** nemmeno collegandosi in RDP. Lo schermo Android si prende da ADB:

```powershell
.\deploy\view-emulator.ps1 -Account rb -Installa
.\deploy\view-emulator.ps1 -Account pers -SolaLettura
```

Apre `scrcpy` sull'emulatore dell'account: si vede quello che il bot sta
facendo **e ci si può cliccare sopra**. Il serial non è scritto nello script:
viene letto dal campo `device:` di `accounts/<account>/config.yml`, che è la
fonte autorevole, così non c'è un terzo elenco da tenere allineato a mano.
`-SolaLettura` guarda senza poter toccare, per non rischiare un click mentre il
bot lavora.

`scrcpy` deve disegnare una finestra, quindi va lanciato **dentro una sessione
grafica**: davanti al mini PC o dentro RDP. Da SSH non ha nessuno schermo su
cui disegnare.

In alternativa lo si fa girare **sull'altro PC**, portandosi dietro il server
ADB con un tunnel SSH — così il rendering lo paga l'altro PC e il video viaggia
cifrato:

```bash
ssh -L 5037:localhost:5037 Roberto@mini-pc
scrcpy -s emulator-5554
```

### Il colpo d'occhio — `status.ps1`

```powershell
ssh Roberto@mini-pc "powershell -File C:\...\deploy\status.ps1 -Righe 30"
```

Risponde in una schermata alle domande che ci si fa da lontano: da quanto è
acceso, i task sono partiti, ADB vede gli emulatori, i due account girano o
sono in pausa o in backoff, e cosa hanno scritto nei log. Legge e basta.
Con `-Continuo` si aggiorna da solo e si può lasciare aperto in un angolo.

### Quale strumento per cosa

| Ti serve | Strumento |
|---|---|
| Sapere se sta girando, dal telefono | `/status`, `/shot` su Telegram |
| Sapere se sta girando, dall'altro PC | `status.ps1` via SSH |
| Leggere log, aggiornare il repo, riavviare un task | SSH |
| Chiudere un popup di Instagram, rifare un login | `view-emulator.ps1` |
| Creare un AVD, usare Android Studio, sistemare Windows | RDP |

## Come si comporta quando qualcosa va storto

| Situazione | Reazione |
|---|---|
| Bot finisce le sessioni della giornata | con `total-sessions: -1` (i due account) non succede: il bot dorme fino alla prima finestra del mattino e riparte da solo; con un numero finito di sessioni il processo esce e il watchdog lo rilancia dopo 15 minuti |
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

Il mini PC di riferimento (Huidun H80, ASIN Amazon `B0DTHW462L`) dichiara 16 GB
e una CPU che l'inserzione italiana **non nomina**: "H80" è il nome del mini PC,
non un modello AMD. Dai listing gemelli americani risulta un **Ryzen 5 3500U**
(4 core / 8 thread, gennaio 2019), ma è una deduzione, non un dato letto sulla
pagina d'acquisto — quindi va verificata sulla macchina con CPU-Z, controllando
che la cache L3 sia di **4 MB**, che è il valore della 3500U.

> **Verificato sulla macchina il 21/08/2026.** `Win32_Processor` riporta
> `AMD Ryzen 5 3500U`, 4 core / 8 thread, **L3 = 4096 KB**: la deduzione era
> giusta e la CPU è quella dichiarata dai listing americani. Windows 11 Pro,
> 16 GB installati (14 GB utilizzabili, il resto va alla grafica integrata).

Con i parametri qui sopra due emulatori ci stanno, ma **è il limite della
macchina**, non una configurazione comoda: sono 2 core per emulatore e quasi
niente per Windows, i due processi Python e ADB. Aspettati sessioni più lente
di quelle su un telefono vero, e qualche timeout in più sulla lettura della UI.

Numeri utili per capire quanto è stretto: Google indica **~4 GB per ogni AVD in
più** in esecuzione e consiglia **32 GB** per più emulatori simultanei. Il
conto realistico su 16 GB è 2 × ~4,4 GB di commit + i due Python + 3-5 GB di
Windows = **12,5-14,5 GB**. Non è il caso peggiore: è lo scenario normale.

C'è poi un rischio da verificare subito, perché deciderebbe se tenere la
macchina: se i 16 GB sono su **un solo modulo**, il sistema lavora in *single
channel* e dimezza la banda verso la grafica integrata — che è esattamente
quello che l'emulatore usa. `check-machine.ps1` te lo dice ("Banchi di
memoria"). Con uno slot libero, aggiungere un secondo modulo uguale è la
modifica col miglior rapporto costo/beneficio di tutte.

> **Verificato: il rischio si è avverato.** C'è **un solo modulo** da 16 GB
> (banco `P0 CHANNEL A`, DDR4-2667), quindi la macchina lavora in single
> channel. Aggiungere un secondo modulo identico nell'altro slot raddoppia la
> banda verso la grafica integrata ed è, a oggi, l'unico intervento hardware
> che valga la spesa su questa macchina.

**Gli account sono già sfalsati, nei config.** I due `config.yml` hanno
`total-sessions: -1` e **finestre fisse** alternate dentro la fascia
consentita **08:00-23:00**, nove sessioni da 75 minuti in tutto:

| | rb.coach | roberto_buonomo_ifbbpro |
|---|---|---|
| 1 | 08:10-09:25 | 09:50-11:05 |
| 2 | 11:30-12:45 | 13:10-14:25 |
| 3 | 14:50-16:05 | 16:30-17:45 |
| 4 | 18:10-19:25 | 19:50-21:05 |
| 5 | 21:30-22:45 | — |

Fra una finestra e la successiva ci sono 25 minuti; `time-delta: 0-10` sposta
gli orari di ±10 minuti al giorno, quindi nemmeno nel caso peggiore i due
emulatori si sovrappongono, la giornata non inizia prima delle 08:00 e non
finisce dopo le 23:00. Ogni finestra vale una sessione sola: se finisce prima
per i limiti, il bot aspetta la finestra dopo; la sera dorme e riparte la
mattina senza che nessuno lo rilanci. Con `-1` nel config `run-dynamic.py`
**non riscrive** `working-hours` (modalità `--fixed-hours`): per cambiare gli
orari si modifica il file e si riavvia il bot.

`OffsetMin` in `watchdog.ps1` (il secondo parte 90 minuti dopo il primo)
resta utile solo al primo avvio; il ritmo giornaliero lo danno le finestre.
La modalità storica (finestre generate dall'ora di lancio, N sessioni e poi
il processo esce) resta attiva per i config con `total-sessions: N`, come
quelli di smoke.

Se l'uso h24 desse comunque problemi, in ordine di efficacia:

1. allargare gli intervalli tra le finestre dei due account nei `config.yml` se vedi che si accavallano
2. scendere a **1 core per AVD**, più lento ma più stabile
3. passare a due telefoni Android fisici collegati in USB: il mini PC
   diventerebbe solo il controller e il carico crollerebbe
