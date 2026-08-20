<#
.SYNOPSIS
    Predispone un terminale remoto vero sul mini PC: server SSH + rete privata.

.DESCRIPTION
    Il bot Telegram con /cmd va bene per un comando al volo dal telefono, ma
    non e' un terminale: niente comandi interattivi, niente output in tempo
    reale, niente copia di file, e ogni comando passa dai server di Telegram.

    Questo script installa il server OpenSSH GIA' PRESENTE in Windows 11 (non
    scarica nulla da terzi) e lo configura per l'accesso a chiave.

    RAGGIUNGIBILITA' DA FUORI CASA
    SSH da solo funziona nella rete locale. Per entrare da fuori ci sono due
    strade:

      a) Tailscale (consigliata): crea una rete privata tra i tuoi dispositivi.
         Niente porte aperte sul router, niente IP pubblico, gratis per uso
         personale. Installalo sul mini PC e sul telefono/portatile, fai il
         login con lo stesso account, e il mini PC diventa raggiungibile con
         un suo nome fisso ovunque tu sia.
         Con -InstallTailscale questo script lo installa via winget.

      b) Port forwarding della 22 sul router: SCONSIGLIATO. Esporre SSH su
         internet significa prendersi tentativi di accesso automatici in
         continuazione. Se proprio, almeno cambia porta e disabilita
         l'autenticazione a password.

    DOPO, DA TELEFONO O PORTATILE
      ssh <utente>@<nome-tailscale-del-minipc>
    e da li' hai il terminale completo: log, git pull, riavvio dei task,
    powershell, tutto.

.PARAMETER InstallTailscale
    Installa anche Tailscale via winget.

.PARAMETER PublicKey
    La tua chiave pubblica (contenuto di id_ed25519.pub). Se la passi, viene
    autorizzata e si puo' disabilitare l'accesso con password.

.PARAMETER DisablePasswordAuth
    Disabilita l'accesso con password. Usalo SOLO dopo aver verificato che
    l'accesso a chiave funziona, altrimenti ti chiudi fuori.

.EXAMPLE
    # PowerShell come amministratore
    .\install-remote-shell.ps1

.EXAMPLE
    .\install-remote-shell.ps1 -InstallTailscale -PublicKey "ssh-ed25519 AAAA... tu@portatile"
#>
param(
    [switch]$InstallTailscale,
    [string]$PublicKey,
    [switch]$DisablePasswordAuth
)

$ErrorActionPreference = 'Stop'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Warning 'Serve un PowerShell APERTO COME AMMINISTRATORE.'
    exit 1
}

# --- 1. server OpenSSH ------------------------------------------------------
Write-Output '== Server OpenSSH =='
$cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' |
    Select-Object -First 1

if (-not $cap) {
    Write-Warning "Questa versione di Windows non offre OpenSSH.Server come funzionalita'."
    exit 1
}

if ($cap.State -ne 'Installed') {
    Write-Output "installo $($cap.Name) ..."
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
    Write-Output 'installato.'
}
else {
    Write-Output 'gia installato.'
}

# Avvio automatico: dopo un blackout deve tornare su da solo, altrimenti la
# prima cosa che perdi e' proprio il modo per entrare a capire cosa e' successo.
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Write-Output ("servizio sshd: " + (Get-Service sshd).Status + " (avvio automatico)")

