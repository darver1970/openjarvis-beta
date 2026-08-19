<#
.SYNOPSIS
    Instaluje Jarvis 1.0 do jediné projektové složky.

.DESCRIPTION
    Stahuje pouze bezplatné závislosti z oficiálních zdrojů, založí lokální
    OpenJarvis backend, vytvoří konfiguraci a zástupce na ploše. Neodesílá
    obsah instalace ani uživatelská data mimo počítač.
#>
[CmdletBinding()]
param(
    [string]$InstallPath,
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

function Copy-JarvisFiles([string]$From, [string]$To) {
    $fileNames = @(
        '.gitignore', 'LICENSE', 'NOTICE', 'README.md', 'VERSION', 'install.ps1', 'spustit-jarvis.ps1',
        'hardware_monitor.py', 'telemetry_extensions.py', 'jarvis_control.py', 'network_monitor.py',
        'agent_runtime.py', 'jarvis_intelligence.py'
    )
    foreach ($name in $fileNames) {
        Copy-Item -LiteralPath (Join-Path $From $name) -Destination (Join-Path $To $name) -Force
    }
    foreach ($directory in @('defaults', 'hud', 'desktop', 'desktop-electron')) {
        $sourceDirectory = Join-Path $From $directory
        $destination = Join-Path $To $directory
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Get-ChildItem -LiteralPath $sourceDirectory -File -Recurse | ForEach-Object {
            $relative = $_.FullName.Substring($sourceDirectory.Length).TrimStart('\')
            if ($_.Extension -eq '.exe' -or $relative -match '(^|\\)node_modules(\\|$)') { return }
            $target = Join-Path $destination $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:PATH = "$machine;$user"
}

function Ensure-Node {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if (-not $command) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) { throw 'Chybí Node.js 22+ a winget není dostupný.' }
        Write-Step 'Instaluji bezplatný Node.js LTS.'
        & $winget.Source install --id OpenJS.NodeJS.LTS --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) { throw 'Instalace Node.js LTS selhala.' }
        Refresh-ProcessPath
        $command = Get-Command node.exe -ErrorAction SilentlyContinue
        if (-not $command -and (Test-Path -LiteralPath "$env:ProgramFiles\nodejs\node.exe")) {
            $env:PATH = "$env:ProgramFiles\nodejs;$env:PATH"
            $command = Get-Command node.exe -ErrorAction SilentlyContinue
        }
    }
    if (-not $command) { throw 'Node.js se po instalaci nepodařilo najít.' }
    $version = [Version]((& $command.Source --version) -replace '^v', '')
    if ($version.Major -lt 22) { throw "Jarvis vyžaduje Node.js 22+, nalezena verze $version." }
    return $command
}

if (-not $InstallPath) {
    $defaultDrive = 'C:\projektjarvis'
    Write-Host ''
    Write-Host 'Zvolte jednu pracovní složku pro celý Jarvis, modely, runtime a data.' -ForegroundColor Yellow
    $InstallPath = Read-Host "Cílová složka Jarvisu [výchozí: $defaultDrive]"
    if (-not $InstallPath) { $InstallPath = $defaultDrive }
}

$installRoot = [System.IO.Path]::GetFullPath($InstallPath)
$sourceRoot = [System.IO.Path]::GetFullPath($sourceRoot)
$installMarker = Join-Path $installRoot '.jarvis-installing'
if ($installRoot.Length -lt 4 -or $installRoot -eq $installRoot.Substring(0, 3)) {
    throw 'Jako cíl nelze použít kořen disku. Zvolte samostatnou složku.'
}

$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($installRoot))
if (-not $drive.IsReady) { throw "Disk $($drive.Name) není připraven." }
if ($drive.AvailableFreeSpace -lt 24GB) {
    throw "Na disku $($drive.Name) je méně než 24 GB volného místa. Zvolte jiný disk."
}

if ($installRoot -ne $sourceRoot) {
    $resuming = Test-Path -LiteralPath $installMarker
    if (Test-Path -LiteralPath $installRoot) {
        $existing = Get-ChildItem -LiteralPath $installRoot -Force | Where-Object Name -ne '.jarvis-installing' | Select-Object -First 1
        if ($existing -and -not $resuming) { throw "Cílová složka není prázdná: $installRoot" }
    }
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    Set-Content -LiteralPath $installMarker -Value 'Jarvis 1.0 installation in progress' -Encoding utf8
    Write-Step "Kopíruji soubory Jarvis 1.0 do $installRoot"
    Copy-JarvisFiles -From $sourceRoot -To $installRoot
} else {
    Set-Content -LiteralPath $installMarker -Value 'Jarvis 1.0 installation in progress' -Encoding utf8
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
    Refresh-ProcessPath
    $git = Assert-Command 'git'
}

