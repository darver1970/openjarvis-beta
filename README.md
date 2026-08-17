# OpenJarvis Beta HUD v0.6

Lokální Windows rozhraní JARVIS postavené nad projektem OpenJarvis. Běží z
libovolné složky zvolené při instalaci a nevyžaduje placený API klíč pro
základní lokální provoz.

## Co obsahuje

- vlastní nativní okno `Jarvis-HUD.exe` s izolovaným WebView profilem,
- lokální modely Ollama `qwen3.5:4b` a `qwen2.5-coder:7b`,
- český a anglický hlasový vstup, wake-word, STOP a lokální Piper hlas,
- volitelný Gemini Live hlas: Gemini je první volba, lokální hlas je záloha,
- lokální režimy AI: Ollama, Gemini Free, OpenRouter Free a Automaticky,
- automatický router: Gemini Free -> OpenRouter Free -> lokální Ollama,
- levý panel agentů a izolovaný lokální OpenClaw s politikou `deny-all`,
- pravou pracovní plochu pro soubory, Git stav a vlastní JARVIS WEB,
- telemetrii CPU, RAM, GPU, disků, procesů a síťové aktivity.

## Bezpečnost a soukromí

Základní režim je lokální. Modely, konfigurace, logy, historie, cache,
OpenClaw workspace a tajemství jsou uloženy pouze v instalační složce pod
`runtime` a nejsou součástí Git repozitáře.

Každá nová instalace začíná v režimu **Lokální Ollama** bez API klíčů. Každý
uživatel zadává vlastní klíč až ve svém HUDu; jeho klíč se šifruje pouze pro
jeho účet Windows. Po úspěšném ověření Gemini klíče se zpřístupní Gemini Live
hlas. Bez klíče zůstává hlas i text plně lokální.

Gemini a OpenRouter jsou volitelné. Klíče se ukládají šifrovaně pomocí Windows
DPAPI pro aktuální účet a nikdy se nezobrazují v HUDu ani v logu. Při použití
Gemini Live odchází hlasový dotaz do služby Google. OpenClaw nemá spuštěnou
Gateway, síťové účty ani oprávnění provádět příkazy.

## Čistá instalace

### Nejjednodušší postup

1. Na GitHubu klikněte na **Code -> Download ZIP** a archiv rozbalte.
2. Otevřete rozbalenou složku a najděte soubor `install.ps1`.
3. Klikněte na něj pravým tlačítkem a zvolte **Spustit v PowerShellu**.
4. Do zobrazeného okna pouze napište cílovou instalační složku a stiskněte
   `Enter`. Pro výchozí umístění stačí stisknout samotný `Enter`.
5. V bezpečnostním dotazu PowerShellu potvrďte spuštění klávesou `R` nebo
   volbou **Ano**. Není nutné ručně psát žádný příkaz.
6. Vyčkejte na dokončení instalace a stažení bezplatných závislostí a modelů.
7. Na ploše spusťte nový zástupce **JARVIS Beta**.

Výchozí cesta je `%LOCALAPPDATA%\OpenJarvis`; můžete zvolit například
`D:\Aplikace\OpenJarvis`. Instalátor nikdy nevynucuje konkrétní disk.

Pokud nabídka **Spustit v PowerShellu** není vidět, podržte `Shift`, klikněte
pravým tlačítkem na `install.ps1` a otevřete ji přes **Zobrazit další možnosti**.
Příkazový postup níže použijte jen jako náhradní řešení.

### Požadavky

- Windows 10 1809 nebo novější, 64bit,
- internet během instalace,
- nejméně 24 GB volného místa na zvoleném disku,
- mikrofon pro hlasový modul,
- Git a Node.js LTS; instalátor je v případě dostupného `winget` doplní.

### Příkazový postup

```powershell
git clone https://github.com/darver1970/openjarvis-beta.git
cd openjarvis-beta
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Volitelné parametry:

```powershell
# Bez lokálních modelů; později spusťte ollama pull qwen3.5:4b
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipModel

