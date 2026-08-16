# OpenJarvis Beta HUD

Lokální beta rozhraní JARVIS pro Windows. Běží ze složky zvolené při instalaci, používá lokální Ollama modely a nevyžaduje placené API tokeny.

Základní serverový projekt je [OpenJarvis](https://github.com/open-jarvis/OpenJarvis). Tento repozitář obsahuje beta nadstavbu HUDu, lokálního hlasu, telemetrie, pravidel a spouštění ve Windows.

## Součásti

- animovaný desktopový HUD s telemetry CPU, RAM, GPU a disky,
- záložka Procesy s lokálním přehledem a bezpečným ukončením vybraného procesu,
- lokální hlasový klient a wake word,
- trvalá pravidla, projekty, nastavení připojení a síťový monitoring,
- lokální OpenJarvis backend.

## Spuštění

V PowerShellu spusťte:

```powershell
& '<zvolená-složka>\spustit-jarvis.ps1'
```

## Čistá instalace

Po stažení repozitáře spusťte `install.ps1`. Skript se zeptá na cílovou složku, ověří alespoň 12 GB volného místa a nainstaluje bezplatné závislosti do zvoleného umístění. Vyžaduje Windows, internet a Git; model `qwen3.5:2b` lze přeskočit parametrem `-SkipModel`.

## Veřejný obsah

Tento repozitář záměrně neobsahuje lokální modely, databáze, cache, hlasové nahrávky, logy ani runtime konfiguraci. Tyto soubory se vytvářejí pouze na zařízení uživatele.

## Stav

Beta, HUD verze 18.