if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'src\.venv\Scripts\python.exe'))) {
    Write-Step 'Spouštím oficiální instalaci OpenJarvisu do zvolené složky.'
    $upstreamInstaller = Join-Path $runtime 'openjarvis-install.ps1'
    Invoke-WebRequest -Uri 'https://open-jarvis.github.io/OpenJarvis/install.ps1' -OutFile $upstreamInstaller -UseBasicParsing
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $upstreamInstaller -SkipService
    if ($LASTEXITCODE -ne 0) { throw "Instalace OpenJarvisu selhala s kódem $LASTEXITCODE." }
} else {
    Write-Step 'Používám existující lokální prostředí OpenJarvisu.'
}

$python = Join-Path $installRoot 'src\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'OpenJarvis nevytvořil očekávané Python prostředí.' }

Write-Step 'Odstraňuji nepoužívané hlasové balíčky a modely.'
& $python -m pip uninstall -y openwakeword piper-tts sounddevice soundfile 2>$null
foreach ($voiceCache in @(
    (Join-Path $runtime 'piper'),
    (Join-Path $runtime 'voice'),
    (Join-Path $runtime 'huggingface\hub\models--hexgrad--Kokoro-82M'),
    (Join-Path $runtime 'huggingface\hub\models--Systran--faster-whisper-base'),
    (Join-Path $runtime 'huggingface\hub\models--Systran--faster-whisper-small')
)) {
    if ($voiceCache.StartsWith($installRoot, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $voiceCache)) {
        Remove-Item -LiteralPath $voiceCache -Recurse -Force
    }
}

Write-Step 'Instaluji bezplatnou lokální telemetrii procesů.'
& $python -m pip install --disable-pip-version-check psutil
if ($LASTEXITCODE -ne 0) { throw 'Nelze nainstalovat telemetrii procesů psutil.' }

Write-Step 'Instaluji bezplatné agentní jádro, webového agenta a lokální crawler.'
& $python -m pip install --disable-pip-version-check 'pydantic-ai-slim==2.32.0' 'browser-use==0.13.8' 'crawl4ai==0.9.2' 'mcp==1.26.0' 'starlette==0.52.1' 'pytest==9.1.1'
if ($LASTEXITCODE -ne 0) { throw 'Instalace agentních komponent selhala.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Python závislosti agentního jádra nejsou kompatibilní.' }

Write-Step 'Vytvářím lokální konfiguraci Jarvis 1.0.'
foreach ($template in Get-ChildItem -LiteralPath (Join-Path $installRoot 'defaults') -Filter '*.json') {
    $destination = Join-Path $runtime $template.Name
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $template.FullName -Destination $destination
    }
}
$settingsPath = Join-Path $runtime 'jarvis-settings.json'
$settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$settings | Add-Member -NotePropertyName storage_root -NotePropertyValue $installRoot -Force
$settings | Add-Member -NotePropertyName ai_provider -NotePropertyValue 'automatic' -Force
$settings | Add-Member -NotePropertyName router_mode -NotePropertyValue 'automatic' -Force
$settings | Add-Member -NotePropertyName permission_mode -NotePropertyValue 'full' -Force
$settings | Add-Member -NotePropertyName cloud_api -NotePropertyValue $true -Force
$settings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $settingsPath -Encoding utf8

Write-Step 'Instaluji bezplatnou desktopovou vrstvu Electron, skutečný web a Monaco editor.'
$node = Ensure-Node
$npm = Assert-Command 'npm.cmd'
$desktopPath = Join-Path $installRoot 'desktop'
$electronProject = Join-Path $installRoot 'desktop-electron'
if (-not (Test-Path -LiteralPath (Join-Path $electronProject 'package.json'))) {
    throw 'Zdroj desktopové vrstvy Jarvise chybí.'
}
& $npm install --prefix $electronProject --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Instalace bezplatných desktopových závislostí selhala.' }
& $npm run --prefix $electronProject pack:portable
if ($LASTEXITCODE -ne 0) { throw 'Vytvoření desktopového EXE selhalo.' }
$builtDesktop = Join-Path $installRoot 'desktop-dist\Jarvis-Desktop.exe'
if (-not (Test-Path -LiteralPath $builtDesktop)) { throw 'Sestavený Jarvis-Desktop.exe nebyl nalezen.' }
Copy-Item -LiteralPath $builtDesktop -Destination (Join-Path $desktopPath 'Jarvis-Desktop.exe') -Force

