<#
.SYNOPSIS
    Riassunto in una schermata di cosa sta facendo il mini PC.

.DESCRIPTION
    Pensato per essere lanciato da SSH dall'altro PC: risponde alle domande che
    ci si fa da lontano, in quest'ordine.

      - il mini PC e' vivo, e da quanto?
      - i task pianificati sono partiti al logon, o sono fermi?
      - gli emulatori sono su e ADB li vede?
      - i due account stanno girando, o sono in pausa o in backoff?
      - cosa hanno scritto nei log negli ultimi minuti?

    Legge e basta, non tocca niente: si puo' lanciare in qualsiasi momento.

    La fonte sullo stato degli account e' logs/deploy/status.json, che scrive
    il watchdog. Se non c'e', vuol dire che il watchdog non e' mai partito, ed
    e' gia' la risposta.

.PARAMETER Righe
    Quante righe di log mostrare per ciascun account (default 12).

.PARAMETER Continuo
    Si aggiorna da solo ogni IntervalloSec secondi. Ctrl+C per uscire.

.EXAMPLE
    .\status.ps1

.EXAMPLE
    ssh Roberto@mini-pc "powershell -File C:\...\deploy\status.ps1 -Righe 30"
#>
param(
    [int]$Righe = 12,
    [switch]$Continuo,
    [int]$IntervalloSec = 30
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'common.ps1')

$LogDir = Join-Path $RepoRoot 'logs\deploy'
$StatusFile = Join-Path $LogDir 'status.json'

function Write-Titolo {
    param([string]$Testo)
    Write-Output ''
    Write-Output ('-' * 68)
    Write-Output "  $Testo"
    Write-Output ('-' * 68)
}

function Get-Trascorso {
    param([string]$Iso)
    if (-not $Iso) { return 'mai' }
    try {
        $t = [datetime]::Parse($Iso)
        $d = (Get-Date) - $t
        if ($d.TotalDays -ge 1) { return ('{0:n0}g {1:n0}h fa' -f $d.Days, $d.Hours) }
        if ($d.TotalHours -ge 1) { return ('{0:n0}h {1:n0}m fa' -f $d.Hours, $d.Minutes) }
        return ('{0:n0}m fa' -f $d.TotalMinutes)
    }
    catch { return $Iso }
}

