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
        '.gitignore', 'LICENSE', 'NOTICE', 'README.md', 'VERSION', 'install.ps1', 'spustit-jarvis.ps1',
        'hardware_monitor.py', 'jarvis_control.py', 'jarvis_voice.py',
        'gemini_live.py', 'network_monitor.py'
    )
    foreach ($name in $fileNames) {
        Copy-Item -LiteralPath (Join-Path $From $name) -Destination (Join-Path $To $name) -Force
    }
    foreach ($directory in @('defaults', 'hud', 'desktop')) {
        $destination = Join-Path $To $directory
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $From $directory) -File | ForEach-Object {
            if ($_.Extension -eq '.exe') { return }
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
    }
}

if (-not $InstallPath) {
    $defaultDrive = Join-Path $env:LOCALAPPDATA 'OpenJarvis'
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
if ($drive.AvailableFreeSpace -lt 24GB) {
    throw "Na disku $($drive.Name) je méně než 24 GB volného místa. Zvolte jiný disk."
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
$settings | Add-Member -NotePropertyName ai_provider -NotePropertyValue 'local' -Force
$settings | Add-Member -NotePropertyName cloud_api -NotePropertyValue $false -Force
$settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settingsPath -Encoding utf8

if (-not $SkipVoice) {
    Write-Step 'Instaluji bezplatné závislosti hlasového klienta.'
    & $python -m pip install --disable-pip-version-check openwakeword sounddevice soundfile piper-tts websockets
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Hlasové závislosti se nepodařilo nainstalovat. HUD zůstává funkční.' }
    $piperDirectory = Join-Path $runtime 'piper'
    $piperModel = Join-Path $piperDirectory 'cs_CZ-jirka-medium.onnx'
    $piperMetadata = "$piperModel.json"
    New-Item -ItemType Directory -Path $piperDirectory -Force | Out-Null
    if (-not (Test-Path -LiteralPath $piperModel)) {
        Write-Step 'Stahuji český lokální hlas Piper Jirka.'
        $piperBase = 'https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/cs/cs_CZ/jirka/medium/cs_CZ-jirka-medium.onnx'
        Invoke-WebRequest -Uri "$piperBase?download=true" -OutFile $piperModel -UseBasicParsing
        Invoke-WebRequest -Uri "$piperBase.json?download=true" -OutFile $piperMetadata -UseBasicParsing
    }
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
        foreach ($model in @('qwen3.5:4b', 'qwen2.5-coder:7b')) {
            Write-Step "Stahuji lokální bezplatný model $model."
            & $ollama.Source pull $model
            if ($LASTEXITCODE -ne 0) { Write-Warning "Model $model se nestáhl. Později spusťte: ollama pull $model" }
        }
    } else {
        Write-Warning 'Ollama nebyla po instalaci nalezena. Spusťte instalátor znovu v novém PowerShellu.'
    }
}

Write-Step 'Připravuji lokálního agenta OpenClaw bez Gateway a bez oprávnění k příkazům.'
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $node) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) { throw 'Pro OpenClaw chybí Node.js 22+ i winget. Nainstalujte Node.js LTS a spusťte instalátor znovu.' }
    & $winget.Source install --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Instalace Node.js LTS selhala.' }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $node) { throw 'Node.js je nainstalovaný, ale tento PowerShell ho ještě nevidí. Otevřete nový PowerShell a spusťte instalátor znovu.' }
}
$nodeVersion = [Version]((& $node.Source --version) -replace '^v', '')
if ($nodeVersion.Major -lt 22) { throw "OpenClaw vyžaduje Node.js 22+, nalezena verze $nodeVersion." }
$npm = Assert-Command 'npm.cmd'
$openClawRoot = Join-Path $runtime 'openclaw'
$env:npm_config_cache = Join-Path $openClawRoot 'npm-cache'
New-Item -ItemType Directory -Path $openClawRoot, $env:npm_config_cache -Force | Out-Null
& $npm install --prefix $openClawRoot --omit=dev 'openclaw@2026.7.1-2'
if ($LASTEXITCODE -ne 0) { throw 'Instalace OpenClaw selhala.' }
$openClawConfig = @{
    models = @{ providers = @{ ollama = @{
        baseUrl = 'http://127.0.0.1:11434'; apiKey = 'ollama-local'; api = 'ollama'; timeoutSeconds = 300
        models = @(@{ id = 'qwen2.5-coder:7b'; name = 'Qwen 2.5 Coder 7B'; input = @('text'); params = @{ keep_alive = '15m' } })
    } } }
    agents = @{ defaults = @{
        model = @{ primary = 'ollama/qwen2.5-coder:7b' }
        models = @{ 'ollama/qwen2.5-coder:7b' = @{} }
        workspace = (Join-Path $runtime 'agents\openclaw')
    } }
    gateway = @{ mode = 'local' }
    tools = @{ exec = @{ host = 'gateway'; security = 'deny'; ask = 'off' } }
}
$openClawConfigPath = Join-Path $openClawRoot 'openclaw.json'
$openClawConfig | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $openClawConfigPath -Encoding utf8
$env:OPENCLAW_STATE_DIR = Join-Path $openClawRoot 'state'
$env:OPENCLAW_CONFIG_PATH = $openClawConfigPath
$env:OPENCLAW_WORKSPACE_DIR = Join-Path $runtime 'agents\openclaw'
New-Item -ItemType Directory -Path $env:OPENCLAW_STATE_DIR, $env:OPENCLAW_WORKSPACE_DIR -Force | Out-Null
$openClawCommand = Join-Path $openClawRoot 'node_modules\.bin\openclaw.cmd'
& $openClawCommand exec-policy preset deny-all
if ($LASTEXITCODE -ne 0) { throw 'Nelze nastavit bezpečnostní politiku OpenClaw.' }
$agentsPath = Join-Path $runtime 'jarvis-agents.json'
$agents = Get-Content -LiteralPath $agentsPath -Raw | ConvertFrom-Json
$openClawAgent = $agents.agents | Where-Object { $_.id -eq 'openclaw' } | Select-Object -First 1
if ($openClawAgent) {
    $openClawAgent.model = 'qwen2.5-coder:7b'
    $openClawAgent.status = if ($SkipModel) { 'planned' } else { 'ready' }
    $openClawAgent.rules = @('Používá jen lokální Ollama a nemá spuštěnou Gateway.', 'Pracuje pouze v izolovaném pracovním adresáři.', 'Systémové změny, síť a externí účty zůstávají zablokované.')
    $openClawAgent.permissions = if ($SkipModel) { @('Čeká na stažení qwen2.5-coder:7b') } else { @('Lokální textová inference', 'Bez příkazů, sítě a externích účtů') }
    $agents | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $agentsPath -Encoding utf8
}
$openClawModulePath = Join-Path $runtime 'openclaw-module.json'
$openClawModule = Get-Content -LiteralPath $openClawModulePath -Raw | ConvertFrom-Json
$openClawModule.state = 'installed_local'
$openClawModule.required_checks = @('OpenClaw CLI ověřen', 'Lokální Ollama ověřena', 'Politika příkazů deny-all')
$openClawModule.blocked_actions = @('Automatické spouštění po Windows', 'Automatický přístup k účtům', 'Automatické systémové změny')
$openClawModule | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $openClawModulePath -Encoding utf8

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
