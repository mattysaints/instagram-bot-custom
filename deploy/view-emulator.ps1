<#
.SYNOPSIS
    Apre lo schermo di un emulatore e lo rende usabile con mouse e tastiera.

.DESCRIPTION
    In produzione gli emulatori partono con -no-window (watchdog.ps1): non
    esiste nessuna finestra da guardare, nemmeno collegandosi in desktop
    remoto. Questo script usa scrcpy, che prende lo schermo dal lato Android
    via ADB e quindi funziona lo stesso, e in piu' lascia intervenire: chiudere
    un popup, rifare un login, accettare un aggiornamento.

    E' il modo con cui si "vede il bot mentre lavora". Il costo in CPU e' basso
    finche' la finestra e' aperta, ma resta CPU sottratta agli emulatori: da
    tenere aperto quando serve, non tutto il giorno.

    DOVE VA LANCIATO
    scrcpy deve disegnare una finestra, quindi va lanciato DENTRO una sessione
    grafica: davanti al mini PC oppure dentro il desktop remoto
    (install-remote-desktop.ps1). Da una sessione SSH non ha nessuno schermo su
    cui disegnare e fallisce.

    In alternativa lo si puo' far girare SULL'ALTRO PC, portandosi dietro il
    server ADB del mini PC con un tunnel SSH:

        # sull'altro PC
        ssh -L 5037:localhost:5037 Roberto@<mini-pc>
        # e in un secondo terminale, sempre sull'altro PC
        scrcpy -s emulator-5554

    Cosi' il video viaggia dentro il tunnel cifrato e la CPU del rendering la
    mette l'altro PC.

.PARAMETER Account
    Nome dell'account (o un alias: rb, coach, pers, roberto). Il serial viene
    letto dal campo `device:` di accounts/<account>/config.yml, che e' la
    fonte autorevole: cosi' non c'e' un terzo elenco di serial da tenere
    allineato a mano.

.PARAMETER Serial
    Serial ADB esplicito, se non si vuole passare da un account.

.PARAMETER Elenco
    Elenca i device che ADB vede e basta.

.PARAMETER SolaLettura
    Guarda senza poter toccare. Utile per non rischiare un click involontario
    mentre il bot sta lavorando.

.PARAMETER Installa
    Installa scrcpy via winget se manca.

.EXAMPLE
    .\view-emulator.ps1 -Account rb

.EXAMPLE
    .\view-emulator.ps1 -Account pers -SolaLettura
#>
param(
    [string]$Account,
    [string]$Serial,
    [switch]$Elenco,
    [switch]$SolaLettura,
    [switch]$Installa,
    [int]$MaxSize = 720,
    [string]$Bitrate = '2M'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')

# Gli stessi alias di remote_control.py, cosi' i comandi si digitano allo
# stesso modo da Telegram e da qui.
$ALIAS = @{
    'rb'        = 'rb.coach'
    'coach'     = 'rb.coach'
    'pers'      = 'roberto_buonomo_ifbbpro'
    'personale' = 'roberto_buonomo_ifbbpro'
    'roberto'   = 'roberto_buonomo_ifbbpro'
}

$Adb = Get-SdkTool 'platform-tools\adb.exe'

# --- elenco dei device ------------------------------------------------------
if ($Elenco) {
    & $Adb devices -l
    exit 0
}

# --- risoluzione account -> serial -----------------------------------------
if (-not $Serial) {
    if (-not $Account) {
        Write-Warning 'Serve -Account (es. rb, pers) oppure -Serial.'
        exit 1
    }
    $nome = $Account
    if ($ALIAS.ContainsKey($Account.ToLower())) { $nome = $ALIAS[$Account.ToLower()] }

    $config = Join-Path $RepoRoot "accounts\$nome\config.yml"
    if (-not (Test-Path $config)) {
        Write-Warning "Non trovo $config"
        exit 1
    }
    $riga = Select-String -Path $config -Pattern '^\s*device\s*:\s*(\S+)' | Select-Object -First 1
    if (-not $riga) {
        Write-Warning "Nessun campo 'device:' in $config"
        exit 1
    }
    $Serial = $riga.Matches[0].Groups[1].Value
    Write-Output "account '$nome' -> $Serial"
}

# --- il device c'e'? --------------------------------------------------------
$online = $false
foreach ($l in (& $Adb devices)) {
    if ($l -match "^$([regex]::Escape($Serial))\s+device") { $online = $true }
}
if (-not $online) {
    Write-Warning "$Serial non risulta online. Device visti da ADB:"
    & $Adb devices
    Write-Output ''
    Write-Output "L'emulatore lo avvia il watchdog. Per uno al volo:"
    Write-Output "    .\deploy\start-account.ps1 -Account <nome> -Avd <avd> -Serial $Serial"
    exit 1
}

# --- scrcpy -----------------------------------------------------------------
$scrcpy = Get-Command scrcpy -ErrorAction SilentlyContinue
if (-not $scrcpy -and $Installa) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Warning 'winget non disponibile: scarica scrcpy da https://github.com/Genymobile/scrcpy/releases'
        exit 1
    }
    & winget install --id Genymobile.scrcpy --accept-source-agreements --accept-package-agreements
    # winget aggiorna il PATH del processo solo alla prossima sessione
    $scrcpy = Get-Command scrcpy -ErrorAction SilentlyContinue
    if (-not $scrcpy) {
        Write-Output ''
        Write-Output 'Installato. Chiudi e riapri PowerShell (il PATH si aggiorna li), poi rilancia.'
        exit 0
    }
}
if (-not $scrcpy) {
    Write-Warning 'scrcpy non installato. Rilancia con -Installa, oppure:'
    Write-Output '    winget install --id Genymobile.scrcpy'
    exit 1
}

# I nomi delle opzioni sono cambiati con la 2.0: --bit-rate e' diventato
# --video-bit-rate, e prima della 2.0 --no-audio non esiste proprio. Passare
# l'opzione sbagliata fa uscire scrcpy con un errore di sintassi, quindi la
# versione va guardata invece che data per scontata.
$versione = (& $scrcpy.Source --version 2>&1 | Select-Object -First 1) -as [string]
$major = 2
if ($versione -match 'scrcpy\s+(\d+)\.(\d+)') { $major = [int]$Matches[1] }

$argomenti = @('-s', $Serial, '--max-size', "$MaxSize", '--window-title', "IGBot $Serial")
if ($major -ge 2) {
    $argomenti += @('--video-bit-rate', $Bitrate, '--no-audio')
}
else {
    $argomenti += @('--bit-rate', $Bitrate)
}
if ($SolaLettura) {
    $argomenti += '--no-control'
}
else {
    # tiene lo schermo acceso finche' la finestra e' aperta: uno schermo che si
    # spegne a meta' sessione manda in errore il bot
    $argomenti += '--stay-awake'
}

Write-Output ("scrcpy " + ($argomenti -join ' '))
Write-Output 'Chiudi la finestra per staccarti: il bot continua a lavorare.'
& $scrcpy.Source @argomenti