function Show-Stato {

    # --- macchina -----------------------------------------------------------
    Write-Titolo 'Macchina'
    $os = Get-CimInstance Win32_OperatingSystem
    $acceso = (Get-Date) - $os.LastBootUpTime
    Write-Output ("ora           : " + (Get-Date -Format 'dd/MM/yyyy HH:mm:ss'))
    Write-Output ("acceso da     : {0:n0}g {1:n0}h {2:n0}m" -f $acceso.Days, $acceso.Hours, $acceso.Minutes)
    Write-Output ("RAM libera    : {0:n1} GB su {1:n1} GB" -f `
        ($os.FreePhysicalMemory / 1MB), ($os.TotalVisibleMemorySize / 1MB))
    $c = Get-PSDrive C
    Write-Output ("disco C:      : {0:n1} GB liberi" -f ($c.Free / 1GB))
    # L'emulatore si rifiuta di partire sotto i 5 GB liberi, e una cartella AVD
    # in esercizio cresce fino a ~30 GB: il disco pieno e' un guasto probabile.
    if (($c.Free / 1GB) -lt 10) {
        Write-Warning 'Meno di 10 GB liberi: gli emulatori possono rifiutarsi di partire.'
    }

    # --- task pianificati ---------------------------------------------------
    Write-Titolo 'Task pianificati'
    $task = Get-ScheduledTask -TaskName 'IGBot-*' -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Warning 'Nessun task IGBot-* registrato: lancia deploy\install-autostart.ps1.'
    }
    else {
        foreach ($t in $task) {
            $info = Get-ScheduledTaskInfo -TaskName $t.TaskName
            Write-Output ("{0,-22} {1,-10} ultima esecuzione: {2}  esito: {3}" -f `
                $t.TaskName, $t.State, $info.LastRunTime, $info.LastTaskResult)
        }
    }

    # --- processi -----------------------------------------------------------
    Write-Titolo 'Processi'
    $proc = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -match '^(emulator|qemu-system|python|adb|scrcpy)' }
    if (-not $proc) {
        Write-Output 'nessun emulatore e nessun python in esecuzione.'
    }
    else {
        $proc | Sort-Object -Property WS -Descending |
            Select-Object -First 12 @{n = 'nome'; e = { $_.ProcessName } },
                @{n = 'pid'; e = { $_.Id } },
                @{n = 'RAM MB'; e = { [int]($_.WS / 1MB) } },
                @{n = 'CPU s'; e = { [int]$_.CPU } } |
            Format-Table -AutoSize | Out-String | Write-Output
    }

    # --- emulatori ----------------------------------------------------------
    Write-Titolo 'Emulatori visti da ADB'
    try {
        $adb = Get-SdkTool 'platform-tools\adb.exe'
        $righe = & $adb devices | Where-Object { $_ -match 'emulator|device$|offline' }
        if ($righe) { $righe | Write-Output } else { Write-Output 'nessun device.' }
    }
    catch {
        Write-Warning $_.Exception.Message
    }

    # --- account ------------------------------------------------------------
    # --- login manuale richiesto --------------------------------------------
    # Il bot non digita mai credenziali: quando Instagram chiede la password
    # lascia questo file e aspetta che entri una persona. Senza questa riga
    # l'unico modo per accorgersene e' leggere il log dell'account.
    $flag = @(Get-ChildItem (Join-Path $LogDir 'login-richiesto_*.flag') -ErrorAction SilentlyContinue)
    if ($flag.Count -gt 0) {
        Write-Titolo 'Login richiesto'
        foreach ($f in $flag) {
            $acct = $f.BaseName -replace '^login-richiesto_', ''
            Write-Warning ("$acct : Instagram chiede la password (segnalato " +
                (Get-Trascorso $f.LastWriteTime.ToString('o')) + "). Apri lo schermo con " +
                "deploy\view-emulator.ps1 -Account $acct e fai il login a mano.")
        }
    }

    Write-Titolo 'Account'
    if (-not (Test-Path $StatusFile)) {
        Write-Warning "Manca $StatusFile : il watchdog non e' mai partito su questa macchina."
        return
    }

    $stato = Get-Content $StatusFile -Raw | ConvertFrom-Json
    Write-Output ("stato aggiornato: " + (Get-Trascorso $stato.updated))
    Write-Output ''

    foreach ($p in $stato.accounts.PSObject.Properties) {
        $nome = $p.Name
        $a = $p.Value
        $inPausa = Test-Path (Join-Path $LogDir "$nome.stop")

        if ($inPausa) { $riga = 'IN PAUSA (file .stop presente)' }
        elseif ($a.running) { $riga = "in esecuzione (pid $($a.pid))" }
        else { $riga = 'fermo' }

        Write-Output ("== $nome : $riga")
        Write-Output ("   avviato     : " + (Get-Trascorso $a.last_start))
        Write-Output ("   ultima fine : " + (Get-Trascorso $a.last_exit) + "  exit code: " + $a.last_code)
        if ([int]$a.failures -gt 0) {
            # il watchdog raddoppia l'attesa a ogni errore: se questo numero
            # sale, l'account e' probabilmente bloccato da Instagram e non
            # serve a niente riavviarlo a mano
            Write-Warning ("   errori consecutivi: " + $a.failures + " (il watchdog sta aspettando sempre di piu)")
        }

        $logAccount = Join-Path $LogDir "$nome.deploy.log"
        if (Test-Path $logAccount) {
            Write-Output "   ultime $Righe righe di $nome.deploy.log:"
            Get-Content $logAccount -Tail $Righe |
                ForEach-Object { Write-Output ("     " + $_) }
        }
        Write-Output ''
    }

    # --- watchdog -----------------------------------------------------------
    $logWatchdog = Join-Path $LogDir 'watchdog.log'
    if (Test-Path $logWatchdog) {
        Write-Titolo 'watchdog.log'
        Get-Content $logWatchdog -Tail $Righe | Write-Output
    }
}

if ($Continuo) {
    while ($true) {
        Clear-Host
        Show-Stato
        Write-Output ''
        Write-Output "(aggiornamento ogni $IntervalloSec s - Ctrl+C per uscire)"
        Start-Sleep -Seconds $IntervalloSec
    }
}
else {
    Show-Stato
}
