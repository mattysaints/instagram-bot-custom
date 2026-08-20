<#
.SYNOPSIS
    Registra watchdog e controllo remoto perche' ripartano da soli dopo un
    riavvio (anche dopo un blackout).

.DESCRIPTION
    VINCOLO IMPORTANTE, da cui dipende tutto il resto:
    l'emulatore Android ha bisogno di una SESSIONE DESKTOP INTERATTIVA.
    Un task "Esegui anche se l'utente non ha effettuato l'accesso" gira nella
    sessione 0, che non ha desktop: l'emulatore non parte e non da' nemmeno un
    errore chiaro.

    Per questo i task sono registrati con trigger AL LOGON, e serve che
    Windows faccia il login da solo all'avvio (autologon). Senza autologon,
    dopo un blackout il PC resta alla schermata di accesso e il bot non parte.

    Questo script:
      1. registra due task pianificati con trigger "at logon" dell'utente
         corrente (watchdog + controllo remoto Telegram)
      2. NON configura l'autologon: va fatto a mano, vedi sotto

    AUTOLOGON (da fare una volta, a mano)
      Consigliato: Sysinternals Autologon
        https://learn.microsoft.com/sysinternals/downloads/autologon
      Salva la password in forma cifrata (LSA secret), non in chiaro nel
      registro come farebbe la procedura manuale.
      In alternativa: netplwiz -> togliere la spunta "Gli utenti devono
      immettere nome utente e password".

      Nota di sicurezza: l'autologon significa che chiunque abbia accesso
      fisico al mini PC si trova il desktop sbloccato. Su una macchina che sta
      in un ufficio o in uno studio va messa in un posto non accessibile.

.EXAMPLE
    # da PowerShell come amministratore
    .\install-autostart.ps1

.EXAMPLE
    .\install-autostart.ps1 -Remove     # rimuove i task
#>
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$User = "$env:USERDOMAIN\$env:USERNAME"

$TaskWatchdog = 'IGBot-Watchdog'
$TaskRemote = 'IGBot-RemoteControl'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Warning 'Esegui questo script da un PowerShell APERTO COME AMMINISTRATORE.'
    exit 1
}

if ($Remove) {
    foreach ($t in @($TaskWatchdog, $TaskRemote)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
            Write-Output "rimosso: $t"
        }
    }
    exit 0
}

# --- controlli preliminari, meglio fallire adesso che al prossimo blackout ---
$venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Warning "Non trovo $venvPython. Crea prima il virtualenv (vedi SETUP.md)."
    exit 1
}
$controlCfg = Join-Path $RepoRoot 'deploy\telegram_control.yml'
if (-not (Test-Path $controlCfg)) {
    Write-Warning "Manca deploy\telegram_control.yml: il controllo remoto non partira'."
    Write-Warning 'Copia telegram_control.example.yml e mettici token e chat-id.'
}

# --- 1. watchdog ------------------------------------------------------------
$watchdogAction = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"" +
               (Join-Path $PSScriptRoot 'watchdog.ps1') + "`"") `
    -WorkingDirectory $RepoRoot

# ritardo di 2 minuti: dopo il boot servono qualche secondo di rete e servizi
$watchdogTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$watchdogTrigger.Delay = 'PT2M'

# StartWhenAvailable + nessun limite di durata: deve girare per sempre
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskWatchdog -Action $watchdogAction `
    -Trigger $watchdogTrigger -Settings $settings -RunLevel Highest `
    -User $User -Force | Out-Null
Write-Output "registrato: $TaskWatchdog (al logon, +2 min)"

# --- 2. controllo remoto Telegram ------------------------------------------
$remoteAction = New-ScheduledTaskAction `
    -Execute $venvPython `
    -Argument ("`"" + (Join-Path $PSScriptRoot 'remote_control.py') + "`"") `
    -WorkingDirectory $RepoRoot

$remoteTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$remoteTrigger.Delay = 'PT1M'

Register-ScheduledTask -TaskName $TaskRemote -Action $remoteAction `
    -Trigger $remoteTrigger -Settings $settings -RunLevel Highest `
    -User $User -Force | Out-Null
Write-Output "registrato: $TaskRemote (al logon, +1 min)"

Write-Output ''
Write-Output 'FATTO. Restano due cose da fare a mano:'
Write-Output '  1. autologon di Windows (vedi le note in testa a questo file),'
Write-Output '     altrimenti dopo un blackout il PC resta al login e il bot non parte'
Write-Output '  2. impostazioni di risparmio energia: schermo pure spento, ma'
Write-Output '     sospensione e ibernazione DISATTIVATE, altrimenti gli emulatori'
Write-Output '     si fermano. Comando:'
Write-Output '       powercfg /change standby-timeout-ac 0'
Write-Output '       powercfg /change hibernate-timeout-ac 0'
Write-Output ''
Write-Output 'Per provare senza riavviare:'
Write-Output "  Start-ScheduledTask -TaskName $TaskWatchdog"
Write-Output "  Start-ScheduledTask -TaskName $TaskRemote"
