<#
.SYNOPSIS
    Instaluje veřejnou beta verzi JARVISu do složky zvolené uživatelem.

.DESCRIPTION
    Stahuje pouze bezplatné závislosti z oficiálních zdrojů, založí lokální
    OpenJarvis backend, vytvoří konfiguraci a zástupce na ploše. Neodesílá
    obsah instalace ani uživatelská data mimo počítač.
#>
[CmdletBinding()]
param(
    [string]$InstallPath,
    [switch]$SkipVoice,
    [switch]$SkipModel
)

$ErrorActionPreference = 'Stop'
$sourceRoot = $PSScriptRoot

function Write-Step([string]$Message) {
    Write-Host "[JARVIS] $Message" -ForegroundColor Cyan
}

function Assert-Command([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Chybí program '$Name'. Nainstalujte jej a spusťte instalátor znovu."
    }
    return $command.Source
}

function Copy-BetaFiles([string]$From, [string]$To) {
    $fileNames = @(
        '.gitignore', 'README.md', 'install.ps1', 'spustit-jarvis.ps1',
        'hardware_monitor.py', 'jarvis_control.py', 'jarvis_voice.py',
        'network_monitor.py'
    )
    foreach ($name in $fileNames) {
        Copy-Item -LiteralPath (Join-Path $From $name) -Destination (Join-Path $To $name) -Force
    }
    foreach ($directory in @('defaults', 'hud')) {
        $destination = Join-Path $To $directory
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $From $directory) -File | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }
}

if (-not $InstallPath) {
    $defaultDrive = if (Test-Path 'A:\') { 'A:\projekty\OpenJarvis' } else { 'D:\OpenJarvis' }
    $InstallPath = Read-Host "Cílová složka instalace [výchozí: $defaultDrive]"
    if (-not $InstallPath) { $InstallPath = $defaultDrive }
}

$installRoot = [System.IO.Path]::GetFullPath($InstallPath)
$sourceRoot = [System.IO.Path]::GetFullPath($sourceRoot)
if ($installRoot.Length -lt 4 -or $installRoot -eq $installRoot.Substring(0, 3)) {
    throw 'Jako cíl nelze použít kořen disku. Zvolte samostatnou složku.'
}

$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installRoot))
if (-not $drive.IsReady) { throw "Disk $($drive.Name) není připraven." }
if ($drive.AvailableFreeSpace -lt 12GB) {
    throw "Na disku $($drive.Name) je méně než 12 GB volného místa. Zvolte jiný disk."
}

if ($installRoot -ne $sourceRoot) {
    if (Test-Path -LiteralPath $installRoot) {
        $existing = Get-ChildItem -LiteralPath $installRoot -Force | Select-Object -First 1
        if ($existing) { throw "Cílová složka není prázdná: $installRoot" }
    }
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    Write-Step "Kopíruji beta soubory do $installRoot"
    Copy-BetaFiles -From $sourceRoot -To $installRoot
}

$runtime = Join-Path $installRoot 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$env:OPENJARVIS_HOME = $installRoot
$env:OPENJARVIS_SKIP_SERVICE = '1'
$env:OLLAMA_MODELS = Join-Path $runtime 'ollama-models'
$env:UV_CACHE_DIR = Join-Path $runtime 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
$env:TEMP = Join-Path $runtime 'temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP, $env:OLLAMA_MODELS, $env:UV_CACHE_DIR -Force | Out-Null

Write-Step 'Kontroluji Git.'
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'Chybí Git i winget. Nainstalujte Git pro Windows a spusťte instalátor znovu.' }
    Write-Step 'Instaluji Git pro Windows.'
    & $winget.Source install --id Git.Git --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Instalace Gitu selhala.' }
    $git = Assert-Command 'git'
}

