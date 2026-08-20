<#
.SYNOPSIS
    Controlla se questa macchina puo' far girare gli emulatori Android del bot.

.DESCRIPTION
    Da lanciare sul mini PC APPENA lo si ha in mano, prima di installare
    qualsiasi cosa. Verifica i prerequisiti che dalla scheda del venditore non
    si possono sapere: virtualizzazione abilitata nel BIOS, edizione di
    Windows, RAM reale, spazio disco, impostazioni di risparmio energia.

    Non modifica niente: legge e basta.

.EXAMPLE
    .\check-machine.ps1
#>

$ErrorActionPreference = 'Continue'

$script:Problemi = @()
$script:Avvisi = @()

function Invoke-ConTimeout {
    <#
    .SYNOPSIS
        Esegue un comando esterno con un tetto di tempo, e lo ammazza se sfora.

    .DESCRIPTION
        Serve per "emulator -accel-check", che su alcune macchine non ritorna
        mai e lascia dietro processi emulator-check appesi. Senza timeout,
        questo script di diagnosi si pianta invece di dare una risposta.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [int]$TimeoutSec = 20,
        [string]$KillProcessName
    )

    $out = New-TemporaryFile
    $err = New-TemporaryFile
    try {
        $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
            -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            try { $p.Kill() } catch {}
            # i figli non muoiono col padre: vanno chiusi a mano
            if ($KillProcessName) {
                Get-Process $KillProcessName -ErrorAction SilentlyContinue |
                    ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {} }
            }
            return [pscustomobject]@{ TimedOut = $true; Output = '' }
        }
        $testo = ((Get-Content $out -Raw -ErrorAction SilentlyContinue) + "`n" +
                  (Get-Content $err -Raw -ErrorAction SilentlyContinue))
        return [pscustomobject]@{ TimedOut = $false; Output = $testo.Trim() }
    }
    catch {
        return [pscustomobject]@{ TimedOut = $false; Output = "errore: $_" }
    }
    finally {
        Remove-Item $out, $err -Force -ErrorAction SilentlyContinue
    }
}

function Write-Sezione {
    param([string]$Titolo)
    Write-Output ''
    Write-Output ('=' * 66)
    Write-Output "  $Titolo"
    Write-Output ('=' * 66)
}

function Write-Esito {
    param(
        [string]$Etichetta,
        [string]$Valore,
        [ValidateSet('ok', 'avviso', 'problema', 'info')][string]$Livello = 'info',
        [string]$Nota = ''
    )
    $simbolo = switch ($Livello) {
        'ok'       { '[ OK ]' }
        'avviso'   { '[ ?  ]' }
        'problema' { '[ !! ]' }
        default    { '[ .. ]' }
    }
    Write-Output ("{0} {1,-34} {2}" -f $simbolo, $Etichetta, $Valore)
    if ($Nota) { Write-Output ("       -> " + $Nota) }
    if ($Livello -eq 'problema') { $script:Problemi += "$Etichetta : $Valore. $Nota" }
    if ($Livello -eq 'avviso') { $script:Avvisi += "$Etichetta : $Valore. $Nota" }
}

Write-Output ''
Write-Output 'Controllo prerequisiti per gli emulatori Android del bot Instagram'
Write-Output ("Macchina: {0}   Data: {1}" -f $env:COMPUTERNAME, (Get-Date -Format 'yyyy-MM-dd HH:mm'))

# --------------------------------------------------------------------------
Write-Sezione 'CPU'
# --------------------------------------------------------------------------
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Esito 'Modello' $cpu.Name.Trim() 'info'
Write-Esito 'Core fisici / logici' ("{0} / {1}" -f $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors) $(
    if ($cpu.NumberOfCores -ge 6) { 'ok' }
    elseif ($cpu.NumberOfCores -ge 4) { 'avviso' }
    else { 'problema' }
) $(
    if ($cpu.NumberOfCores -ge 6) { '' }
    elseif ($cpu.NumberOfCores -ge 4) { 'Due emulatori in parallelo sono il limite: sfalsa gli orari (OffsetMin nel watchdog).' }
    else { 'Sotto i 4 core, due emulatori in parallelo non sono realistici. Falli girare uno alla volta.' }
)

