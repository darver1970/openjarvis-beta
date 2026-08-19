# Spouští všechny lokální služby Jarvise z instalační složky a otevře HUD rozhraní.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$launcherMutex = [Threading.Mutex]::new($false, 'Local\JarvisLauncherV1')
if (-not $launcherMutex.WaitOne(30000)) { throw 'Jiný start Jarvise stále probíhá.' }
$env:PATH = "$root\runtime\node;$env:PATH"
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
$foreignOllamaApps = Get-Process -Name "ollama app" -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and -not $_.Path.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase) }
$foreignOllamaApps | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
$ollamaListener = Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($ollamaListener) {
    $ollamaProcess = Get-Process -Id $ollamaListener.OwningProcess -ErrorAction SilentlyContinue
    if ($ollamaProcess -and $ollamaProcess.Path -ne $ollamaPath) {
        Stop-Process -Id $ollamaProcess.Id -Force
        Start-Sleep -Seconds 1
    }
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
if (-not (Test-Port 5174)) {
    Start-Process -FilePath $pythonPath -ArgumentList "-m", "http.server", "5174", "--bind", "127.0.0.1", "--directory", "$root\hud" -WorkingDirectory "$root\hud" -WindowStyle Hidden
    Start-Sleep -Seconds 1
}
# Senzory CPU/GPU/disků: program, konfigurace, log i API zůstávají v instalační složce.
$hardwarePath = Get-ChildItem -Path "$root\runtime\librehardwaremonitor" -Filter "LibreHardwareMonitor.exe" -File -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
$hardwareProcess = Get-Process -Name "LibreHardwareMonitor" -ErrorAction SilentlyContinue |
    Select-Object -First 1
$windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = [Security.Principal.WindowsPrincipal]::new($windowsIdentity)
$isAdministrator = $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($hardwarePath -and -not $hardwareProcess -and $isAdministrator) {
    try {
        Start-Process -FilePath $hardwarePath -WorkingDirectory "$root\runtime\librehardwaremonitor" -WindowStyle Hidden
        Start-Sleep -Seconds 2
    } catch {
        Write-Warning "LibreHardwareMonitor nebyl spuštěn: $($_.Exception.Message)"
    }
} elseif ($hardwarePath -and -not $hardwareProcess) {
    Write-Warning "LibreHardwareMonitor vyžaduje spuštění launcheru jako správce; pokračuji bez něj."
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

# Trvalá pravidla a stav poskytovatele obsluhuje lokální API na loopbacku.
# Při novějším zdroji se restartuje pouze tato vlastní služba, aby HUD nikdy
# nezůstal připojený ke staré kopii backendu.
$controlProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*$root*" -and
        $_.CommandLine -like "*jarvis_control.py*"
    }
# Služba je lehká a restart při hlavním spuštění zaručí aktuální zdroj i po
# instalaci aktualizace, bez závislosti na nespolehlivém čase procesu z WMI.
if (-not (Test-Port 8126) -or $controlProcesses) {
    $controlProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList "$root\jarvis_control.py" -WorkingDirectory $root -WindowStyle Hidden
}

$networkProcess = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "network_monitor.py" } |
    Select-Object -First 1
if (-not $networkProcess) {
    Start-Process -FilePath "$root\src\.venv\Scripts\python.exe" -ArgumentList "$root\network_monitor.py" -WorkingDirectory $root -WindowStyle Hidden
}

# Nový Electron shell obsahuje skutečný prohlížeč WebContentsView, Monaco a pracovní karty.
$desktopApp = "$root\desktop\Jarvis-Desktop.exe"
if (Test-Path -LiteralPath $desktopApp) {
    Start-Process -FilePath $desktopApp -WorkingDirectory "$root\desktop"
} else {
    $electron = "$root\desktop-electron\node_modules\electron\dist\electron.exe"
    if (-not (Test-Path -LiteralPath $electron)) {
        throw "Desktopová vrstva Jarvise nebyla nalezena. Spusťte install.ps1."
    }
    Start-Process -FilePath $electron -ArgumentList "$root\desktop-electron" -WorkingDirectory "$root\desktop-electron"
}
$launcherMutex.ReleaseMutex()
$launcherMutex.Dispose()
