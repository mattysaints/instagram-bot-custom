<#
.SYNOPSIS
    Funzioni condivise dagli script di deploy.
#>

function Get-BotPython {
    <#
    .SYNOPSIS
        Trova l'interprete Python con cui lanciare il bot.

    .DESCRIPTION
        Ordine di ricerca:
          1. $env:IGBOT_PYTHON, se punta a un file esistente
          2. <repo>\.venv\Scripts\python.exe   (layout di SETUP.md, quello del mini PC)
          3. <repo>\venv\Scripts\python.exe    (nome alternativo diffuso)

        Il punto 1 serve quando il branch e' un git worktree: i worktree non
        hanno un venv proprio e condividono quello del clone principale.
        In quel caso, prima di installare l'autostart:
            setx IGBOT_PYTHON "C:\...\instagram-bot-custom\.venv\Scripts\python.exe"

        Non c'e' fallback sul `python` di sistema: girerebbe senza uiautomator2
        e fallirebbe a meta' sessione invece che subito, che e' molto peggio da
        diagnosticare su una macchina che sta in un'altra stanza.
    #>
    param(
        [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
    )

    if ($env:IGBOT_PYTHON -and (Test-Path $env:IGBOT_PYTHON)) {
        return $env:IGBOT_PYTHON
    }
    foreach ($rel in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe')) {
        $candidate = Join-Path $RepoRoot $rel
        if (Test-Path $candidate) { return $candidate }
    }

    throw ("Nessun virtualenv trovato in $RepoRoot (.venv o venv). " +
           "Crealo come da SETUP.md, oppure imposta IGBOT_PYTHON sul python.exe " +
           "del venv da usare.")
}

function Get-SdkTool {
    <#
    .SYNOPSIS
        Trova un eseguibile dell'SDK Android partendo dal percorso relativo
        alla radice dell'SDK (es. 'platform-tools\adb.exe').

    .DESCRIPTION
        Ordine di ricerca: ANDROID_HOME, ANDROID_SDK_ROOT, il percorso
        predefinito di Android Studio, infine il PATH.

        Sta qui e non nei singoli script perche' la usano sia start-account.ps1
        sia gli strumenti di ispezione: adb e emulator vanno cercati allo
        stesso modo, altrimenti si finisce per parlare a due SDK diversi.
    #>
    param([Parameter(Mandatory = $true)][string]$Relative)

    $roots = @(
        $env:ANDROID_HOME,
        $env:ANDROID_SDK_ROOT,
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk')
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($r in $roots) {
        $candidate = Join-Path $r $Relative
        if (Test-Path $candidate) { return $candidate }
    }

    $leaf = Split-Path -Leaf $Relative
    $cmd = Get-Command $leaf -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    throw "Non trovo $Relative. Imposta ANDROID_HOME o aggiungi l'SDK al PATH."
}
