<#
.SYNOPSIS
    Installa sugli emulatori la versione di Instagram su cui il bot e' tarato.

.DESCRIPTION
    Il bot legge la UI di Instagram tramite i resource-id interni dell'app, che
    cambiano a ogni aggiornamento. Tutti i resource-id di questo fork sono
    verificati su Instagram 300.0.0.29.110 (vedi GramAddict/__init__.py,
    __tested_ig_version__). Su versioni recenti il bot non riesce nemmeno a
    leggere un profilo: e' gia' successo con la 428.0.0.0.4, vedi
    COMMIT_CHANGELOG.txt.

    Lo script NON scarica nulla: l'APK va procurato a mano e passato con -Apk
    (o messo in deploy\apk\ oppure in Download). Il motivo e' che un APK di
    Instagram preso da una fonte qualsiasi e' il modo piu' diretto per farsi
    rubare l'account, visto che l'app riceve le credenziali del cliente. Va
    preso da APKMirror, che verifica la firma contro quella di Google Play, e
    la firma va confrontata con quella che questo script stampa.

    L'architettura dell'APK deve combaciare con quella dell'emulatore. Lo
    script la controlla e si ferma prima di provare, dicendo quale variante
    serve: su un'immagine x86 a 32 bit non c'e' nessun ponte ARM, quindi un
    APK arm non gira e basta.

.PARAMETER Apk
    Percorso dell'APK. Se omesso lo cerca in deploy\apk\ e in Download.

.PARAMETER Serial
    Uno o piu' serial ADB. Se omesso prende tutti i device online.

.PARAMETER Version
    Versione attesa. Default: quella di __tested_ig_version__.

.PARAMETER Force
    Reinstalla anche se sul device c'e' gia' la versione giusta.

.PARAMETER BloccaAggiornamenti
    Disattiva il Play Store sul device dopo l'installazione. Sulle immagini
    "google_apis_playstore" il Play Store aggiorna Instagram da solo e il
    giorno dopo il bot non trova piu' i resource-id: e' un guasto che si
    presenta come "ha smesso di funzionare da solo".

.EXAMPLE
    .\deploy\install-instagram.ps1 -Apk C:\Users\Roberto\Downloads\instagram-300-x86.apk

.EXAMPLE
    .\deploy\install-instagram.ps1 -Serial emulator-5554 -BloccaAggiornamenti
