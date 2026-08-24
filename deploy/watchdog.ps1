<#
.SYNOPSIS
    Supervisore h24: tiene vivi i bot dei due account e li fa ripartire quando
    muoiono.

.DESCRIPTION
    run-dynamic.py fa un numero finito di sessioni e poi TERMINA. Per un
    funzionamento 7 giorni su 7 serve qualcosa che lo rilanci: e' questo.

    Cosa gestisce:
      - bot che esce normalmente a fine giornata  -> rilancio dopo una pausa
      - bot che crasha                            -> rilancio con backoff
      - crash ripetuti                            -> pausa lunga invece di
        martellare (se Instagram ha bloccato l'account, insistere peggiora)
      - emulatore piantato -> start-account.ps1 lo riavvia da solo, perche'
        aspetta sys.boot_completed prima di lanciare il bot

    Lo stato viene scritto in logs/deploy/status.json, che il controllo remoto
    via Telegram legge per rispondere a /status.

.EXAMPLE
    .\watchdog.ps1
#>
param(
    [int]$RestartDelaySec = 900,      # pausa normale tra due cicli (15 min)
    [int]$MaxBackoffSec = 7200,       # tetto della pausa dopo crash ripetuti (2h)
    # Anti-freeze: se il log del bot non avanza per questo numero di minuti
    # mentre il processo e' vivo (e non sta dichiaratamente aspettando la
    # prossima finestra), il processo e' considerato congelato e viene
    # riavviato. Il 22-23/08 due freeze cosi' sono costati 95 e 344 minuti
    # di sessioni perse, senza che il watchdog se ne accorgesse: il processo
    # non esce, resta appeso a meta' interazione.
    [int]$StallMin = 15,
    # Tregua dopo l'avvio: boot dell'emulatore + avvio app possono passare
    # diversi minuti prima della prima riga di log del bot.
    [int]$StallGraceMin = 20
)

$ErrorActionPreference = 'Continue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot 'logs\deploy'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir 'watchdog.log'
$StatusFile = Join-Path $LogDir 'status.json'

# Un account per emulatore. I serial devono combaciare con il campo `device:`
# nei rispettivi config.yml, altrimenti il bot parla al device sbagliato.
#
# OffsetMin sfalsa la PRIMA partenza del secondo account, perche' su un mini
# PC a 4 core i due emulatori che lavorano insieme si contendono la CPU.
# Con i config attuali (total-sessions: -1, finestre FISSE alternate tra i
# due account, vedi accounts/*/config.yml) l'alternanza giornaliera la danno
# le finestre stesse e il processo non esce mai da solo: qui si gestiscono
# solo i crash. L'offset resta per il primo avvio e per eventuali config a
# finestre dinamiche (total-sessions: N), dove run-dynamic.py costruisce le
# finestre dall'ora di lancio.
$Accounts = @(
    @{ Name = 'rb.coach';                Avd = 'rbcoach';    Serial = 'emulator-5554'; OffsetMin = 0 },
    @{ Name = 'roberto_buonomo_ifbbpro'; Avd = 'robertobuo'; Serial = 'emulator-5556'; OffsetMin = 90 }
)

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

# stato condiviso col controllo remoto
$script:State = @{}
foreach ($a in $Accounts) {
    $script:State[$a.Name] = @{
        running = $false; pid = 0; failures = 0
        last_start = ''; last_exit = ''; last_code = $null
    }
}

function Save-Status {
    $payload = @{
        updated  = (Get-Date -Format 'o')
        accounts = $script:State
    }
    try {
        $payload | ConvertTo-Json -Depth 5 | Set-Content -Path $StatusFile -Encoding utf8
    }
    catch { Write-Log "non riesco a scrivere status.json: $_" }
}

# File-interruttore: il controllo remoto crea <account>.stop per fermare un
# account senza uccidere il watchdog. Cancellarlo lo fa ripartire.
function Test-Paused {
    param([string]$Account)
    return (Test-Path (Join-Path $LogDir "$Account.stop"))
}

function Start-AccountProcess {
    param($Acct)
    $script = Join-Path $PSScriptRoot 'start-account.ps1'
    # -NoWindow: in produzione l'emulatore gira senza finestra. Nessuno guarda
    # lo schermo del mini PC, e comporre l'immagine costa CPU che su 4 core
    # serve altrove. Per vedere cosa sta facendo c'e' /shot su Telegram.
    $args = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script,
        '-Account', $Acct.Name, '-Avd', $Acct.Avd, '-Serial', $Acct.Serial,
        '-NoWindow'
    )
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList $args `
        -WindowStyle Minimized -PassThru
    $script:State[$Acct.Name].running = $true
    $script:State[$Acct.Name].pid = $p.Id
    $script:State[$Acct.Name].last_start = (Get-Date -Format 'o')
    Write-Log "[$($Acct.Name)] avviato (pid $($p.Id))"
    return $p
}

# Rileva un processo congelato: log fermo da piu' di $StallMin minuti SENZA
# una spiegazione legittima. La spiegazione legittima e' l'attesa dichiarata
# della prossima finestra: GramAddict la scrive nel log come
#   "Next session will start at: HH:MM:SS (YYYY/MM/DD)."
# e in quel caso il log resta fermo, anche per ore, fino all'orario indicato.
# Quindi: se l'ultima riga di attesa promette un risveglio FUTURO, va tutto
# bene; se il risveglio promesso e' passato da piu' di $StallMin minuti, o
# non c'e' nessuna attesa dichiarata, il processo e' congelato.
function Test-Frozen {
    param([string]$Account)
    $log = Join-Path $RepoRoot ("logs\{0}.log" -f $Account)
    if (-not (Test-Path $log)) { return $false }
    $staleMin = ((Get-Date) - (Get-Item $log).LastWriteTime).TotalMinutes
    if ($staleMin -lt $StallMin) { return $false }

    $tail = Get-Content $log -Tail 40 -ErrorAction SilentlyContinue
    if ($null -eq $tail) { return $false }
    $wake = $null
    foreach ($line in $tail) {
        if ($line -match 'Next session will start at: (\d{2}:\d{2}:\d{2} \(\d{4}/\d{2}/\d{2}\))') {
            $wake = $Matches[1]   # l'ULTIMA occorrenza nel tail vince
        }
    }
    if ($null -ne $wake) {
        try {
            $wakeDt = [datetime]::ParseExact($wake, 'HH:mm:ss (yyyy/MM/dd)', [System.Globalization.CultureInfo]::InvariantCulture)
            if ((Get-Date) -lt $wakeDt.AddMinutes($StallMin)) { return $false }
        }
        catch {
            # formato inatteso: meglio non uccidere un bot che forse dorme.
            # Sotto le 3 ore di silenzio si lascia stare (pausa tra finestre).
            if ($staleMin -lt 180) { return $false }
        }
    }
    return $true
}

Write-Log '=== watchdog avviato ==='
$procs = @{}
$nextStart = @{}
foreach ($a in $Accounts) {
    $off = [int]$a.OffsetMin
    $nextStart[$a.Name] = (Get-Date).AddMinutes($off)
    if ($off -gt 0) {
        Write-Log "[$($a.Name)] prima partenza sfalsata di $off min (per non far lavorare i due emulatori insieme)"
    }
}
Save-Status

while ($true) {
    foreach ($a in $Accounts) {
        $name = $a.Name
        $st = $script:State[$name]

        # 1. l'account e' stato messo in pausa da remoto?
        if (Test-Paused $name) {
            if ($st.running -and $procs.ContainsKey($name)) {
                Write-Log "[$name] richiesta di stop: termino il processo"
                try { Stop-Process -Id $procs[$name].Id -Force -ErrorAction SilentlyContinue } catch {}
                $procs.Remove($name)
                $st.running = $false
                $st.last_exit = (Get-Date -Format 'o')
                Save-Status
            }
            continue
        }

        # 2. il processo gira ancora?
        if ($st.running -and $procs.ContainsKey($name)) {
            if (-not $procs[$name].HasExited) {
                # 2a. vivo ma congelato? (log fermo senza attesa dichiarata)
                $ageMin = ((Get-Date) - [datetime]$st.last_start).TotalMinutes
                if ($ageMin -gt $StallGraceMin -and (Test-Frozen $name)) {
                    Write-Log "[$name] log fermo oltre $StallMin min senza attesa dichiarata: processo congelato, riavvio (taskkill /T)"
                    # /T uccide tutta la catena: wrapper powershell,
                    # run-dynamic.py, GramAddict e ANCHE l'emulatore, che
                    # Windows considera discendente nonostante il
                    # DETACHED_PROCESS (vedi stop-bots.py). Qui va bene cosi':
                    # un bot congelato spesso vuol dire emulatore incantato, e
                    # start-account.ps1 al rilancio lo riavvia e ne aspetta il
                    # boot da solo.
                    try { taskkill /PID $procs[$name].Id /T /F 2>$null | Out-Null } catch {}
                    $procs.Remove($name)
                    $st.running = $false
                    $st.last_exit = (Get-Date -Format 'o')
                    $st.last_code = 'frozen'
                    # niente backoff: un freeze non e' un blocco di Instagram,
                    # e ogni minuto di attesa e' una finestra di lavoro persa.
                    # resume-from-last-position riprende da dove si era fermato.
                    $nextStart[$name] = (Get-Date).AddSeconds(120)
                    Save-Status
                }
                continue
            }

            $code = $procs[$name].ExitCode
            $st.running = $false
            $st.last_exit = (Get-Date -Format 'o')
            $st.last_code = $code
            $procs.Remove($name)

            if ($code -eq 0) {
                # uscita pulita: ha finito le sessioni previste
                $st.failures = 0
                $wait = $RestartDelaySec
                Write-Log "[$name] uscito regolarmente. Riparto tra $wait s."
            }
            else {
                # crash: backoff esponenziale, per non insistere se IG ha bloccato
                $st.failures = [int]$st.failures + 1
                $wait = [Math]::Min($RestartDelaySec * [Math]::Pow(2, $st.failures), $MaxBackoffSec)
                Write-Log "[$name] uscito con codice $code (errore #$($st.failures)). Riparto tra $wait s."
            }
            $nextStart[$name] = (Get-Date).AddSeconds($wait)
            Save-Status
            continue
        }

        # 3. fermo: e' ora di ripartire?
        if ((Get-Date) -ge $nextStart[$name]) {
            try {
                $procs[$name] = Start-AccountProcess $a
                Save-Status
            }
            catch {
                Write-Log "[$name] avvio fallito: $_"
                $nextStart[$name] = (Get-Date).AddSeconds($RestartDelaySec)
            }
        }
    }

    Start-Sleep -Seconds 30
}
