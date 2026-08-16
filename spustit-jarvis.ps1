# Spouští všechny lokální služby Jarvise z disku A: a otevře HUD rozhraní.
$ErrorActionPreference = "Stop"
$root = "A:\projekty\OpenJarvis"
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

if (-not (Test-Port 11434)) {
    Start-Process -FilePath "$root\runtime\ollama\ollama.exe" -ArgumentList "serve" -WorkingDirectory "$root\runtime\ollama" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
if (-not (Test-Port 8000)) {
    Start-Process -FilePath "$root\src\.venv\Scripts\jarvis.exe" -ArgumentList "serve", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory "$root\src" -WindowStyle Hidden
    Start-Sleep -Seconds 4
}
if (-not (Test-Port 5173)) {
    Start-Process -FilePath "$root\runtime\python\cpython-3.13-windows-x86_64-none\python.exe" -ArgumentList "-m", "http.server", "5173", "--bind", "127.0.0.1", "--directory", "$root\hud" -WorkingDirectory "$root\hud" -WindowStyle Hidden
    Start-Sleep -Seconds 1
}

# Senzory CPU/GPU/disků: program, konfigurace, log i API zůstávají na A:.
$hardwarePath = "$root\runtime\librehardwaremonitor\LibreHardwareMonitor.exe"
$hardwareProcess = Get-Process -Name "LibreHardwareMonitor" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $hardwareProcess) {
    Start-Process -FilePath $hardwarePath -WorkingDirectory "$root\runtime\librehardwaremonitor" -WindowStyle Hidden
    Start-Sleep -Seconds 2
}
$telemetryProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -match "A:\\projekty\\OpenJarvis\\src\\.venv\\Scripts\\python.exe" -and
        $_.CommandLine -match "A:\\projekty\\OpenJarvis\\hardware_monitor.py"
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
        $_.CommandLine -match "A:\\projekty\\OpenJarvis\\src\\.venv\\Scripts\\python.exe" -and
        $_.CommandLine -match "A:\\projekty\\OpenJarvis\\jarvis_voice.py"
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

# HUD se otevírá jako samostatná desktopová aplikace bez karet a adresního řádku.
$edgePaths = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
)
$edgePath = $edgePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edgePath) {
    throw "Nebyl nalezen podporovaný prohlížeč pro samostatné HUD okno."
}

Start-Process -FilePath $edgePath -ArgumentList @(
    "--app=http://127.0.0.1:5173/?hud_version=18",
    "--new-window",
    "--user-data-dir=$root\runtime\hud-profile",
    "--window-size=1440,920"
) -WorkingDirectory "$root\hud"