# Bez hlasových závislostí
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipVoice

# Bez dotazu na cílovou cestu
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InstallPath 'D:\Aplikace\OpenJarvis'
```

## První spuštění

JARVIS startuje v lokálním režimu. V nastavení lze vybrat:

- **Lokální Ollama** pro plně offline textové odpovědi,
- **Gemini Free** po jednorázovém uložení a ověření vlastního klíče,
- **OpenRouter Free** po jednorázovém uložení a ověření vlastního klíče,
- **Automaticky**, který preferuje Gemini, poté OpenRouter a nakonec lokální
  Ollama bez použití placených modelů.

Pokud je uložen funkční Gemini klíč, hlasový modul nejdříve použije Gemini
Live. Při chybě, nedostupnosti nebo vyčerpání kvóty přejde na lokální hlas a
Gemini zkusí znovu po pěti minutách.

## Řešení problémů

- **HUD se neotevře:** spusťte `spustit-jarvis.ps1` přímo z instalační složky.
- **Mikrofon nefunguje:** povolte mikrofon pro desktopové aplikace v Nastavení
  Windows a ověřte vstup v nastavení JARVISu.
- **Gemini Live selže:** JARVIS přepne na lokální hlas. Ověřte internet a
  kvótu v Google AI Studio.
- **Model chybí:** v instalační složce spusťte `ollama pull qwen3.5:4b` nebo
  `ollama pull qwen2.5-coder:7b`.
- **OpenClaw není připraven:** ověřte Node.js 22+ a dostupnost Ollama.

## Kredit a licence

Projekt je vydán pod [Apache License 2.0](LICENSE). Zachovává kredit původním
autorům [OpenJarvis](https://github.com/open-jarvis/OpenJarvis): Jon
Saad-Falcon, Avanika Narayan, Robby Manihani, Tanvir Bhathal, Herumb
Shandilya, Hakki Orhun Akengin, Gabriel Bo, Andrew Park, Matthew Hart, Caia
Costello, Chuan Li, Christopher Re a Azalia Mirhoseini.

Použité samostatné projekty a služby:

- [Ollama](https://github.com/ollama/ollama) (MIT) pro lokální modely,
- [OpenClaw](https://github.com/openclaw/openclaw) (MIT) pro lokálního agenta,
- [openWakeWord](https://github.com/dscripka/openWakeWord) (Apache-2.0) pro
  wake-word,
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT) pro
  lokální přepis řeči,
- [Piper](https://github.com/rhasspy/piper) (GPL-3.0-or-later) pro lokální
  syntézu řeči,
- [python-sounddevice](https://github.com/spatialaudio/python-sounddevice)
  (MIT), [SoundFile](https://github.com/bastibe/python-soundfile) (BSD-3),
  [websockets](https://github.com/python-websockets/websockets) (BSD-3),
- [pywebview](https://github.com/r0x0r/pywebview) (BSD-3) a
  [PyInstaller](https://pyinstaller.org/) pro nativní HUD,
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
  (MPL-2.0) pro volitelnou telemetrii,
- [psutil](https://github.com/giampaolo/psutil) (BSD-3-Clause) pro lokální
  přehled procesů a systémového vytížení,
- [eadmin2/jarvis_ai](https://github.com/eadmin2/jarvis_ai) (MIT) jako
  vizuální inspirace filmového HUDu,
- [Gemini API](https://ai.google.dev/gemini-api) a
  [OpenRouter](https://openrouter.ai/) jako volitelné online služby.

Licence jednotlivých modelů Qwen se řídí podmínkami jejich vydavatele v
Ollama registru. Tento repozitář neobsahuje modelové váhy ani API klíče.

## Stav vydání

`v0.6` je beta vydání. Před publikací každé změny se provádí lokální kontrola
syntaxí, služeb, hlasových adaptérů, bezpečnostních omezení a instalačního
skriptu. Nahrání na GitHub probíhá pouze po výslovném příkazu `nahraj na github`.