Write-Step 'Spouštím oficiální instalaci OpenJarvisu do zvolené složky.'
$upstreamInstaller = Join-Path $runtime 'openjarvis-install.ps1'
Invoke-WebRequest -Uri 'https://open-jarvis.github.io/OpenJarvis/install.ps1' -OutFile $upstreamInstaller -UseBasicParsing
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $upstreamInstaller -SkipService
if ($LASTEXITCODE -ne 0) { throw "Instalace OpenJarvisu selhala s kódem $LASTEXITCODE." }

$python = Join-Path $installRoot 'src\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'OpenJarvis nevytvořil očekávané Python prostředí.' }

Write-Step 'Vytvářím lokální konfiguraci beta HUDu.'
foreach ($template in Get-ChildItem -LiteralPath (Join-Path $installRoot 'defaults') -Filter '*.json') {
    $destination = Join-Path $runtime $template.Name
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $template.FullName -Destination $destination
    }
}
$settingsPath = Join-Path $runtime 'jarvis-settings.json'
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$settings | Add-Member -NotePropertyName storage_root -NotePropertyValue $installRoot -Force
$settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settingsPath -Encoding utf8

if (-not $SkipVoice) {
    Write-Step 'Instaluji bezplatné závislosti hlasového klienta.'
    & $python -m pip install --disable-pip-version-check openwakeword sounddevice soundfile
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Hlasové závislosti se nepodařilo nainstalovat. HUD zůstává funkční.' }
}

Write-Step 'Vytvářím vlastní nativní okno JARVIS HUD.'
$desktopPath = Join-Path $installRoot 'desktop'
$hudSource = Join-Path $desktopPath 'jarvis_hud.py'
$hudIcon = Join-Path $desktopPath 'jarvis.ico'
if (-not (Test-Path -LiteralPath $hudSource) -or -not (Test-Path -LiteralPath $hudIcon)) {
    throw 'Zdroj nebo ikona vlastního JARVIS HUDu chybí.'
}
& $python -m pip install --disable-pip-version-check pywebview pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Nelze nainstalovat závislosti vlastního JARVIS HUDu.' }
& $python -m PyInstaller --noconfirm --clean --onefile --noconsole --name 'Jarvis-HUD' --icon $hudIcon --distpath $desktopPath --workpath (Join-Path $runtime 'pyinstaller-build') --specpath (Join-Path $runtime 'pyinstaller-spec') $hudSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $desktopPath 'Jarvis-HUD.exe'))) {
    throw 'Vytvoření vlastního JARVIS HUDu selhalo.'
}

if (-not $SkipModel) {
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollama) {
        Write-Step 'Stahuji lokální bezplatný model qwen3.5:2b.'
        & $ollama.Source pull 'qwen3.5:2b'
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Model se nestáhl. Později spusťte: ollama pull qwen3.5:2b' }
    } else {
        Write-Warning 'Ollama nebyla po instalaci nalezena. Spusťte instalátor znovu v novém PowerShellu.'
    }
}

Write-Step 'Připravuji volitelnou lokální telemetrii.'
try {
    $lhmZip = Join-Path $runtime 'LibreHardwareMonitor.zip'
    Invoke-WebRequest -Uri 'https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest/download/LibreHardwareMonitor-net472.zip' -OutFile $lhmZip -UseBasicParsing
    Expand-Archive -LiteralPath $lhmZip -DestinationPath (Join-Path $runtime 'librehardwaremonitor') -Force
    Remove-Item -LiteralPath $lhmZip -Force
} catch {
    Write-Warning 'LibreHardwareMonitor se nepodařilo stáhnout; ostatní části zůstávají funkční.'
}

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'JARVIS Beta.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$installRoot\spustit-jarvis.ps1`""
$shortcut.WorkingDirectory = $installRoot
$shortcut.Description = 'Spustit lokální JARVIS Beta HUD'
$shortcut.IconLocation = "$(Join-Path $desktopPath 'Jarvis-HUD.exe'),0"
$shortcut.Save()

Write-Step 'Instalace dokončena. Spusťte zástupce JARVIS Beta na ploše.'