#>
param(
    [string]$Apk,
    [string[]]$Serial,
    [string]$Version,
    [switch]$Force,
    [switch]$BloccaAggiornamenti
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PackageName = 'com.instagram.android'

# --- versione attesa: l'unica fonte di verita' e' il codice ---------------
if (-not $Version) {
    $initFile = Join-Path $RepoRoot 'GramAddict\__init__.py'
    $m = [regex]::Match((Get-Content $initFile -Raw), '__tested_ig_version__\s*=\s*"([^"]+)"')
    if (-not $m.Success) {
        throw "Non riesco a leggere __tested_ig_version__ da $initFile. Passa -Version a mano."
    }
    $Version = $m.Groups[1].Value
}
Write-Output "Versione richiesta dal bot: $Version"

# --- strumenti dell'SDK ----------------------------------------------------
function Get-SdkRoots {
    # @(...) obbligatorio: con un solo risultato Where-Object restituisce una
    # stringa, e $roots[0] darebbe il primo CARATTERE del percorso.
    return @(
        $env:ANDROID_HOME,
        $env:ANDROID_SDK_ROOT,
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
    ) | Where-Object { $_ -and (Test-Path $_) }
}

function Get-SdkTool {
    param([string]$Relative)
    foreach ($r in (Get-SdkRoots)) {
        $candidate = Join-Path $r $Relative
        if (Test-Path $candidate) { return $candidate }
    }
    $leaf = Split-Path -Leaf $Relative
    $cmd = Get-Command $leaf -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Non trovo $Relative. Imposta ANDROID_HOME o aggiungi l'SDK al PATH."
}

function Get-BuildTool {
    # build-tools e' versionato: prendo la piu' recente installata.
    param([string]$Leaf)
    foreach ($r in (Get-SdkRoots)) {
        $bt = Join-Path $r 'build-tools'
        if (-not (Test-Path $bt)) { continue }
        $dirs = @(Get-ChildItem $bt -Directory | Sort-Object Name -Descending)
        foreach ($d in $dirs) {
            $candidate = Join-Path $d.FullName $Leaf
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

function Get-JavaHome {
    <#
        apksigner e' uno script che lancia java, e su una macchina con solo
        l'SDK (senza JDK nel PATH) muore con "JAVA_HOME is not set". Android
        Studio pero' si porta dietro il proprio runtime in jbr\, che va
        benissimo: lo cerco li' invece di chiedere di installare un JDK.
    #>
    if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME 'bin\java.exe'))) {
        return $env:JAVA_HOME
    }
    $candidati = @(
        (Join-Path $env:ProgramFiles 'Android\Android Studio\jbr'),
        (Join-Path ${env:ProgramFiles(x86)} 'Android\Android Studio\jbr'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Android Studio\jbr'),
        (Join-Path $env:ProgramFiles 'Android\Android Studio\jre')
    )
    foreach ($c in $candidati) {
        if ($c -and (Test-Path (Join-Path $c 'bin\java.exe'))) { return $c }
    }
    $j = Get-Command java -ErrorAction SilentlyContinue
    if ($j) { return (Split-Path -Parent (Split-Path -Parent $j.Source)) }
    return $null
}

$Adb = Get-SdkTool 'platform-tools\adb.exe'
$Aapt = Get-BuildTool 'aapt.exe'
if (-not $Aapt) { $Aapt = Get-BuildTool 'aapt2.exe' }
$ApkSigner = Get-BuildTool 'apksigner.bat'
$JavaHome = Get-JavaHome

# --- device online ---------------------------------------------------------
function Get-DeviceOnline {
    $out = @(& $Adb devices)
    $lista = @()
    foreach ($l in $out) {
        $m = [regex]::Match($l, '^(\S+)\s+device\s*$')
        if ($m.Success) { $lista += $m.Groups[1].Value }
    }
    return $lista
}

function Show-AbiDeiDevice {
    foreach ($d in (Get-DeviceOnline)) {
        $abi = (& $Adb -s $d shell getprop ro.product.cpu.abilist | Out-String).Trim()
        $api = (& $Adb -s $d shell getprop ro.build.version.sdk | Out-String).Trim()
        Write-Output "  $d  API $api  abi: $abi"
    }
}

# --- 1. trova l'APK --------------------------------------------------------
if (-not $Apk) {
    $cerca = @(
        (Join-Path $PSScriptRoot 'apk'),
        (Join-Path $env:USERPROFILE 'Downloads')
    ) | Where-Object { Test-Path $_ }

    $trovati = @()
    foreach ($dir in $cerca) {
        $trovati += @(Get-ChildItem -Path $dir -Filter '*.apk' -File -ErrorAction SilentlyContinue)
    }
    if ($trovati.Count -eq 0) {
        Write-Output ''
        Write-Output "Nessun APK trovato in: $($cerca -join ', ')"
        Write-Output ''
        Write-Output "Scarica Instagram $Version da APKMirror, variante nodpi, con"
        Write-Output 'architettura fra quelle elencate qui sotto per i tuoi device:'
        Show-AbiDeiDevice
        Write-Output ''
        Write-Output '  https://www.apkmirror.com/apk/instagram/instagram-instagram/'
        Write-Output ''
        Write-Output "Poi mettilo in $(Join-Path $PSScriptRoot 'apk') e rilancia, oppure passa -Apk."
        exit 1
    }
    $Apk = ($trovati | Sort-Object LastWriteTime -Descending)[0].FullName
    Write-Output "APK trovato: $Apk"
}

if (-not (Test-Path $Apk)) { throw "APK non trovato: $Apk" }
$ApkItem = Get-Item $Apk
Write-Output ("Dimensione: {0:N1} MB" -f ($ApkItem.Length / 1MB))

# --- 2. cosa c'e' davvero dentro l'APK ------------------------------------
$badging = & $Aapt dump badging $Apk | Out-String

$mPkg = [regex]::Match($badging, "package:\s+name='([^']+)'")
$mVer = [regex]::Match($badging, "versionName='([^']+)'")
$mSdk = [regex]::Match($badging, "sdkVersion:'([0-9]+)'")
$abiApk = @([regex]::Matches($badging, 'native-code:\s*(.+)') |
    ForEach-Object { $_.Groups[1].Value -split "'" } |
    Where-Object { $_ -match '^[a-z0-9_-]+$' })

if (-not $mPkg.Success) { throw "aapt non riconosce $Apk come APK valido." }
$pkgApk = $mPkg.Groups[1].Value
$verApk = if ($mVer.Success) { $mVer.Groups[1].Value } else { '?' }
$minSdk = if ($mSdk.Success) { [int]$mSdk.Groups[1].Value } else { 0 }

Write-Output "  package     : $pkgApk"
Write-Output "  versionName : $verApk"
Write-Output "  minSdk      : $minSdk"
Write-Output "  native-code : $($abiApk -join ', ')"

if ($pkgApk -ne $PackageName) {
    throw "Questo APK e' '$pkgApk', non '$PackageName'. Non lo installo."
}
if ($verApk -ne $Version) {
    throw ("L'APK e' la versione $verApk, il bot e' tarato sulla $Version. " +
           "Con una versione diversa i resource-id non combaciano e il bot non " +
           "legge la UI. Se e' voluto, rilancia con -Version $verApk.")
}

# --- 3. firma: si confronta a mano, non c'e' modo di farlo in automatico ---
Write-Output ''
if (-not $ApkSigner) {
    Write-Output 'ATTENZIONE: apksigner non trovato, firma NON verificata.'
}
elseif (-not $JavaHome) {
    Write-Output 'ATTENZIONE: nessun Java trovato, firma NON verificata.'
    Write-Output '  apksigner ha bisogno di un JDK. Installa Android Studio oppure imposta JAVA_HOME.'
}
else {
    $env:JAVA_HOME = $JavaHome
    $certs = @(& cmd /c "`"$ApkSigner`" verify --print-certs `"$Apk`" 2>&1")
    $sha = @($certs | Select-String -Pattern 'SHA-256 digest' | ForEach-Object { $_.Line.Trim() })
    if ($sha.Count -eq 0) {
        # Silenzio qui vorrebbe dire "firma ok" mentre invece apksigner e'
        # morto: meglio dirlo e mostrare cosa ha risposto davvero.
        Write-Output 'ATTENZIONE: apksigner non ha stampato nessun certificato. Firma NON verificata.'
        foreach ($l in @($certs | Select-Object -First 6)) { Write-Output "  $l" }
    }
    else {
        Write-Output 'Firma dell APK (confrontala con quella mostrata da APKMirror):'
        foreach ($l in $sha) { Write-Output "  $l" }
    }
}
Write-Output ''

# --- 4. device target ------------------------------------------------------
if (-not $Serial) {
    $Serial = Get-DeviceOnline
    if ($Serial.Count -eq 0) { throw 'Nessun device ADB online. Avvia gli emulatori.' }
    Write-Output "Device target (tutti quelli online): $($Serial -join ', ')"
}

# --- 5. installazione, un device alla volta -------------------------------
$falliti = @()
foreach ($d in $Serial) {
    Write-Output ''
    Write-Output "=== $d ==="

    $abiDev = @((& $Adb -s $d shell getprop ro.product.cpu.abilist | Out-String).Trim() -split ',' |
        ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $apiDev = [int]((& $Adb -s $d shell getprop ro.build.version.sdk | Out-String).Trim())
    Write-Output "  API $apiDev, abi: $($abiDev -join ', ')"

    # Controllo prima di provare: adb fallirebbe comunque con
    # INSTALL_FAILED_NO_MATCHING_ABIS, ma con un messaggio che non dice quale
    # variante scaricare.
    if ($abiApk.Count -gt 0) {
        $comune = @($abiApk | Where-Object { $abiDev -contains $_ })
        if ($comune.Count -eq 0) {
            Write-Output "  SALTATO: l'APK e' per [$($abiApk -join ', ')], il device e' [$($abiDev -join ', ')]."
            Write-Output "  Serve la variante $($abiDev[0]) di Instagram $Version."
            $falliti += $d
            continue
        }
    }
    if ($apiDev -lt $minSdk) {
        Write-Output "  SALTATO: l'APK richiede API $minSdk, il device e' API $apiDev."
        $falliti += $d
        continue
    }

    $dump = (& $Adb -s $d shell dumpsys package $PackageName | Out-String)
    $mIns = [regex]::Match($dump, 'versionName=(\S+)')
    $verIns = if ($mIns.Success) { $mIns.Groups[1].Value } else { $null }

    if ($verIns -eq $Version -and -not $Force) {
        Write-Output "  Instagram $Version gia' installata, non tocco niente (-Force per reinstallare)."
        continue
    }
    if ($verIns -and $verIns -ne $Version) {
        # Il downgrade non e' permesso senza disinstallare: adb install -d
        # funziona solo sui build debuggabili, e Instagram non lo e'.
        Write-Output "  C'e' la versione ${verIns}: la disinstallo (questo cancella il login su questo device)."
        & $Adb -s $d uninstall $PackageName | Out-Null
    }

    # -r  reinstalla mantenendo i dati quando possibile
    # -g  concede subito i permessi runtime: senza, durante la sessione
    #     comparirebbero dialog di sistema che il bot non sa chiudere
    Write-Output '  installazione in corso...'
    $out = (& $Adb -s $d install -r -g $Apk | Out-String)
    if ($out -notmatch 'Success') {
        Write-Output "  ERRORE: $($out.Trim())"
        $falliti += $d
        continue
    }

    $dump = (& $Adb -s $d shell dumpsys package $PackageName | Out-String)
    $mIns = [regex]::Match($dump, 'versionName=(\S+)')
    $verFin = if ($mIns.Success) { $mIns.Groups[1].Value } else { '?' }
    if ($verFin -ne $Version) {
        Write-Output "  ERRORE: dopo l'installazione il device dichiara $verFin."
        $falliti += $d
        continue
    }
    Write-Output "  OK: Instagram $verFin installata."

    if ($BloccaAggiornamenti) {
        $play = (& $Adb -s $d shell pm list packages com.android.vending | Out-String).Trim()
        if ($play) {
            & $Adb -s $d shell pm disable-user --user 0 com.android.vending | Out-Null
            Write-Output '  Play Store disattivato: non puo piu aggiornare Instagram a tua insaputa.'
        }
        else {
            Write-Output '  Play Store non presente su questa immagine, niente da bloccare.'
        }
    }
}

Write-Output ''
if ($falliti.Count -gt 0) {
    Write-Output "FALLITI: $($falliti -join ', ')"
    exit 1
}
Write-Output 'Fatto. Prossimo passo: login manuale su ogni emulatore.'
