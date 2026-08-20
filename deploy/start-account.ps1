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

# --- individua gli strumenti dell'SDK -------------------------------------
function Get-SdkTool {
    param([string]$Relative)
    $roots = @(
        $env:ANDROID_HOME,
        $env:ANDROID_SDK_ROOT,
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
    ) | Where-Object { $_ -and (Test-Path $_) }
    foreach ($r in $roots) {
        $candidate = Join-Path $r $Relative
        if (Test-Path $candidate) { return $candidate }
    }
    # ultimo tentativo: gia' nel PATH
    $leaf = Split-Path -Leaf $Relative
    $cmd = Get-Command $leaf -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Non trovo $Relative. Imposta ANDROID_HOME o aggiungi l'SDK al PATH."
}

$Adb = Get-SdkTool 'platform-tools\adb.exe'
$Emulator = Get-SdkTool 'emulator\emulator.exe'

# --- 1. l'emulatore e' gia' su? -------------------------------------------
function Test-DeviceOnline {
    $out = & $Adb devices 2>$null
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
        $flag = (& $Adb -s $Serial shell getprop sys.boot_completed 2>$null | Out-String).Trim()
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
& $Adb -s $Serial shell svc power stayon true 2>$null | Out-Null
& $Adb -s $Serial shell input keyevent 82 2>$null | Out-Null

# --- 3. avvio del bot -----------------------------------------------------
# run-dynamic.py stampa emoji e caratteri di box-drawing. Qui sotto lo stdout
# finisce in una pipe, non in una console: senza questo Python su Windows usa
# cp1252 e muore con UnicodeEncodeError prima ancora di lanciare il bot.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$python = Get-BotPython
$config = "accounts/$Account/config.yml"

if (-not (Test-Path (Join-Path $RepoRoot $config))) {
    Write-Log "ERRORE: config non trovato: $config"
    exit 1
}

# run-dynamic.py genera di default 5 finestre, ma GramAddict si ferma dopo
# `total-sessions`. Se i due numeri non coincidono la giornata viene spalmata
# su piu' ore del necessario: passiamo il valore vero letto dal config.
$runArgs = @('run-dynamic.py', '--config', $config)
$totalSessions = Select-String -Path (Join-Path $RepoRoot $config) `
    -Pattern '^\s*total-sessions\s*:\s*(\d+)' | Select-Object -First 1
if ($totalSessions) {
    $n = $totalSessions.Matches[0].Groups[1].Value
    $runArgs += @('--sessions', $n)
    Write-Log "total-sessions dal config: $n"
}

Write-Log ("lancio: $python " + ($runArgs -join ' '))
& $python @runArgs 2>&1 | ForEach-Object {
    Add-Content -Path $LogFile -Value $_ -Encoding utf8
    Write-Output $_
}
$code = $LASTEXITCODE
Write-Log "run-dynamic terminato con exit code $code"
exit $code
