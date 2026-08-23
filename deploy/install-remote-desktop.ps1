<#
.SYNOPSIS
    Accende il desktop remoto (RDP) sul mini PC e lo limita alla rete privata.

.DESCRIPTION
    Serve a VEDERE e TOCCARE il mini PC da un altro computer: rispondere a un
    popup di Instagram, rifare un login scaduto, guardare un emulatore, far
    ripartire un task. Il terminale (install-remote-shell.ps1) e il bot
    Telegram (remote_control.py) coprono i comandi, non l'interfaccia grafica.

    PERCHE' RDP E NON VNC
    RDP e' gia' dentro Windows 11 Pro: niente software di terzi, traffico
    cifrato, autenticazione a livello di rete. Il client c'e' su ogni Windows
    (mstsc) e come app su Android, iOS e macOS.
    Differenza da VNC, da sapere: collegandosi in RDP la sessione viene
    "portata via" dal monitor fisico, che torna alla schermata di accesso. Sul
    mini PC non e' un problema perche' nessuno ci sta davanti, e i processi
    restano vivi anche dopo che ti scolleghi. Se invece serve vedere ESATTAMENTE
    quello che appare sul monitor fisico, allora serve un VNC.

    IL VINCOLO CHE FA FALLIRE TUTTO: LA PASSWORD
    Windows rifiuta gli accessi di rete (RDP compreso) agli account con password
    VUOTA. E' una policy predefinita, e fallisce senza spiegare il motivo.
    Se l'account non ha password questo script si ferma: la password va messa a
    mano, nessuno script deve toccarla.

    E SUBITO DOPO: L'AUTOLOGON
    Un account senza password fa il logon da solo all'avvio. Mettendo la
    password quel comportamento sparisce, e senza logon non c'e' sessione
    desktop: gli emulatori non partono e dopo un blackout il mini PC resta
    fermo alla schermata di accesso. Le due cose vanno fatte in coppia:
    password + autologon configurato (Sysinternals Autologon, che la salva
    cifrata). Vedi le note in install-autostart.ps1.

    RAGGIUNGIBILITA'
    Le regole del firewall vengono limitate alla rete locale e alla rete
    Tailscale (100.64.0.0/10). La porta 3389 NON va aperta sul router: RDP
    esposto su internet raccoglie tentativi di accesso automatici in
    continuazione.

.PARAMETER InstallaTailscale
    Installa Tailscale via winget (il login va poi fatto a mano).

.PARAMETER SoloTailscale
    Restringe RDP alla sola rete Tailscale, escludendo la rete locale.
    Da usare DOPO aver verificato che il collegamento via Tailscale funziona.

.PARAMETER IgnoraControlloPassword
    Salta il controllo sulla password vuota. Serve solo se il controllo da' un
    falso allarme (account con password ma con il flag "password non
    richiesta" attivo).

.PARAMETER Disattiva
    Rimette tutto com'era: RDP spento e regole del firewall disabilitate.

.EXAMPLE
    # PowerShell come amministratore
    .\install-remote-desktop.ps1

.EXAMPLE
    .\install-remote-desktop.ps1 -InstallaTailscale
#>
param(
    [switch]$InstallaTailscale,
    [switch]$SoloTailscale,
    [switch]$IgnoraControlloPassword,
    [switch]$Disattiva
)

$ErrorActionPreference = 'Stop'

# Rete CGNAT su cui Tailscale assegna gli indirizzi dei nodi.
$RETE_TAILSCALE = '100.64.0.0/10'

# I Name delle regole predefinite, non i DisplayName: i secondi sono tradotti,
# e su un Windows italiano cercarli in inglese non trova niente.
$REGOLE_RDP = @('RemoteDesktop-UserMode-In-TCP', 'RemoteDesktop-UserMode-In-UDP')

$CHIAVE_TS = 'HKLM:\System\CurrentControlSet\Control\Terminal Server'
$CHIAVE_RDPTCP = 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Warning 'Serve un PowerShell APERTO COME AMMINISTRATORE.'
    exit 1
}

# --- disattivazione ---------------------------------------------------------
if ($Disattiva) {
    Set-ItemProperty -Path $CHIAVE_TS -Name 'fDenyTSConnections' -Value 1
    foreach ($r in $REGOLE_RDP) {
        if (Get-NetFirewallRule -Name $r -ErrorAction SilentlyContinue) {
            Disable-NetFirewallRule -Name $r
        }
    }
    Write-Output 'Desktop remoto disattivato e regole del firewall disabilitate.'
    exit 0
}

# --- 1. la password c'e'? ---------------------------------------------------
Write-Output '== Password dell account =='
if ($IgnoraControlloPassword) {
    Write-Output 'controllo saltato su richiesta.'
}
else {
    # PasswordRequired viene da Get-LocalUser, non dall'output tradotto di
    # "net user": cosi' le etichette in italiano non c'entrano niente.
    $utente = $null
    try { $utente = Get-LocalUser -Name $env:USERNAME -ErrorAction Stop } catch {}

    if (-not $utente) {
        Write-Warning "Non riesco a leggere l account $env:USERNAME : salto il controllo."
    }
    elseif (-not $utente.PasswordRequired) {
        Write-Warning "L account '$env:USERNAME' risulta SENZA PASSWORD."
        Write-Output ''
        Write-Output 'Windows non accetta accessi di rete dagli account senza password:'
        Write-Output 'RDP fallirebbe senza dirti il motivo. Mettila a mano, poi rilancia:'
        Write-Output ''
        Write-Output '    Ctrl+Alt+Canc  ->  Cambia password'
        Write-Output '  oppure da questo terminale:'
        Write-Output "    net user $env:USERNAME *"
        Write-Output ''
        Write-Warning 'SUBITO DOPO va configurato l autologon, altrimenti al prossimo'
        Write-Warning 'riavvio il mini PC resta alla schermata di accesso e non parte niente.'
        Write-Output '  Sysinternals Autologon:'
        Write-Output '  https://learn.microsoft.com/sysinternals/downloads/autologon'
        exit 1
    }
    else {
        Write-Output 'ok, la password c e.'
    }
}