# --- 2. firewall ------------------------------------------------------------
Write-Output ''
Write-Output '== Firewall =='
$regola = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
if (-not $regola) {
    New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' `
        -DisplayName 'OpenSSH Server (sshd)' -Enabled True `
        -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    Write-Output 'regola creata per la porta 22.'
}
else {
    Write-Output 'regola gia presente.'
}

# --- 3. chiave pubblica -----------------------------------------------------
if ($PublicKey) {
    Write-Output ''
    Write-Output '== Chiave pubblica =='
    $chiave = $PublicKey.Trim()
    if ($chiave -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-)') {
        Write-Warning "Non sembra una chiave pubblica valida. Attese: ssh-ed25519 / ssh-rsa / ecdsa-sha2-*"
        exit 1
    }

    # Gli utenti amministratori NON usano ~/.ssh/authorized_keys: OpenSSH su
    # Windows li fa passare da un file comune, con permessi ristretti. Metterla
    # nel posto sbagliato e' l'errore piu' frequente, e fallisce in silenzio.
    $isAdminUser = (New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)

    if ($isAdminUser) {
        $keyFile = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
        Write-Output "utente amministratore -> uso $keyFile"
    }
    else {
        $sshDir = Join-Path $env:USERPROFILE '.ssh'
        if (-not (Test-Path $sshDir)) { New-Item -ItemType Directory -Path $sshDir -Force | Out-Null }
        $keyFile = Join-Path $sshDir 'authorized_keys'
    }

    $esistenti = @()
    if (Test-Path $keyFile) {
        $esistenti = @(Get-Content $keyFile -ErrorAction SilentlyContinue)
    }
    if ($esistenti -contains $chiave) {
        Write-Output 'chiave gia autorizzata.'
    }
    else {
        Add-Content -Path $keyFile -Value $chiave -Encoding ascii
        Write-Output 'chiave aggiunta.'
    }

    if ($isAdminUser) {
        # senza questi permessi sshd IGNORA il file, senza dirlo
        icacls $keyFile /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
        Write-Output 'permessi del file sistemati (senza, sshd lo ignora in silenzio).'
    }
}

# --- 4. password auth -------------------------------------------------------
if ($DisablePasswordAuth) {
    Write-Output ''
    Write-Output '== Autenticazione a password =='
    if (-not $PublicKey) {
        Write-Warning 'Rifiuto: disabilitare le password senza aver autorizzato una chiave ti chiude fuori dalla macchina.'
        Write-Warning 'Rilancia passando anche -PublicKey, e prova prima che l accesso a chiave funzioni.'
    }
    else {
        $cfgFile = Join-Path $env:ProgramData 'ssh\sshd_config'
        $backup = "$cfgFile.bak"
        if (-not (Test-Path $backup)) { Copy-Item $cfgFile $backup }
        $testo = Get-Content $cfgFile -Raw
        $testo = $testo -replace '(?m)^\s*#?\s*PasswordAuthentication\s+\w+', 'PasswordAuthentication no'
        if ($testo -notmatch '(?m)^PasswordAuthentication\s+no') {
            $testo += "`r`nPasswordAuthentication no`r`n"
        }
        Set-Content -Path $cfgFile -Value $testo -Encoding ascii
        Restart-Service sshd
        Write-Output "password disabilitate (backup in $backup)."
        Write-Warning 'PRIMA di chiudere questa sessione, apri un altro terminale e verifica che l accesso a chiave funzioni.'
    }
}

# --- 5. Tailscale -----------------------------------------------------------
if ($InstallTailscale) {
    Write-Output ''
    Write-Output '== Tailscale =='
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warning 'winget non disponibile. Scarica Tailscale a mano da https://tailscale.com/download/windows'
    }
    else {
        & winget install --id Tailscale.Tailscale --accept-source-agreements --accept-package-agreements
        Write-Output ''
        Write-Output 'Installato. Ora apri Tailscale dal menu Start e fai il login.'
        Write-Output 'Fai il login con lo STESSO account anche su telefono e portatile.'
    }
}

# --- riepilogo --------------------------------------------------------------
Write-Output ''
Write-Output ('=' * 66)
Write-Output '  COME COLLEGARSI'
Write-Output ('=' * 66)
$utente = $env:USERNAME
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1).IPAddress

Write-Output ''
Write-Output "Dalla rete di casa:"
Write-Output "    ssh $utente@$ip"
Write-Output ''
Write-Output 'Da fuori casa (dopo aver fatto il login su Tailscale):'
Write-Output "    ssh $utente@<nome-del-minipc-su-tailscale>"
Write-Output ''
Write-Output 'Da telefono: Termius (iOS/Android) o JuiceSSH (Android).'
Write-Output ''
if (-not $PublicKey) {
    Write-Output 'Consiglio: passa a autenticazione a chiave invece che a password.'
    Write-Output '  1. sul TUO portatile:   ssh-keygen -t ed25519'
    Write-Output '  2. copia il contenuto di ~/.ssh/id_ed25519.pub'
    Write-Output '  3. qui:  .\install-remote-shell.ps1 -PublicKey "ssh-ed25519 AAAA..."'
    Write-Output '  4. verifica che entri senza password, POI aggiungi -DisablePasswordAuth'
    Write-Output ''
}
Write-Output 'NON aprire la porta 22 sul router: usa Tailscale.'
Write-Output ''