# --------------------------------------------------------------------------
Write-Sezione 'VIRTUALIZZAZIONE (il controllo piu importante)'
# --------------------------------------------------------------------------
# VirtualizationFirmwareEnabled = l'opzione e' accesa nel BIOS.
# ATTENZIONE: se e' gia' attivo un hypervisor (Hyper-V), Windows riporta
# spesso $false anche quando in realta' e' abilitata: in quel caso vale
# HypervisorPresent.
$virtFw = $cpu.VirtualizationFirmwareEnabled
$cs = Get-CimInstance Win32_ComputerSystem
$hvPresent = $cs.HypervisorPresent

if ($hvPresent) {
    Write-Esito 'Hypervisor attivo' 'si' 'ok' 'Un hypervisor gira gia: la virtualizzazione hardware e abilitata.'
}
elseif ($virtFw -eq $true) {
    Write-Esito 'Virtualizzazione nel BIOS' 'abilitata' 'ok' ''
}
elseif ($virtFw -eq $false) {
    Write-Esito 'Virtualizzazione nel BIOS' 'DISABILITATA' 'problema' `
        'Riavvia, entra nel BIOS/UEFI e abilita SVM Mode (AMD) o Intel VT-x. Senza questa, l emulatore va in emulazione software ed e inutilizzabile.'
}
else {
    Write-Esito 'Virtualizzazione nel BIOS' 'non rilevabile' 'avviso' `
        'Windows non espone il dato. Verifica in Gestione attivita > Prestazioni > CPU, voce "Virtualizzazione".'
}

# Come per VirtualizationFirmwareEnabled, con un hypervisor gia' attivo
# Windows riporta $false anche quando SLAT c'e' eccome: segnalarlo come
# problema sarebbe un falso allarme (WHPX non funzionerebbe proprio, se
# mancasse davvero).
if ($cpu.SecondLevelAddressTranslationExtensions) {
    Write-Esito 'SLAT (nested paging)' 'si' 'ok' ''
}
elseif ($hvPresent) {
    Write-Esito 'SLAT (nested paging)' 'non riportato' 'info' `
        'Con un hypervisor attivo Windows non espone il dato. Fa fede il controllo sull acceleratore, piu sotto.'
}
else {
    Write-Esito 'SLAT (nested paging)' 'no' 'avviso' `
        'Richiesto da WHPX, che e l unico acceleratore con un futuro (AEHD viene dismesso a fine 2026, HAXM e gia morto).'
}

# --------------------------------------------------------------------------
Write-Sezione 'WINDOWS'
# --------------------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
Write-Esito 'Edizione' $os.Caption.Trim() 'info'
Write-Esito 'Versione' ("{0} build {1}" -f $os.Version, $os.BuildNumber) 'info'
Write-Esito 'Architettura' $os.OSArchitecture 'info'

# WHPX e' una funzionalita' opzionale; su alcune edizioni non e' presente.
try {
    # WHPX e' la strada da prendere: HAXM e' fuori dall'emulatore da ottobre
    # 2025 e AEHD viene dismesso il 31/12/2026. Se questa feature e' spenta,
    # va accesa.
    $whpx = Get-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -ErrorAction Stop
    Write-Esito 'Windows Hypervisor Platform' $whpx.State $(
        if ($whpx.State -eq 'Enabled') { 'ok' } else { 'avviso' }
    ) $(
        if ($whpx.State -eq 'Enabled') { '' }
        else { 'Da abilitare: DISM /Online /Enable-Feature /FeatureName:HypervisorPlatform /All e riavvio. E l acceleratore raccomandato da Google.' }
    )
}
catch {
    Write-Esito 'Windows Hypervisor Platform' 'non interrogabile' 'info' 'Serve un PowerShell da amministratore per leggerlo.'
}

# --------------------------------------------------------------------------
Write-Sezione 'MEMORIA E DISCO'
# --------------------------------------------------------------------------
$ramGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$ramLibereGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
Write-Esito 'RAM totale' ("{0} GB" -f $ramGB) $(
    if ($ramGB -ge 24) { 'ok' }
    elseif ($ramGB -ge 12) { 'avviso' }
    else { 'problema' }
) $(
    if ($ramGB -ge 24) { '' }
    elseif ($ramGB -ge 12) { 'Sufficiente per 2 AVD da 2 GB, ma senza margine: tieni gli AVD leggeri.' }
    else { 'Troppo poca per due emulatori. Uno solo alla volta.' }
)
Write-Esito 'RAM libera ora' ("{0} GB" -f $ramLibereGB) 'info'

