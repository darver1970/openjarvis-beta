# OpenJarvis Beta HUD

Lokální beta rozhraní JARVIS pro Windows. Běží ze složky zvolené při instalaci, používá lokální Ollama modely a nevyžaduje placené API tokeny.

Základní serverový projekt je [OpenJarvis](https://github.com/open-jarvis/OpenJarvis). Tento repozitář obsahuje beta nadstavbu HUDu, lokálního hlasu, telemetrie, pravidel a spouštění ve Windows.

## Součásti

- animovaný desktopový HUD s telemetry CPU, RAM, GPU a disky,
- záložka Procesy s lokálním přehledem a bezpečným ukončením vybraného procesu,
- lokální hlasový klient a wake word,
- trvalá pravidla, projekty, nastavení připojení a síťový monitoring,
- lokální OpenJarvis backend.

## Čistá instalace

### Nejjednodušší postup

1. Na této stránce klikněte na zelené tlačítko **Code → Download ZIP**.
2. Stažený ZIP soubor rozbalte do libovolné dočasné složky.
3. V rozbalené složce klikněte pravým tlačítkem na soubor `install.ps1`.
4. Zvolte **Spustit v PowerShellu**.
5. Do otevřeného okna zadejte cílovou instalační složku, například `D:\Aplikace\OpenJarvis`, a vyčkejte na dokončení.
6. Po instalaci spusťte zástupce **JARVIS Beta** na ploše.

Pokud Windows zobrazí bezpečnostní upozornění na soubor stažený z internetu, otevřete **Vlastnosti** souboru `install.ps1`, zaškrtněte **Odblokovat**, potvrďte tlačítkem **Použít** a znovu vyberte **Spustit v PowerShellu**.

### Podrobný postup

### Požadavky

- Windows 10 (1809) nebo novější ve 64bitové verzi,
- připojení k internetu během instalace,
- alespoň 12 GB volného místa na vybraném disku; více místa je vhodné pro další modely,
- Git pro Windows. Pokud jej v systému nemáte, instalátor se jej pokusí nainstalovat přes `winget`.

### Postup přes příkazový řádek

1. Na stránce repozitáře klikněte na **Code → Download ZIP** a archiv rozbalte, nebo repozitář naklonujte:

   ```powershell
   git clone https://github.com/darver1970/openjarvis-beta.git
   cd openjarvis-beta
   ```

2. Ve složce projektu otevřete PowerShell a spusťte instalátor:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   ```

3. Instalátor se zeptá na cílovou složku. Zadejte vlastní cestu, například `D:\Aplikace\OpenJarvis`. Do této složky uloží backend, modely, konfiguraci, cache, telemetrii a lokální data.

4. Vyčkejte na dokončení. Při prvním běhu se stahuje bezplatný lokální model `qwen3.5:2b`, proto instalace může trvat déle a vyžaduje několik GB dat.

5. Na ploše vznikne zástupce **JARVIS Beta**. Dvojklikem jej spusťte. Alternativně spusťte:

   ```powershell
   & 'D:\Aplikace\OpenJarvis\spustit-jarvis.ps1'
   ```

### Volitelné parametry

```powershell
# Instalace bez stažení modelu; stáhněte jej později příkazem ollama pull qwen3.5:2b
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipModel

# Instalace bez hlasového modulu
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipVoice

# Zadání cílové složky bez dotazu
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallPath 'D:\Aplikace\OpenJarvis'
```

### Řešení problémů

- **Málo místa na disku:** zvolte jiný disk nebo použijte `-SkipModel`.
- **Chybí Git:** nainstalujte Git pro Windows, otevřete nový PowerShell a spusťte instalátor znovu.
- **Model se nestáhl:** otevřete nový PowerShell, nastavte se do instalační složky a spusťte `ollama pull qwen3.5:2b`.
- **HUD se neotevře:** spusťte `spustit-jarvis.ps1` přímo z instalační složky a zkontrolujte, zda antivirus nebo firewall neblokuje místní porty `8000`, `5173`, `8125` a `11434`.
- **Hlas nefunguje:** povolte mikrofon pro desktopové aplikace v Nastavení Windows a spusťte instalátor znovu bez `-SkipVoice`.

## Veřejný obsah

Tento repozitář záměrně neobsahuje lokální modely, databáze, cache, hlasové nahrávky, logy ani runtime konfiguraci. Tyto soubory se vytvářejí pouze na zařízení uživatele.

## Kredit a licence

Tato beta nadstavba vychází ze serverového projektu
[OpenJarvis](https://github.com/open-jarvis/OpenJarvis). Kredit původním
autorům: Jon Saad-Falcon, Avanika Narayan, Robby Manihani, Tanvir Bhathal,
Herumb Shandilya, Hakki Orhun Akengin, Gabriel Bo, Andrew Park, Matthew Hart,
Caia Costello, Chuan Li, Christopher Re a Azalia Mirhoseini.

Projekt je vydán pod licencí Apache License 2.0. Úplné znění je v souboru
[`LICENSE`](LICENSE).

## Stav

Beta, v0.22.

## Změny

- 2026-08-16: Nastavení obsahuje přepínač pro spuštění JARVISu po přihlášení do Windows.
- 2026-08-16: Nastavení obsahuje přepínač pro otevření HUDu v okně bez rámečku prohlížeče.
- 2026-08-16: Přidána sekce Agentů v levé liště, lokální katalog a řízená instalace bezplatných rolí.
- 2026-08-16: Přidáno paralelní zadání úkolu připraveným agentům příkazem `agenti: <úkol>`.
- 2026-08-16: Vydání v0.22 přidává vlastní izolovaný Jarvis-HUD.exe, projektovou paměť pro spolupráci a postranní panely pouze v konverzaci.
