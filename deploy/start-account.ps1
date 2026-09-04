<#
.SYNOPSIS
    Avvia l'emulatore di un account e poi il bot, aspettando che il device sia
    davvero pronto.

.DESCRIPTION
    Pensato per girare h24 su un mini PC. Fa tre cose in ordine:
      1. avvia l'AVD se non e' gia' in esecuzione
      2. aspetta che ADB lo veda come "device" E che il boot sia completo
         (sys.boot_completed: senza questo controllo il bot parte su un
         Android a meta' avvio e fallisce la prima sessione)
      3. lancia run-dynamic.py sul config dell'account

    NON contiene il loop di riavvio: se ne occupa watchdog.ps1, cosi' questo
    script resta semplice da lanciare anche a mano per un test.

.PARAMETER Account
    Nome cartella in accounts/ (es. rb.coach)

.PARAMETER Avd
    Nome dell'AVD da avviare (emulator -list-avds)

.PARAMETER Serial
    Serial ADB atteso, es. emulator-5554. Deve combaciare con il campo
    `device:` nel config.yml dell'account.

.EXAMPLE
    .\start-account.ps1 -Account rb.coach -Avd bot_rb -Serial emulator-5554
#>
param(
    [Parameter(Mandatory = $true)][string]$Account,
    [Parameter(Mandatory = $true)][string]$Avd,
    [Parameter(Mandatory = $true)][string]$Serial,
    [int]$BootTimeoutSec = 300,
    # Quante finestre di attivita' pianificare nella giornata.
    # Portato da 4 a 5 il 04/09: con 4 finestre da 90 min il bot lavorava 6 ore
    # su 16 disponibili (fascia 08:00-23:59), e i ritmi misurati erano a un
    # terzo dei limiti che Instagram tollera. Aggiungere tempo e' l'unica leva
    # che alza davvero i totali, perche' i tetti per sessione di follow e like
    # non venivano MAI raggiunti: il collo di bottiglia e' il tempo per
    # profilo, non il config.
    # 5 finestre richiedono (5-1)*2h + 1.5h = 9.5h, quindi entrano purche' il
    # bot parta entro le 14:30. Con 6 ne servirebbero 11.5 e un avvio nel
    # pomeriggio le farebbe comprimere.
    [int]$Sessioni = 5,
    # Emulatore senza finestra: risparmia CPU e memoria, ed e' quello che
    # serve su una macchina che gira h24 senza nessuno davanti. Il bot non
    # perde niente, perche' lavora via ADB e non guarda lo schermo; anche gli
    # screenshot di /shot continuano a funzionare (adb exec-out screencap).
    # Il default e' spento cosi' un test a mano mostra la finestra: in
    # produzione lo passa il watchdog.
    [switch]$NoWindow
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
. (Join-Path $PSScriptRoot 'common.ps1')

$LogDir = Join-Path $RepoRoot 'logs\deploy'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir "$Account.deploy.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

# --- individua gli strumenti dell'SDK (Get-SdkTool sta in common.ps1) -----
$Adb = Get-SdkTool 'platform-tools\adb.exe'
$Emulator = Get-SdkTool 'emulator\emulator.exe'

# Chiamate ad adb. Con $ErrorActionPreference = 'Stop', in PowerShell 5.1
# QUALSIASI riga che un eseguibile scrive su stderr diventa un errore
# terminante, e `2>$null` non lo evita. adb scrive su stderr proprio a freddo
# ("* daemon not running; starting now at tcp:5037"): dopo un riavvio del
# PC la prima `adb devices` uccideva questo script prima ancora di avviare
# l'emulatore, e il watchdog contava un crash. Qui stderr viene scartato e
# torna solo lo stdout.
function Invoke-Adb {
    param([string[]]$AdbArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Adb @AdbArgs 2>&1
        return @($out | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

# --- 1. l'emulatore e' gia' su? -------------------------------------------
# Il server adb parte qui, una volta, cosi' i messaggi di avvio non si
# mescolano all'elenco dei device.
Invoke-Adb @('start-server') | Out-Null

function Test-DeviceOnline {
    $out = Invoke-Adb @('devices')
    foreach ($l in $out) {
        if ($l -match "^$([regex]::Escape($Serial))\s+device") { return $true }
    }
    return $false
}

Write-Log "=== avvio account '$Account' (avd=$Avd serial=$Serial) ==="

if (Test-DeviceOnline) {
    Write-Log "$Serial gia' online, non riavvio l'emulatore."
}
else {
    # -no-snapshot-load: partire sempre da stato pulito evita il caso in cui lo
    #   snapshot contiene Instagram aperto in una schermata imprevista
    # -no-boot-anim / -no-audio: risparmio di CPU, su un mini PC conta
    # -gpu swiftshader_indirect: rendering software, l'unico affidabile senza
    #   GPU dedicata e in sessione headless
    $args = @(
        '-avd', $Avd,
        # -port lega il serial a QUESTO emulatore. Senza, il serial lo decide
        # l'ordine di avvio: se il primo muore e riparte mentre il secondo e'
        # gia' su, i due si scambiano emulator-5554 e emulator-5556, e ogni bot
        # si ritrova a lavorare sull'account dell'altro. Un errore che non da'
        # nessun messaggio, solo commenti pubblicati dal profilo sbagliato.
        '-port', ($Serial -replace '^emulator-', ''),
        '-no-snapshot-load',
        '-no-boot-anim',
        '-no-audio',
        '-gpu', 'swiftshader_indirect'
    )
    if ($NoWindow) { $args += '-no-window' }
    Write-Log "avvio emulatore: $Emulator $($args -join ' ')"
    Start-Process -FilePath $Emulator -ArgumentList $args -WindowStyle Minimized | Out-Null
}

# --- 2. attesa: device online E boot completato ---------------------------
$deadline = (Get-Date).AddSeconds($BootTimeoutSec)
$booted = $false
while ((Get-Date) -lt $deadline) {
    if (Test-DeviceOnline) {
        # "device" in adb non basta: Android puo' essere ancora in avvio
        $flag = (Invoke-Adb @('-s', $Serial, 'shell', 'getprop', 'sys.boot_completed') | Out-String).Trim()
        if ($flag -eq '1') { $booted = $true; break }
    }
    Start-Sleep -Seconds 5
}

if (-not $booted) {
    Write-Log "ERRORE: $Serial non e' arrivato a boot completo entro $BootTimeoutSec s."
    exit 1
}
Write-Log "$Serial pronto (boot completato)."

# lo schermo deve restare acceso e sbloccato, altrimenti il bot "non vede" nulla
Invoke-Adb @('-s', $Serial, 'shell', 'svc', 'power', 'stayon', 'true') | Out-Null
Invoke-Adb @('-s', $Serial, 'shell', 'input', 'keyevent', '82') | Out-Null

# --- 3. avvio del bot -----------------------------------------------------
# run-dynamic.py stampa emoji e caratteri di box-drawing. Qui sotto lo stdout
# finisce in una pipe, non in una console: senza questo Python su Windows usa
# cp1252 e muore con UnicodeEncodeError prima ancora di lanciare il bot.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
# ...e PowerShell deve leggere la pipe con la stessa codifica, altrimenti le
# emoji di run-dynamic finiscono nel log come "­ƒöî".
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$python = Get-BotPython
$config = "accounts/$Account/config.yml"

if (-not (Test-Path (Join-Path $RepoRoot $config))) {
    Write-Log "ERRORE: config non trovato: $config"
    exit 1
}

# Quante finestre generare nella giornata. run-dynamic.py le calcola dall'ora
# del lancio (la prima sessione parte subito) e le scrive nel config; con
# total-sessions: -1 GramAddict poi le ripete ogni giorno da solo.
# Fanno eccezione i config con il marcatore '# finestre-fisse' (quelli
# alternati): li' gli orari sono decisi a tavolino e run-dynamic non li tocca.
$runArgs = @('run-dynamic.py', '--config', $config, '--sessions', "$Sessioni")
Write-Log "finestre da generare al lancio: $Sessioni"

Write-Log ("lancio: $python " + ($runArgs -join ' '))
# Con $ErrorActionPreference = 'Stop' e 2>&1, PowerShell 5.1 trasforma la
# PRIMA riga che python scrive su stderr in un errore terminante: bastava
# l'avviso "pkg_resources is deprecated" di uiautomator2 per far morire
# questo script in silenzio prima di avviare il bot (22/08). Qui stderr e'
# solo testo da loggare, non un errore dello script.
$ErrorActionPreference = 'Continue'
& $python @runArgs 2>&1 | ForEach-Object {
    $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Output $line
}
$code = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
Write-Log "run-dynamic terminato con exit code $code"
exit $code
