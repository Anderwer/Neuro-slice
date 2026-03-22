[CmdletBinding()]
param(
    [switch]$SkipAudio,
    [switch]$SkipDev,
    [switch]$SkipGpu,
    [switch]$SkipLegacy,
    [switch]$NoPause,
    [string]$WslHttpProxy = "",
    [string]$WslHttpsProxy = "",
    [string]$WslPipIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [string]$WslAptMirror = "https://mirrors.tuna.tsinghua.edu.cn/ubuntu"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Info {
    param([string]$Message)
    Write-Host "  $Message" -ForegroundColor Gray
}

function Write-Success {
    param([string]$Message)
    Write-Host "  $Message" -ForegroundColor Green
}

function Write-WarnText {
    param([string]$Message)
    Write-Host "  $Message" -ForegroundColor Yellow
}

function Test-CommandExists {
    param([Parameter(Mandatory = $true)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-WSLCommandPrefix {
    $parts = @()

    if ($WslHttpProxy) {
        $parts += "export http_proxy='$WslHttpProxy'"
    }

    if ($WslHttpsProxy) {
        $parts += "export https_proxy='$WslHttpsProxy'"
    }

    if ($WslPipIndexUrl) {
        $parts += "export PIP_INDEX_URL='$WslPipIndexUrl'"
    }

    if ($parts.Count -eq 0) {
        return ""
    }

    return ($parts -join "; ") + "; "
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $PSScriptRoot
    )

    Write-Info "$FilePath $($Arguments -join ' ')"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -NoNewWindow `
        -Wait `
        -PassThru

    if ($process.ExitCode -ne 0) {
        throw "Command failed with exit code $($process.ExitCode): $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-WSLBash {
    param(
        [Parameter(Mandatory = $true)][string]$Distro,
        [Parameter(Mandatory = $true)][string]$Command,
        [switch]$AsRoot,
        [switch]$IgnoreExitCode,
        [string]$WorkingDirectory = $PSScriptRoot
    )

    $prefixedCommand = "$(Get-WSLCommandPrefix)$Command"

    $display = "wsl -d $Distro "
    if ($AsRoot) {
        $display += "-u root "
    }
    $display += "-- bash -lc $prefixedCommand"
    Write-Info $display

    $cmdLine = "wsl -d $Distro "
    if ($AsRoot) {
        $cmdLine += "-u root "
    }
    $escapedCommand = $prefixedCommand.Replace('"', '\"')
    $cmdLine += "-- bash -lc `"$escapedCommand`""

    Push-Location $WorkingDirectory
    try {
        cmd /d /c "$cmdLine 2>nul"

        if (-not $IgnoreExitCode -and $LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $display"
        }
    }
    finally {
        Pop-Location
    }
}

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $MyInvocation.PSScriptRoot
    if (-not $scriptDir) {
        $scriptDir = Get-Location
    }
    return $scriptDir
}

function Convert-WindowsPathToWSL {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    $full = [System.IO.Path]::GetFullPath($Path)
    $drive = $full.Substring(0, 1).ToLower()
    $rest = $full.Substring(2).Replace("\", "/")
    return "/mnt/$drive$rest"
}

function Get-WSLValue {
    param(
        [Parameter(Mandatory = $true)][string]$Distro,
        [Parameter(Mandatory = $true)][string]$Expression
    )

    $raw = cmd /d /c "wsl -d $Distro -- bash -lc ""printf '%s' $Expression"" 2>nul"
    if ($null -eq $raw) {
        return ""
    }

    $text = "$raw"
    if (-not $text) {
        return ""
    }

    return ($text | Select-Object -First 1).ToString().Trim()
}

function Install-Uv {
    Write-Step "Checking uv"

    if (Test-CommandExists "uv") {
        $uvVersion = (& uv --version) 2>$null
        Write-Success "uv already installed: $uvVersion"
        return
    }

    Write-WarnText "uv is not installed."
    Write-Host ""
    $choice = Read-Host "Install uv now? [Y/n]"
    if ($choice -and $choice.Trim().ToLower() -eq "n") {
        throw "uv is required to continue."
    }

    if (-not (Test-CommandExists "winget")) {
        throw "uv is not installed and winget is unavailable. Please install uv manually: https://docs.astral.sh/uv/getting-started/installation/"
    }

    Write-Info "Installing uv with winget..."
    Invoke-Checked -FilePath "winget" -Arguments @("install", "--id", "Astral-sh.uv", "-e")

    if (-not (Test-CommandExists "uv")) {
        $userPath = Join-Path $env:USERPROFILE ".local\bin"
        if (Test-Path $userPath) {
            $env:PATH = "$userPath;$env:PATH"
        }
    }

    if (-not (Test-CommandExists "uv")) {
        throw "uv installation finished, but 'uv' is still not available in PATH. Open a new terminal and run this script again."
    }

    $uvVersion = (& uv --version) 2>$null
    Write-Success "uv installed successfully: $uvVersion"
}

function Test-FFmpeg {
    Write-Step "Checking ffmpeg"

    $ffmpegOk = Test-CommandExists "ffmpeg"
    $ffprobeOk = Test-CommandExists "ffprobe"

    if ($ffmpegOk -and $ffprobeOk) {
        $ffmpegVersion = (& ffmpeg -version | Select-Object -First 1) 2>$null
        Write-Success "ffmpeg detected: $ffmpegVersion"
        return
    }

    Write-WarnText "ffmpeg and/or ffprobe are missing."
    Write-WarnText "This project requires FFmpeg for audio extraction and clip export."
    Write-WarnText "Recommended install options:"
    Write-WarnText "  1. winget install Gyan.FFmpeg"
    Write-WarnText "  2. choco install ffmpeg"
    Write-WarnText "  3. manual install from https://ffmpeg.org/download.html"
    Write-Host ""

    if (Test-CommandExists "winget") {
        $choice = Read-Host "Install ffmpeg with winget now? [Y/n]"
        if (-not $choice -or $choice.Trim().ToLower() -ne "n") {
            Invoke-Checked -FilePath "winget" -Arguments @("install", "--id", "Gyan.FFmpeg", "-e")

            $ffmpegOk = Test-CommandExists "ffmpeg"
            $ffprobeOk = Test-CommandExists "ffprobe"
            if ($ffmpegOk -and $ffprobeOk) {
                $ffmpegVersion = (& ffmpeg -version | Select-Object -First 1) 2>$null
                Write-Success "ffmpeg installed successfully: $ffmpegVersion"
                return
            }

            Write-WarnText "ffmpeg installation may require a new terminal session before PATH updates are visible."
        }
    }

    Write-WarnText "Continuing without ffmpeg installation verification."
    Write-WarnText "If analyze/export commands fail later, install ffmpeg first."
}

function Ensure-Python {
    Write-Step "Checking Python"

    if (Test-CommandExists "python") {
        $versionLine = (& python --version) 2>$null
        Write-Success "Python detected: $versionLine"
        return
    }

    Write-WarnText "Python is not available in PATH."
    Write-WarnText "uv can manage project environments, but having Python available is still recommended."
    Write-WarnText "Please install Python 3.11+ if the next steps fail."
}

function Ensure-WSLAvailable {
    if (Test-CommandExists "wsl") {
        Write-Success "WSL command detected."
        return
    }

    Write-WarnText "WSL was not found on PATH."
    Write-WarnText "Attempting to install Ubuntu through WSL..."

    if (-not (Test-CommandExists "winget")) {
        throw "WSL is not available and winget is unavailable. Install WSL manually, reboot if required, then run setup.ps1 again."
    }

    $process = Start-Process `
        -FilePath "wsl" `
        -ArgumentList @("--install", "-d", "Ubuntu") `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -ErrorAction SilentlyContinue

    if ($null -eq $process) {
        throw "Failed to launch `wsl --install -d Ubuntu`. Try running PowerShell as Administrator, then run setup.ps1 again."
    }

    if ($process.ExitCode -ne 0) {
        throw "WSL installation did not complete successfully. Administrator privileges and/or a reboot may be required. Reboot Windows if prompted, then rerun setup.ps1."
    }

    if (-not (Test-CommandExists "wsl")) {
        throw "WSL installation was attempted, but `wsl` is still unavailable. Reboot Windows and run setup.ps1 again."
    }

    Write-Success "WSL installation command completed."
}

function Ensure-UbuntuDistro {
    Write-Info "Checking whether Ubuntu is already available and initialized..."
    $probe = cmd /d /c "wsl -d Ubuntu -- bash -lc ""printf ready"" 2>nul"

    if ("$probe" -match "ready") {
        Write-Success "Ubuntu is available and initialized."
        return
    }

    Write-WarnText "Ubuntu is not ready yet. Attempting to install it through WSL..."
    $process = Start-Process `
        -FilePath "wsl" `
        -ArgumentList @("--install", "-d", "Ubuntu") `
        -NoNewWindow `
        -Wait `
        -PassThru

    if ($process.ExitCode -ne 0) {
        throw "Ubuntu installation inside WSL did not complete successfully. A reboot or first-launch distro setup may be required."
    }

    Write-Success "Ubuntu installation command completed."
    Write-Info "Checking whether Ubuntu has completed first-launch initialization..."
    $probe = cmd /d /c "wsl -d Ubuntu -- bash -lc ""printf ready"" 2>nul"
    if ("$probe" -notmatch "ready") {
        throw "Ubuntu exists but is not ready for automation yet. Launch Ubuntu once (for example: `wsl -d Ubuntu`), complete any first-run user setup, then rerun setup.ps1."
    }

    Write-Success "Ubuntu is initialized and ready."
}

function Ensure-WSLLegacyDetectorEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Write-Step "Provisioning WSL legacy detector environment"

    $runtimeDir = Join-Path $RepoRoot "runtime"
    $runtimeConfig = Join-Path $runtimeDir "local_envs.json"
    $mainPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $legacyDetectorScript = Join-Path $runtimeDir "detect_segments_tf.py"

    if (-not (Test-Path $runtimeDir)) {
        New-Item -ItemType Directory -Path $runtimeDir | Out-Null
        Write-Info "Created runtime directory: $runtimeDir"
    }

    if (-not (Test-Path $legacyDetectorScript)) {
        Write-WarnText "Legacy detector script was not found at:"
        Write-WarnText "  $legacyDetectorScript"
        Write-WarnText "The WSL detector environment can still be prepared, but detection will not work until detect_segments_tf.py exists in the runtime directory."
    }

    Ensure-WSLAvailable
    Ensure-UbuntuDistro

    $wslDistro = "Ubuntu"
    $wslCurrentUser = Get-WSLValue -Distro $wslDistro -Expression '$USER'
    $wslHome = Get-WSLValue -Distro $wslDistro -Expression '$HOME'

    if (-not $wslCurrentUser -or -not $wslHome) {
        throw "Failed to resolve the current WSL user and home directory. Launch Ubuntu once and make sure it initializes successfully, then rerun setup.ps1."
    }

    $wslLegacyRoot = "$wslHome/.neuro-slice-legacy"
    $wslLegacyPython = "$wslLegacyRoot/bin/python"
    $wslLegacyDetectorRoot = "$wslHome/neuro-slice-runtime"
    $wslLegacyDetectorScript = "$wslLegacyDetectorRoot/detect_segments_tf.py"
    $legacyWrapperScript = Join-Path $runtimeDir "detect_segments_wsl.sh"
    $wslLegacyWrapperScript = "$wslLegacyDetectorRoot/detect_segments_wsl.sh"

    if ($WslAptMirror) {
        $normalizedAptMirror = $WslAptMirror.TrimEnd("/")
        $aptInstallCommand = "if [ -f /etc/apt/sources.list ]; then sed -i 's|http://archive.ubuntu.com/ubuntu|$normalizedAptMirror|g; s|http://security.ubuntu.com/ubuntu|$normalizedAptMirror|g; s|http://ports.ubuntu.com/ubuntu-ports|$normalizedAptMirror|g' /etc/apt/sources.list; fi; if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then sed -i 's|http://archive.ubuntu.com/ubuntu|$normalizedAptMirror|g; s|http://security.ubuntu.com/ubuntu|$normalizedAptMirror|g; s|http://ports.ubuntu.com/ubuntu-ports|$normalizedAptMirror|g' /etc/apt/sources.list.d/ubuntu.sources; fi; apt-get update && apt-get install -y python3 python3-venv python3-pip ffmpeg"
    }
    else {
        $aptInstallCommand = "apt-get update && apt-get install -y python3 python3-venv python3-pip ffmpeg"
    }
    $createVenvCommand = "python3 -m venv '$wslLegacyRoot'"
    $upgradeLegacyPipCommand = "'$wslLegacyPython' -m pip install --upgrade pip setuptools wheel"
    $installLegacyDepsCommand = "'$wslLegacyPython' -m pip install inaSpeechSegmenter"

    Write-Info "Ensuring Python, venv support, and ffmpeg inside WSL..."
    Invoke-WSLBash -Distro $wslDistro -AsRoot -IgnoreExitCode -Command $aptInstallCommand -WorkingDirectory $RepoRoot

    Write-Info "Creating / refreshing WSL legacy detector virtual environment..."
    Invoke-WSLBash -Distro $wslDistro -IgnoreExitCode -Command $createVenvCommand -WorkingDirectory $RepoRoot

    Write-Info "Upgrading WSL legacy detector packaging tools..."
    Invoke-WSLBash -Distro $wslDistro -IgnoreExitCode -Command $upgradeLegacyPipCommand -WorkingDirectory $RepoRoot

    Write-Info "Installing WSL legacy detector dependencies..."
    Invoke-WSLBash -Distro $wslDistro -IgnoreExitCode -Command $installLegacyDepsCommand -WorkingDirectory $RepoRoot

    if (Test-Path $legacyDetectorScript) {
        $wslSource = Convert-WindowsPathToWSL -Path $legacyDetectorScript
        $copyDetectorCommand = "mkdir -p '$wslLegacyDetectorRoot' && cp '$wslSource' '$wslLegacyDetectorScript' && sed -i 's/\r$//' '$wslLegacyDetectorScript'"
        Write-Info "Copying legacy detector script into WSL runtime directory..."
        Invoke-WSLBash -Distro $wslDistro -IgnoreExitCode -Command $copyDetectorCommand -WorkingDirectory $RepoRoot
    }
    else {
        Write-WarnText "The WSL legacy detector script copy step was skipped because detect_segments_tf.py is missing on the Windows side."
    }

    if (Test-Path $legacyWrapperScript) {
        $wslWrapperSource = Convert-WindowsPathToWSL -Path $legacyWrapperScript
        $copyWrapperCommand = "mkdir -p '$wslLegacyDetectorRoot' && cp '$wslWrapperSource' '$wslLegacyWrapperScript' && sed -i 's/\r$//' '$wslLegacyWrapperScript' && chmod +x '$wslLegacyWrapperScript'"
        Write-Info "Copying WSL legacy detector wrapper script into WSL runtime directory..."
        Invoke-WSLBash -Distro $wslDistro -IgnoreExitCode -Command $copyWrapperCommand -WorkingDirectory $RepoRoot
    }
    else {
        Write-WarnText "The WSL legacy detector wrapper copy step was skipped because detect_segments_wsl.sh is missing on the Windows side."
    }

    Write-Info "Validating the resulting WSL legacy detector runtime..."
    $pythonProbe = cmd /d /c "wsl -d $wslDistro -- bash -lc ""test -x '$wslLegacyPython' && printf ready"" 2>nul"
    $detectorProbe = cmd /d /c "wsl -d $wslDistro -- bash -lc ""test -f '$wslLegacyDetectorScript' && printf ready"" 2>nul"
    $wrapperProbe = cmd /d /c "wsl -d $wslDistro -- bash -lc ""test -f '$wslLegacyWrapperScript' && printf ready"" 2>nul"

    $pythonReady = "$pythonProbe" -match "ready"
    $detectorReady = "$detectorProbe" -match "ready"
    $wrapperReady = "$wrapperProbe" -match "ready"

    if (-not $pythonReady) {
        throw "WSL legacy detector python was not found after setup: $wslLegacyPython"
    }

    if (-not $detectorReady) {
        throw "WSL legacy detector script was not found after setup: $wslLegacyDetectorScript"
    }

    if (-not $wrapperReady) {
        throw "WSL legacy detector wrapper script was not found after setup: $wslLegacyWrapperScript"
    }

    Write-Success "WSL legacy detector runtime validation succeeded."

    $runtimePayload = @{
        repo_root = $RepoRoot
        main_python = ".venv/Scripts/python.exe"
        main_python_absolute = $mainPython
        detector_backend = "legacy-wsl"
        wsl_distro = $wslDistro
        wsl_user = $wslCurrentUser
        wsl_home = $wslHome
        legacy_wsl_python = $wslLegacyPython
        legacy_wsl_detector_script = $wslLegacyDetectorScript
        legacy_wsl_wrapper_script = $wslLegacyWrapperScript
        windows_fallback_legacy_python = ".venv_legacy/Scripts/python.exe"
        windows_fallback_legacy_python_absolute = (Join-Path $RepoRoot ".venv_legacy\Scripts\python.exe")
        windows_fallback_legacy_detector_script = "runtime/detect_segments_tf.py"
        windows_fallback_legacy_detector_script_absolute = $legacyDetectorScript
        windows_fallback_legacy_wrapper_script = "runtime/detect_segments_wsl.sh"
        windows_fallback_legacy_wrapper_script_absolute = $legacyWrapperScript
        legacy_script_exists = (Test-Path $legacyDetectorScript)
        legacy_wrapper_script_exists = (Test-Path $legacyWrapperScript)
        legacy_python_exists = $true
    } | ConvertTo-Json -Depth 4

    Set-Content -Path $runtimeConfig -Value $runtimePayload -Encoding UTF8
    Write-Success "Legacy detector runtime configuration written: $runtimeConfig"
    Write-Info "Main Python: $mainPython"
    Write-Info "WSL distro: $wslDistro"
    Write-Info "WSL user: $wslCurrentUser"
    Write-Info "WSL home: $wslHome"
    Write-Info "WSL legacy detector Python: $wslLegacyPython"
    Write-Info "WSL legacy detector script: $wslLegacyDetectorScript"
    Write-Info "WSL legacy detector wrapper script: $wslLegacyWrapperScript"
}

function Ensure-LegacyDetectorEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Ensure-WSLLegacyDetectorEnvironment -RepoRoot $RepoRoot
}

function Sync-Project {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Write-Step "Preparing virtual environment"
    $mainEnvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $mainEnvPython) {
        Write-Info "Main project virtual environment already exists. Reusing .venv"
    }
    else {
        Invoke-Checked -FilePath "uv" -Arguments @("venv") -WorkingDirectory $RepoRoot
    }

    $syncArguments = @("sync")

    if (-not $SkipDev) {
        $syncArguments += @("--group", "dev")
    }
    else {
        Write-Info "Skipping dev dependencies."
    }

    if (-not $SkipAudio) {
        $syncArguments += @("--extra", "audio")
    }
    else {
        Write-Info "Skipping audio dependencies."
    }

    if (-not $SkipGpu) {
        $syncArguments += @("--extra", "gpu")
    }
    else {
        Write-Info "Skipping GPU runtime dependencies."
    }

    Write-Step "Installing main environment dependencies"
    $syncArguments += @("--extra", "web")
    Invoke-Checked -FilePath "uv" -Arguments $syncArguments -WorkingDirectory $RepoRoot

    if (-not $SkipLegacy) {
        Ensure-LegacyDetectorEnvironment -RepoRoot $RepoRoot
    }
    else {
        Write-Info "Skipping legacy detector environment provisioning."
    }
}

function Show-NextSteps {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Write-Host ""
    Write-Host "Setup complete." -ForegroundColor Green
    Write-Host ""
    Write-Host "Project root:" -ForegroundColor Cyan
    Write-Host "  $RepoRoot"
    Write-Host ""
    Write-Host "Next commands:" -ForegroundColor Cyan
    Write-Host "  uv run neuro-slice --help"
    Write-Host "  uv run neuro-slice analyze path\to\vod.mp4 --config examples\sample_config.toml --output output\"
    Write-Host "  uv run pytest tests\test_manifest_export.py"
    Write-Host ""
    Write-Host "Tips:" -ForegroundColor Cyan
    Write-Host "  - Edit examples\sample_config.toml before real runs."
    Write-Host "  - Install ffmpeg if analyze/export commands complain about missing binaries."
    Write-Host "  - GPU runtime dependencies are installed by default for Windows NVIDIA systems."
    Write-Host "  - Web UI dependencies are also installed by default."
    Write-Host "  - The setup now prefers a WSL-oriented legacy detector backend."
    Write-Host "  - If WSL or Ubuntu is missing, setup will try to install them automatically."
    Write-Host "  - Some machines may still require administrator privileges, a reboot, or one manual first launch of Ubuntu."
    Write-Host "  - If setup reports that Ubuntu is not initialized yet, run: wsl -d Ubuntu"
    Write-Host "  - Complete the first-run Linux user setup, then rerun setup.ps1."
    Write-Host "  - Domestic mirror defaults are enabled for WSL by default:"
    Write-Host "      - WSL pip index: https://pypi.tuna.tsinghua.edu.cn/simple"
    Write-Host "      - WSL apt mirror: https://mirrors.tuna.tsinghua.edu.cn/ubuntu"
    Write-Host "  - You can still override them if needed:"
    Write-Host "      -WslHttpProxy  http://127.0.0.1:7890"
    Write-Host "      -WslHttpsProxy http://127.0.0.1:7890"
    Write-Host "      -WslPipIndexUrl https://pypi.org/simple"
    Write-Host "      -WslAptMirror https://mirrors.tuna.tsinghua.edu.cn/ubuntu/"
    Write-Host "  - Re-run this script with -SkipAudio, -SkipDev, -SkipGpu, or -SkipLegacy if you want a lighter install."
    Write-Host ""
}

try {
    $repoRoot = Get-RepoRoot
    Set-Location $repoRoot

    Write-Host "Neuro-slice Windows setup" -ForegroundColor Magenta
    Write-Host "Repository: $repoRoot" -ForegroundColor Gray

    Install-Uv
    Ensure-Python
    Test-FFmpeg
    Sync-Project -RepoRoot $RepoRoot
    Show-NextSteps -RepoRoot $RepoRoot
}
catch {
    Write-Host ""
    Write-Host "Setup failed." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

if (-not $NoPause) {
    Write-Host ""
    Read-Host "Press Enter to exit"
}