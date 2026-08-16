# Spouští všechny lokální služby Jarvise z disku A: a otevře HUD rozhraní.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$env:OPENJARVIS_HOME = $root
$env:OLLAMA_MODELS = "$root\runtime\ollama-models"
$env:HF_HOME = "$root\runtime\huggingface"
$env:PLAYWRIGHT_BROWSERS_PATH = "$root\runtime\ms-playwright"
$env:TEMP = "$root\runtime\temp"
$env:TMP = "$root\runtime\temp"
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

function Test-Port([int]$Port) {
    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

$ollamaPath = "$root\runtime\ollama\ollama.exe"
if (-not (Test-Path -LiteralPath $ollamaPath)) {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollamaCommand) { throw "Ollama nebyla nalezena. Nejdříve spusťte install.ps1." }
    $ollamaPath = $ollamaCommand.Source
}
if (-not (Test-Port 11434)) {
    Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WorkingDirectory $root -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
if (-not (Test-Port 8000)) {
    Start-Process -FilePath "$root\src\.venv\Scripts\jarvis.exe" -ArgumentList "serve", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory "$root\src" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}
$pythonPath = "$root\src\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python prostředí nebylo nalezeno. Nejdříve spusťte install.ps1."
}
if (-not (Test-Port 5173)) {
    Start-Process -FilePath $pythonPath -ArgumentList "-m", "http.server", "5173", "--bind", "127.0.0.1", "--directory", "$root\hud" -WorkingDirectory "$root\hud" -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

# Senzory CPU/GPU/disků: program, konfigurace, log i API zůstávají na A:.
$hardwarePath = Get-ChildItem -Path "$root\runtime\librehardwaremonitor" -Filter "LibreHardwareMonitor.exe" -File -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
$hardwareProcess = Get-Process -Name "LibreHardwareMonitor" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($hardwarePath -and -not $hardwareProcess) {
    Start-Process -FilePath $hardwarePath -WorkingDirectory "$root\runtime\librehardwaremonitor" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
$telemetryProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*$root*" -and
        $_.CommandLine -like "*hardware_monitor.py*"
    } |
    Select-Object -First 1
if (-not $telemetryProcess) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList "$root\hardware_monitor.py" -WorkingDirectory $root -WindowStyle Hidden
}

# Trvalá pravidla jsou obsloužena lokálním API na loopbacku.
if (-not (Test-Port 8123)) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList "$root\jarvis_control.py" -WorkingDirectory $root -WindowStyle Hidden
}

# Samostatný lokální klient reaguje na „Hey Jarvis“ i když HUD právě čte odpověď.
$voiceProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*$root*" -and
        $_.CommandLine -like "*jarvis_voice.py*"
    } |
    Select-Object -First 1
if (-not $voiceProcess) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList "$root\jarvis_voice.py" -WorkingDirectory $root -WindowStyle Hidden
}

$networkProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "network_monitor.py" } |
    Select-Object -First 1
if (-not $networkProcess) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList "$root\network_monitor.py" -WorkingDirectory $root -WindowStyle Hidden
}

# HUD lze otevřít bez panelu prohlížeče nebo jako běžné okno.
$edgePaths = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
)
$edgePath = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edgePath) {
    throw "Nebyl nalezen podporovaný prohlížeč pro samostatné HUD okno."
}

$hudUrl = "http://127.0.0.1:5173/?hud_version=20"
$borderlessWindow = $true
$settingsPath = Join-Path $root "runtime\jarvis-settings.json"
try {
    if (Test-Path -LiteralPath $settingsPath) {
        $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
        if ($settings.PSObject.Properties.Name -contains "borderless_window") {
            $borderlessWindow = [bool]$settings.borderless_window
        }
    }
} catch {
    Write-Warning "Nastavení vzhledu okna nelze načíst; používám okno bez rámečku."
}

$browserArguments = @(
    "--new-window",
    "--user-data-dir=$root\runtime\hud-profile",
    "--window-size=1440,920"
)
if ($borderlessWindow) {
    $browserArguments = @("--app=$hudUrl") + $browserArguments
} else {
    $browserArguments = @($hudUrl) + $browserArguments
}

Start-Process -FilePath $edgePath -ArgumentList $browserArguments -WorkingDirectory "$root\hud"