# Numero di moduli: con UN solo banco la macchina lavora in single channel e
# dimezza la banda verso la grafica integrata. Per un emulatore, che usa la
# RAM di sistema come memoria video, e' proprio il collo di bottiglia.
$moduli = @(Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue)
if ($moduli.Count -gt 0) {
    $slotTot = $null
    try { $slotTot = (@(Get-CimInstance Win32_PhysicalMemoryArray))[0].MemoryDevices } catch {}
    $desc = "$($moduli.Count) modulo/i"
    if ($slotTot) { $desc += " su $slotTot slot" }
    Write-Esito 'Banchi di memoria' $desc $(
        if ($moduli.Count -ge 2) { 'ok' } else { 'avviso' }
    ) $(
        if ($moduli.Count -ge 2) { 'Dual channel: la banda verso la grafica integrata e piena.' }
        else { 'SINGLE CHANNEL: banda dimezzata verso la iGPU, che e proprio cio che usa l emulatore. Se c e uno slot libero, aggiungere un secondo modulo uguale e la modifica col miglior rapporto costo/beneficio.' }
    )
    $velocita = ($moduli | ForEach-Object { $_.ConfiguredClockSpeed } | Where-Object { $_ } | Select-Object -First 1)
    if ($velocita) { Write-Esito 'Velocita memoria' ("{0} MT/s" -f $velocita) 'info' }
}

# L'emulatore chiede il commit dell'intera RAM del guest all'AVVIO: se il
# commit limit (RAM + file di paging) non basta, non parte proprio.
$commitLimitGB = [math]::Round($os.TotalVirtualMemorySize / 1MB, 1)
Write-Esito 'Commit limit (RAM + paging)' ("{0} GB" -f $commitLimitGB) $(
    if ($commitLimitGB -ge ($ramGB + 8)) { 'ok' }
    else { 'avviso' }
) $(
    if ($commitLimitGB -ge ($ramGB + 8)) { '' }
    else { 'Con due emulatori conviene un file di paging generoso: l emulatore committa tutta la RAM del guest all avvio e senza margine non parte.' }
)

$disco = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$liberiGB = [math]::Round($disco.FreeSpace / 1GB, 1)
Write-Esito 'Spazio libero su C:' ("{0} GB" -f $liberiGB) $(
    if ($liberiGB -ge 60) { 'ok' }
    elseif ($liberiGB -ge 30) { 'avviso' }
    else { 'problema' }
) $(
    if ($liberiGB -ge 60) { '' }
    else { 'SDK + 2 immagini di sistema + 2 AVD occupano circa 25-30 GB, piu i log che crescono h24.' }
)

# --------------------------------------------------------------------------
Write-Sezione 'RISPARMIO ENERGIA (se il PC dorme, il bot si ferma)'
# --------------------------------------------------------------------------
$standby = (& powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 2>$null | Out-String)

# powercfg traduce le etichette, quindi la regex per nome e' fragile
# (in italiano e' "Indice impostazione alimentazione CA corrente"). Come
# riserva uso la posizione, che invece non cambia con la lingua: gli ultimi
# due valori esadecimali stampati sono sempre CA e CC, in quest'ordine.
$secondi = $null
$m = [regex]::Match($standby, '(?im)^\s*(?:Indice impostazione alimentazione CA corrente|Current AC Power Setting Index)\s*:\s*0x([0-9a-f]+)')
if ($m.Success) {
    $secondi = [Convert]::ToInt32($m.Groups[1].Value, 16)
}
else {
    $hex = @([regex]::Matches($standby, '0x([0-9a-f]{8})'))
    if ($hex.Count -ge 2) {
        $secondi = [Convert]::ToInt32($hex[$hex.Count - 2].Groups[1].Value, 16)
    }
}

if ($null -ne $secondi) {
    Write-Esito 'Sospensione con alimentazione' $(
        if ($secondi -eq 0) { 'mai (corretto)' } else { "dopo $secondi s" }
    ) $(
        if ($secondi -eq 0) { 'ok' } else { 'problema' }
    ) $(
        if ($secondi -eq 0) { '' } else { 'Disattivala: powercfg /change standby-timeout-ac 0' }
    )
}
else {
    Write-Esito 'Sospensione con alimentazione' 'non leggibile' 'avviso' 'Lancia comunque: powercfg /change standby-timeout-ac 0'
}