# --- 2. accensione di RDP ---------------------------------------------------
Write-Output ''
Write-Output '== Desktop remoto =='
Set-ItemProperty -Path $CHIAVE_TS -Name 'fDenyTSConnections' -Value 0
Write-Output 'connessioni remote consentite.'

# NLA: l'autenticazione avviene PRIMA di aprire una sessione grafica. Senza,
# chiunque arrivi sulla 3389 si trova una schermata di login da attaccare.
Set-ItemProperty -Path $CHIAVE_RDPTCP -Name 'UserAuthentication' -Value 1
Write-Output 'autenticazione a livello di rete (NLA) attiva.'

$svc = Get-Service TermService -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.StartType -eq 'Disabled') { Set-Service TermService -StartupType Manual }
    if ($svc.Status -ne 'Running') { Start-Service TermService }
    Write-Output ('servizio TermService: ' + (Get-Service TermService).Status)
}

# --- 3. firewall ------------------------------------------------------------
Write-Output ''
Write-Output '== Firewall =='
if ($SoloTailscale) {
    $ambito = @($RETE_TAILSCALE)
    Write-Output "ambito: solo rete Tailscale ($RETE_TAILSCALE)"
}
else {
    $ambito = @('LocalSubnet', $RETE_TAILSCALE)
    Write-Output "ambito: rete locale + rete Tailscale ($RETE_TAILSCALE)"
}

foreach ($r in $REGOLE_RDP) {
    if (-not (Get-NetFirewallRule -Name $r -ErrorAction SilentlyContinue)) {
        Write-Warning "regola $r non presente su questo Windows, la salto."
        continue
    }
    Enable-NetFirewallRule -Name $r
    Set-NetFirewallRule -Name $r -RemoteAddress $ambito
    Write-Output "  $r : abilitata e limitata all ambito."
}

# --- 4. energia -------------------------------------------------------------
# Un mini PC che va in sospensione si porta dietro gli emulatori, e da remoto
# non c'e' nessun modo di svegliarlo.
Write-Output ''
Write-Output '== Risparmio energia =='
& powercfg /change standby-timeout-ac 0
& powercfg /change hibernate-timeout-ac 0
& powercfg /change disk-timeout-ac 0
Write-Output 'sospensione, ibernazione e spegnimento dischi disattivati (rete elettrica).'

# --- 5. Tailscale -----------------------------------------------------------
if ($InstallaTailscale) {
    Write-Output ''
    Write-Output '== Tailscale =='
    if (Get-Command tailscale -ErrorAction SilentlyContinue) {
        Write-Output 'gia presente.'
    }
    elseif (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Warning 'winget non disponibile: scarica Tailscale da https://tailscale.com/download/windows'
    }
    else {
        & winget install --id Tailscale.Tailscale --accept-source-agreements --accept-package-agreements
        Write-Output 'installato. Apri Tailscale dal menu Start e fai il login.'
        Write-Output 'Poi fai il login con LO STESSO account sull altro PC.'
    }
}

# --- riepilogo --------------------------------------------------------------
Write-Output ''
Write-Output ('=' * 66)
Write-Output '  COME COLLEGARSI'
Write-Output ('=' * 66)

$ipLocale = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and
                   $_.IPAddress -notlike '169.254.*' -and
                   $_.IPAddress -notlike '100.*' } |
    Select-Object -First 1).IPAddress

Write-Output ''
Write-Output 'Dalla rete di casa, dall altro PC:'
Write-Output "    mstsc /v:$ipLocale"
Write-Output ''

$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if ($ts) {
    $ipTs = & $ts.Source ip -4 2>$null | Select-Object -First 1
    if ($ipTs) {
        Write-Output 'Da fuori casa (Tailscale collegato su tutti e due i lati):'
        Write-Output ('    mstsc /v:' + $ipTs.ToString().Trim())
    }
    else {
        Write-Output 'Tailscale installato ma non collegato: apri l app e fai il login.'
    }
}
else {
    Write-Output 'Da fuori casa: serve Tailscale (rilancia con -InstallaTailscale).'
}

Write-Output ''
Write-Output 'Utente da mettere nella finestra di RDP:'
Write-Output ('    ' + $env:COMPUTERNAME + '\' + $env:USERNAME)
Write-Output ''
Write-Output 'DUE COSE CHE NESSUNO SCRIPT PUO FARE AL POSTO TUO:'
Write-Output '  1. autologon di Windows, se hai appena messo la password'
Write-Output '     (senza, dopo un blackout non riparte niente)'
Write-Output '  2. nel BIOS: accensione automatica al ritorno della corrente'
Write-Output '     (voce "Restore on AC Power Loss" / "AC Back" -> "Power On")'
Write-Output ''
Write-Output 'NON aprire la porta 3389 sul router.'
Write-Output ''