if (-not $SkipModel) {
    $ollamaPath = Join-Path $runtime 'ollama\ollama.exe'
    if (-not (Test-Path -LiteralPath $ollamaPath)) {
        $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
        if ($ollama) { $ollamaPath = $ollama.Source }
    }
    if (-not (Test-Path -LiteralPath $ollamaPath)) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Step 'Instaluji bezplatnou Ollamu.'
            & $winget.Source install --id Ollama.Ollama --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
            Refresh-ProcessPath
            $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
            if ($ollama) { $ollamaPath = $ollama.Source }
        }
    }
    if (Test-Path -LiteralPath $ollamaPath) {
        foreach ($model in @('qwen3.5:4b', 'qwen2.5-coder:7b')) {
            Write-Step "Stahuji lokální bezplatný model $model."
            & $ollamaPath pull $model
            if ($LASTEXITCODE -ne 0) { Write-Warning "Model $model se nestáhl. Později spusťte: ollama pull $model" }
        }
    } else {
        Write-Warning 'Ollama nebyla po instalaci nalezena. Online bezplatný režim zůstává dostupný.'
    }
}

Write-Step 'Připravuji lokálního agenta OpenClaw bez Gateway a bez oprávnění k příkazům.'
$node = Ensure-Node
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

try {
    $winget = Get-Command winget -ErrorAction Stop
    $presentMonDirectory = Join-Path $runtime 'presentmon'
    Write-Step 'Instaluji bezplatný PresentMon pro FPS a frametime.'
    & $winget.Source install --id Intel.PresentMon.Console --exact --location $presentMonDirectory --silent --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "PresentMon skončil s kódem $LASTEXITCODE." }
} catch {
    Write-Warning 'PresentMon se nepodařilo nainstalovat; ostatní telemetrie zůstává funkční.'
}

try {
    $handleDirectory = Join-Path $runtime 'sysinternals-handle'
    $handleArchive = Join-Path $handleDirectory 'Handle.zip'
    New-Item -ItemType Directory -Path $handleDirectory -Force | Out-Null
    Write-Step 'Stahuji podepsaný Microsoft Sysinternals Handle.'
    Invoke-WebRequest -Uri 'https://download.sysinternals.com/files/Handle.zip' -OutFile $handleArchive -UseBasicParsing
    Expand-Archive -LiteralPath $handleArchive -DestinationPath $handleDirectory -Force
    $handleBinary = Join-Path $handleDirectory 'handle64.exe'
    $handleSignature = Get-AuthenticodeSignature -LiteralPath $handleBinary
    if ($handleSignature.Status -ne 'Valid' -or $handleSignature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') {
        throw 'Digitální podpis Microsoft Handle není platný.'
    }
    Remove-Item -LiteralPath $handleArchive -Force
} catch {
    Write-Warning 'Microsoft Handle se nepodařilo ověřit; registry handly zůstanou nedostupné.'
}

$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Jarvis 1.0.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$installRoot\spustit-jarvis.ps1`""
$shortcut.WorkingDirectory = $installRoot
$shortcut.Description = 'Spustit lokální Jarvis 1.0'
$shortcut.IconLocation = "$(Join-Path $desktopPath 'Jarvis-Desktop.exe'),0"
$shortcut.Save()

$installConfigDirectory = Join-Path $env:LOCALAPPDATA 'Jarvis'
New-Item -ItemType Directory -Path $installConfigDirectory -Force | Out-Null
Set-Content -LiteralPath (Join-Path $installConfigDirectory 'install-path.txt') -Value $installRoot -Encoding utf8
Remove-Item -LiteralPath $installMarker -Force -ErrorAction SilentlyContinue
Write-Step 'Instalace dokončena. Spusťte zástupce Jarvis 1.0 na ploše.'