# --------------------------------------------------------------------------
Write-Sezione 'STRUMENTI ANDROID'
# --------------------------------------------------------------------------
# Le @() esterne servono davvero: se Where-Object restituisce UN solo elemento
# PowerShell lo "srotola" a stringa, e $sdkRoots[0] darebbe il primo CARATTERE
# ("C") invece del percorso. Il controllo su .Count non se ne accorge, perche'
# anche una stringa ha Count = 1.
$sdkRoots = @(
    @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA 'Android\Sdk')) |
        Where-Object { $_ -and (Test-Path $_) }
)

if ($sdkRoots.Count -eq 0) {
    Write-Esito 'Android SDK' 'non trovato' 'avviso' 'Da installare (Android Studio o command-line tools).'
}
else {
    $sdk = $sdkRoots[0]
    Write-Esito 'Android SDK' $sdk 'ok' ''
    foreach ($t in @(@('adb', 'platform-tools\adb.exe'), @('emulator', 'emulator\emulator.exe'))) {
        $p = Join-Path $sdk $t[1]
        if (Test-Path $p) { Write-Esito ("  " + $t[0]) 'presente' 'ok' '' }
        else { Write-Esito ("  " + $t[0]) 'MANCANTE' 'problema' "Atteso in $p" }
    }
    $emu = Join-Path $sdk 'emulator\emulator.exe'
    if (Test-Path $emu) {
        $avds = & $emu -list-avds 2>$null | Where-Object { $_ -and $_.Trim() }
        if ($avds) {
            Write-Esito 'AVD configurati' ($avds -join ', ') 'ok' ''
            foreach ($atteso in @('bot_rb', 'bot_pers')) {
                if ($avds -notcontains $atteso) {
                    Write-Esito ("  AVD '$atteso'") 'assente' 'avviso' 'Atteso da deploy\watchdog.ps1'
                }
            }
        }
        else {
            Write-Esito 'AVD configurati' 'nessuno' 'avviso' 'Creali da Android Studio: bot_rb e bot_pers.'
        }
        # -accel-check dice quale acceleratore e' realmente utilizzabile QUI.
        # Va lanciato con timeout: su alcune macchine non ritorna mai e lascia
        # dietro processi emulator-check appesi.
        # 45s e non 20: su una macchina carica -accel-check ci mette il suo, e
        # un timeout troppo stretto darebbe un falso allarme piu' spesso di
        # quanto scoprirebbe un blocco vero.
        $res = Invoke-ConTimeout -FilePath $emu -ArgumentList @('-accel-check') `
            -TimeoutSec 45 -KillProcessName 'emulator-check'
        if ($res.TimedOut) {
            Write-Esito 'Acceleratore emulatore' 'nessuna risposta in 45s' 'avviso' `
                'Il controllo si e piantato (succede se la macchina e carica). Rilancia, o a mano: emulator -accel-check'
        }
        elseif ($res.Output) {
            $accelOk = ($res.Output -match 'is installed and usable|accel.*ok')
            Write-Esito 'Acceleratore emulatore' $(
                if ($accelOk) { 'utilizzabile' } else { 'NON utilizzabile' }
            ) $(
                if ($accelOk) { 'ok' } else { 'problema' }
            ) ($res.Output -replace '\s+', ' ')
        }
    }
}

# --------------------------------------------------------------------------
Write-Sezione 'RIEPILOGO'
# --------------------------------------------------------------------------
if ($script:Problemi.Count -eq 0 -and $script:Avvisi.Count -eq 0) {
    Write-Output ''
    Write-Output 'Nessun problema rilevato: la macchina puo far girare gli emulatori.'
}
else {
    if ($script:Problemi.Count -gt 0) {
        Write-Output ''
        Write-Output ("DA RISOLVERE ({0}):" -f $script:Problemi.Count)
        $script:Problemi | ForEach-Object { Write-Output ("  - " + $_) }
    }
    if ($script:Avvisi.Count -gt 0) {
        Write-Output ''
        Write-Output ("DA CONTROLLARE ({0}):" -f $script:Avvisi.Count)
        $script:Avvisi | ForEach-Object { Write-Output ("  - " + $_) }
    }
}
Write-Output ''
