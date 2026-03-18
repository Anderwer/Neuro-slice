[CmdletBinding()]
param(
    [switch]$SkipAudio,
    [switch]$SkipDev,
    [switch]$SkipGpu,
    [switch]$SkipLegacy,
    [switch]$NoPause
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

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $MyInvocation.PSScriptRoot
    if (-not $scriptDir) {
        $scriptDir = Get-Location
    }
    return $scriptDir
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

function Ensure-LegacyDetectorEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Write-Step "Provisioning legacy detector environment"

    $runtimeDir = Join-Path $RepoRoot "runtime"
    $legacyEnvPath = Join-Path $RepoRoot ".venv_legacy"
    $legacyPython = Join-Path $legacyEnvPath "Scripts\python.exe"
    $legacyDetectorScript = Join-Path $runtimeDir "detect_segments_tf.py"
    $runtimeConfig = Join-Path $runtimeDir "local_envs.json"

    if (-not (Test-Path $runtimeDir)) {
        New-Item -ItemType Directory -Path $runtimeDir | Out-Null
        Write-Info "Created runtime directory: $runtimeDir"
    }

    if (-not (Test-Path $legacyEnvPath)) {
        Write-Info "Creating legacy detector virtual environment..."
        Invoke-Checked -FilePath "python" -Arguments @("-m", "venv", ".venv_legacy") -WorkingDirectory $RepoRoot
    }
    else {
        Write-Info "Legacy detector virtual environment already exists."
    }

    if (-not (Test-Path $legacyPython)) {
        throw "Legacy detector Python was not found after creating .venv_legacy: $legacyPython"
    }

    Write-Info "Upgrading legacy detector packaging tools..."
    Invoke-Checked -FilePath $legacyPython -Arguments @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel") -WorkingDirectory $RepoRoot

    Write-Info "Installing legacy detector dependencies..."
    Invoke-Checked -FilePath $legacyPython -Arguments @("-m", "pip", "install", "inaSpeechSegmenter") -WorkingDirectory $RepoRoot

    if (-not (Test-Path $legacyDetectorScript)) {
        Write-WarnText "Legacy detector script was not found at:"
        Write-WarnText "  $legacyDetectorScript"
        Write-WarnText "The runtime environment has been prepared, but detection will not work until detect_segments_tf.py is placed in the runtime directory."
    }

    $mainPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $runtimePayload = @{
        repo_root = $RepoRoot
        main_python = ".venv/Scripts/python.exe"
        main_python_absolute = $mainPython
        legacy_python = ".venv_legacy/Scripts/python.exe"
        legacy_python_absolute = $legacyPython
        legacy_detector_script = "runtime/detect_segments_tf.py"
        legacy_detector_script_absolute = $legacyDetectorScript
        detector_backend = "legacy-subprocess"
        legacy_script_exists = (Test-Path $legacyDetectorScript)
        legacy_python_exists = (Test-Path $legacyPython)
    } | ConvertTo-Json -Depth 4

    Set-Content -Path $runtimeConfig -Value $runtimePayload -Encoding UTF8
    Write-Success "Legacy detector runtime configuration written: $runtimeConfig"
    Write-Info "Main Python: $mainPython"
    Write-Info "Legacy detector Python: $legacyPython"
    Write-Info "Legacy detector script: $legacyDetectorScript"
}

function Sync-Project {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot
    )

    Write-Step "Preparing virtual environment"
    Invoke-Checked -FilePath "uv" -Arguments @("venv") -WorkingDirectory $RepoRoot

    Write-Step "Installing base dependencies"
    Invoke-Checked -FilePath "uv" -Arguments @("sync") -WorkingDirectory $RepoRoot

    if (-not $SkipDev) {
        Write-Step "Installing development dependencies"
        Invoke-Checked -FilePath "uv" -Arguments @("sync", "--group", "dev") -WorkingDirectory $RepoRoot
    }
    else {
        Write-Info "Skipping dev dependencies."
    }

    if (-not $SkipAudio) {
        Write-Step "Installing optional audio dependencies"
        Invoke-Checked -FilePath "uv" -Arguments @("sync", "--extra", "audio") -WorkingDirectory $RepoRoot
    }
    else {
        Write-Info "Skipping audio dependencies."
    }

    if (-not $SkipGpu) {
        Write-Step "Installing GPU runtime dependencies"
        Invoke-Checked -FilePath "uv" -Arguments @("sync", "--extra", "gpu") -WorkingDirectory $RepoRoot
    }
    else {
        Write-Info "Skipping GPU runtime dependencies."
    }

    Write-Step "Installing Web UI dependencies"
    Invoke-Checked -FilePath "uv" -Arguments @("sync", "--extra", "web") -WorkingDirectory $RepoRoot

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
    Write-Host "  - A legacy detector environment is also prepared by default in .venv_legacy."
    Write-Host "  - Place detect_segments_tf.py in the runtime directory if you want the legacy detector backend to work."
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