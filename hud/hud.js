"use strict";

const api = "http://127.0.0.1:8000";
const controlApi = "http://127.0.0.1:8123";
const reactor = document.getElementById("reactor");
const activity = document.getElementById("activity");
const messages = document.getElementById("messages");
const form = document.getElementById("command-form");
const input = document.getElementById("command");
const muteButton = document.getElementById("mute-button");
const stopButton = document.getElementById("stop-button");
const voiceState = document.getElementById("voice-state");
const voiceMeter = document.getElementById("voice-meter");
const agentActivity = document.getElementById("agent-activity");
let recorder;
let audioChunks = [];
let wakeEnabled = false;
let wakeStream;
let muted = false;
let activeRequest;
let lastVoiceEventId = "";
let conversation = [];
let persistentRules = [];
let jarvisSettings = {};
let sidebarSection = "recent";
const modelRouter = {
  default: "qwen3.5:4b",
  complex: "qwen3.5:9b",
  coding: "qwen2.5-coder:7b",
  vision: "qwen3-vl:4b"
};

try {
  conversation = JSON.parse(localStorage.getItem("jarvis-local-conversation") || "[]");
  if (!Array.isArray(conversation)) conversation = [];
} catch (_) {
  conversation = [];
}

function saveConversation() {
  conversation = conversation.slice(-30);
  localStorage.setItem("jarvis-local-conversation", JSON.stringify(conversation));
}

function installFilmHud() {
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `/film-hud.css?version=18`;
  document.head.append(link);
  const core = document.querySelector(".core-panel");
  if (core && !core.querySelector(".arc-label")) {
    core.insertAdjacentHTML("afterbegin", '<div class="arc-label">ARC REACTOR<br><span>MARK VII</span></div>');
  }
  const rightRail = document.querySelector(".right-rail");
  if (rightRail && !document.getElementById("sensor-source")) {
    rightRail.insertAdjacentHTML("afterbegin", '<section class="panel telemetry-panel" aria-label="Živá hardwarová telemetrie"><h2>TELEMETRIE <small>05</small></h2><p class="sensor-source" id="sensor-source">ČEKÁM NA SENZORY</p><div class="gauges"><div class="gauge"><span class="gauge-name">CPU</span><strong id="cpu-temp">N/A</strong><i><b id="cpu-load"></b></i><small id="cpu-load-text">VYTÍŽENÍ N/A</small></div><div class="gauge"><span class="gauge-name">GPU</span><strong id="gpu-temp">N/A</strong><i><b id="gpu-load"></b></i><small id="gpu-load-text">VYTÍŽENÍ N/A</small></div><div class="gauge"><span class="gauge-name">RAM</span><strong id="ram-load">N/A</strong><i><b id="ram-load-bar"></b></i><small>VYUŽITÍ PAMĚTI</small></div></div><div class="disk-readout" id="disk-readout">DISKY: ČEKÁM NA DATA</div></section>');
  }
  if (rightRail && !document.getElementById("performance-grid")) {
    rightRail.insertAdjacentHTML("beforeend", '<section class="panel performance-panel" id="performance-panel"><h2>VÝKON <small>09</small></h2><div id="performance-grid" class="performance-grid"></div><div class="thermal-list" id="thermal-list">TEPLOTY: ČEKÁM NA DATA</div></section><section class="panel process-panel" id="process-panel"><h2>PROCESY <small>10</small></h2><p class="sensor-source">NEJVĚTŠÍ PROCESY PODLE PAMĚTI</p><div id="process-list" class="process-list">ČEKÁM NA DATA</div></section>');
  }
  const systemButton = document.querySelector('.view-button[data-view="system"]');
  systemButton?.closest(".panel")?.classList.add("views-panel");
  if (systemButton && !document.querySelector('.view-button[data-view="processes"]')) {
    systemButton.insertAdjacentHTML("afterend", '<button class="view-button" data-view="processes">PROCESY</button>');
  }
  installSidebar();
  if (!document.getElementById("processes-overlay")) {
    document.body.insertAdjacentHTML("beforeend", '<section id="processes-overlay" class="processes-overlay" aria-label="Správce procesů"><header><div class="brand"><span class="dot"></span><span>J.A.R.V.I.S. // PROCESY</span></div><nav class="process-nav" aria-label="Navigace HUDu"><button data-process-view="chat" type="button">KONVERZACE</button><button data-process-view="system" type="button">SYSTÉM</button><button id="close-processes" type="button">× ZAVŘÍT</button></nav></header><div class="processes-stage"><h1>PROCESY</h1><p>ŽIVÝ PŘEHLED VÝKONU VLASTNÍHO POČÍTAČE</p><div id="process-list-full" class="process-list-full"></div></div></section>');
    document.getElementById("close-processes").addEventListener("click", () => setHudView("chat"));
    document.querySelectorAll("[data-process-view]").forEach(button => button.addEventListener("click", () => setHudView(button.dataset.processView)));
  }
}

async function loadRules() {
  try {
    const response = await fetch(`${controlApi}/rules`, { cache: "no-store" });
    persistentRules = (await response.json()).rules || [];
  } catch (_) { persistentRules = []; }
}

async function controlGet(path) {
  const response = await fetch(`${controlApi}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Lokální konfigurace není dostupná");
  return response.json();
}

async function controlPost(path, payload) {
  const response = await fetch(`${controlApi}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error("Nastavení nelze uložit");
  return response.json();
}

function recentChats() {
  try { return JSON.parse(localStorage.getItem("jarvis-recent-chats") || "[]"); } catch (_) { return []; }
}

function saveRecentChats(chats) {
  localStorage.setItem("jarvis-recent-chats", JSON.stringify(chats.slice(0, 30)));
}

function openSidebar(section = sidebarSection) {
  sidebarSection = section;
  document.body.classList.add("sidebar-open");
  document.getElementById("sidebar")?.setAttribute("aria-hidden", "false");
  document.getElementById("sidebar-toggle")?.setAttribute("aria-expanded", "true");
  renderSidebar();
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  document.getElementById("sidebar")?.setAttribute("aria-hidden", "true");
  document.getElementById("sidebar-toggle")?.setAttribute("aria-expanded", "false");
}

async function renderSidebar() {
  const content = document.getElementById("sidebar-content");
  if (!content) return;
  document.querySelectorAll(".sidebar-nav button").forEach(button => button.classList.toggle("active", button.dataset.section === sidebarSection));
  try {
    if (sidebarSection === "recent") {
      const chats = recentChats();
      content.innerHTML = chats.length ? chats.map((chat, index) => `<button class="chat-item" data-chat="${index}"><b>${safeText(chat.title)}</b><small>${safeText(chat.time)}</small></button>`).join("") : '<p class="empty-state">Žádné uložené chaty.<br>Nový chat vytvoříš tlačítkem nahoře.</p>';
      content.querySelectorAll("[data-chat]").forEach(button => button.addEventListener("click", () => {
        const chat = recentChats()[Number(button.dataset.chat)];
        if (chat?.messages) { conversation = chat.messages; saveConversation(); messages.innerHTML = ""; conversation.forEach(item => addMessage(item.content, item.role === "assistant" ? "jarvis" : "user")); closeSidebar(); }
      }));
    } else if (sidebarSection === "projects") {
      const data = await controlGet("/projects");
      const entries = (data.projects || []).map(project => `<li><b>${safeText(project.name)}</b><small>${safeText(project.created_at)}</small></li>`).join("") || '<li class="empty-state">Žádné projekty.</li>';
      content.innerHTML = `<button class="primary-side" id="create-project">+ VYTVOŘIT PROJEKT</button><ul class="project-list">${entries}</ul>`;
      document.getElementById("create-project")?.addEventListener("click", async () => {
        const name = window.prompt("Název nového projektu:");
        if (!name?.trim()) return;
        await controlPost("/projects", { name: name.trim() });
        renderSidebar();
      });
    } else if (sidebarSection === "rules") {
      await loadRules();
      content.innerHTML = `<button id="show-rule-form" class="primary-side" type="button">＋ PŘIDAT PRAVIDLO</button><form id="rule-form" class="rule-form hidden"><label for="new-rule">NOVÉ TRVALÉ PRAVIDLO</label><textarea id="new-rule" maxlength="1000" placeholder="Například: Odpovídej stručně česky."></textarea><div class="rule-actions"><button class="primary-side" type="submit">ULOŽIT</button><button id="cancel-rule" type="button">ZRUŠIT</button></div></form>${persistentRules.length ? `<ol class="rule-list">${persistentRules.map((rule, index) => `<li><span>${safeText(rule)}</span><button type="button" data-remove-rule="${index}" aria-label="Odstranit pravidlo">×</button></li>`).join("")}</ol>` : '<p class="empty-state">Žádná pravidla.</p>'}`;
      document.getElementById("show-rule-form")?.addEventListener("click", () => {
        document.getElementById("show-rule-form").classList.add("hidden");
        document.getElementById("rule-form").classList.remove("hidden");
        document.getElementById("new-rule").focus();
      });
      document.getElementById("cancel-rule")?.addEventListener("click", renderSidebar);
      document.getElementById("rule-form")?.addEventListener("submit", async event => {
        event.preventDefault();
        const rule = document.getElementById("new-rule").value.trim();
        if (!rule) return;
        await rememberRule(rule);
        renderSidebar();
      });
      content.querySelectorAll("[data-remove-rule]").forEach(button => button.addEventListener("click", async () => {
        const index = Number(button.dataset.removeRule);
        if (!window.confirm("Odstranit toto trvalé pravidlo?")) return;
        const data = await controlPost("/rules/remove", { index });
        persistentRules = data.rules || [];
        renderSidebar();
      }));
    } else {
      jarvisSettings = await controlGet("/settings");
      content.innerHTML = `<form id="settings-form" class="settings-form"><label>Výchozí model<select name="default_model"><option value="qwen3.5:4b">Qwen 3.5 4B</option><option value="qwen3.5:9b">Qwen 3.5 9B</option><option value="qwen2.5-coder:7b">Qwen 2.5 Coder 7B</option></select></label><label><input type="checkbox" name="voice_output"> Hlasový výstup</label><label><input type="checkbox" name="wake_word"> Wake-word „Hey Jarvis“</label><label><input type="checkbox" name="start_with_windows"> Spustit JARVIS po přihlášení do Windows</label><label>Internet<select name="internet_mode"><option value="on_request">Pouze na pokyn</option><option value="always_online">Stále online</option><option value="offline">Pouze offline</option></select></label><label><input type="checkbox" name="project_start_required"> Projekty až po START</label><dl><dt>Cloudové API</dt><dd>VYPNUTO</dd><dt>Open source</dt><dd>ANO</dd><dt>Úložiště</dt><dd>A:\projekty\OpenJarvis</dd></dl><button class="primary-side" type="submit">ULOŽIT NASTAVENÍ</button></form>`;
      const formSettings = document.getElementById("settings-form");
      formSettings.default_model.value = jarvisSettings.default_model || modelRouter.default;
      formSettings.voice_output.checked = jarvisSettings.voice_output !== false;
      formSettings.wake_word.checked = jarvisSettings.wake_word !== false;
      formSettings.start_with_windows.checked = jarvisSettings.start_with_windows === true;
      formSettings.internet_mode.value = jarvisSettings.internet_mode || "on_request";
      formSettings.project_start_required.checked = jarvisSettings.project_start_required !== false;
      formSettings.addEventListener("submit", async event => {
        event.preventDefault();
        jarvisSettings = await controlPost("/settings", {
          default_model: formSettings.default_model.value, voice_output: formSettings.voice_output.checked,
          wake_word: formSettings.wake_word.checked, start_with_windows: formSettings.start_with_windows.checked, internet_mode: formSettings.internet_mode.value,
          project_start_required: formSettings.project_start_required.checked
        });
        modelRouter.default = jarvisSettings.default_model;
        muted = !jarvisSettings.voice_output;
        muteButton.setAttribute("aria-pressed", String(muted));
        muteButton.textContent = muted ? "🔇 HLAS ZTIŠEN" : "🔊 HLAS ZAPNUT";
        addActivity("LOKÁLNÍ NASTAVENÍ ULOŽENO");
      });
    }
  } catch (error) { content.innerHTML = `<p class="empty-state">${safeText(error.message)}</p>`; }
}

function installSidebar() {
  if (document.getElementById("sidebar")) return;
  document.querySelector("header")?.insertAdjacentHTML("beforeend", '<button id="sidebar-toggle" class="sidebar-toggle" type="button" aria-label="Otevřít panel JARVISu" aria-expanded="false">☰ PANEL</button>');
  document.body.insertAdjacentHTML("beforeend", '<aside id="sidebar" class="sidebar" aria-label="Lokální navigační panel JARVISu" aria-hidden="true"><div class="sidebar-head"><b>J.A.R.V.I.S.</b><button id="sidebar-close" type="button" aria-label="Zavřít panel">×</button></div><button id="new-chat" class="new-chat" type="button">＋ NOVÝ CHAT</button><nav class="sidebar-nav" aria-label="Sekce panelu"><button data-section="recent">◷ NEDÁVNÉ CHATY</button><button data-section="projects">▣ PROJEKTY</button><button data-section="rules">⌁ PRAVIDLA</button><button data-section="settings">⚙ NASTAVENÍ</button></nav><section id="sidebar-content" class="sidebar-content"></section><div class="sidebar-foot">LOKÁLNÍ · A: · BEZ TOKENŮ</div></aside><button id="sidebar-backdrop" class="sidebar-backdrop" type="button" aria-label="Zavřít panel"></button>');
  document.getElementById("sidebar-toggle").addEventListener("click", () => openSidebar());
  document.getElementById("sidebar-close").addEventListener("click", closeSidebar);
  document.getElementById("sidebar-backdrop").addEventListener("click", closeSidebar);
  document.querySelectorAll(".sidebar-nav button").forEach(button => button.addEventListener("click", () => openSidebar(button.dataset.section)));
  document.getElementById("new-chat").addEventListener("click", () => {
    if (conversation.length) saveRecentChats([{ title: conversation.at(-1)?.content?.slice(0, 48) || "Nový chat", time: new Date().toLocaleString("cs-CZ"), messages: conversation }, ...recentChats()]);
    conversation = []; saveConversation(); messages.innerHTML = ""; addMessage("Nové lokální sezení připraveno.", "jarvis"); closeSidebar();
  });
}

async function rememberRule(rule) {
  const response = await fetch(`${controlApi}/rules`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule })
  });
  if (!response.ok) throw new Error("Pravidlo nelze uložit");
  persistentRules = (await response.json()).rules || persistentRules;
}

function ruleFromCommand(command) {
  const match = command.match(/^(?:zapamatuj si(?:,? že)?|pravidlo:?|od teď|odted|vždy|vzdy)\s*(.+)$/i);
  return match?.[1]?.trim() || "";
}

function selectModel(text) {
  const request = text.toLowerCase();
  if (/\b(kód|code|program|skript|debug|chyba|html|css|javascript|python|api|plugin|refaktor)\b/.test(request)) return modelRouter.coding;
  if (/\b(obrázek|screenshot|fotku|video|kamera)\b/.test(request)) return modelRouter.vision;
  if (/\b(analyzuj|naplánuj|architektura|porovnej|výzkum|strategie|autonom)\b/.test(request)) return modelRouter.complex;
  return modelRouter.default;
}
const wakeToggle = document.getElementById("wake-toggle");

function addMessage(text, type) {
  const item = document.createElement("article");
  item.className = `message ${type}`;
  item.textContent = text;
  messages.append(item);
  messages.scrollTop = messages.scrollHeight;
}

function addActivity(text) {
  const item = document.createElement("p");
  item.textContent = text;
  agentActivity.prepend(item);
}

function setActivity(text, state = "") {
  activity.textContent = text;
  reactor.className = `reactor ${state}`;
}

function speak(text) {
  if (muted || !("speechSynthesis" in window) || !text) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "cs-CZ";
  utterance.rate = 1.03;
  window.speechSynthesis.speak(utterance);
}

function stopPlayback() {
  window.speechSynthesis?.cancel();
}

function stopCurrentRun() {
  stopPlayback();
  activeRequest?.abort();
  activeRequest = null;
  stopButton.classList.remove("active");
  setActivity("TAH ZASTAVEN");
  addActivity("PŘERUŠENO UŽIVATELEM");
}

async function sendCommand(text) {
  const command = text.trim();
  if (!command) return;
  if (/^(?:otevři|otevri|ukaž|ukaz) (?:panel|postranní panel|boční panel)$/i.test(command)) {
    openSidebar();
    addMessage(command, "user");
    addMessage("Lokální panel je otevřen.", "jarvis");
    input.value = "";
    return;
  }
  if (/^(?:zavři|zavri|skryj) (?:panel|postranní panel|boční panel)$/i.test(command)) {
    closeSidebar();
    input.value = "";
    return;
  }
  const newRule = ruleFromCommand(command);
  if (newRule) {
    try {
      await rememberRule(newRule);
      addMessage(command, "user");
      addMessage(`Trvalé pravidlo bylo uloženo lokálně: ${newRule}`, "jarvis");
      addActivity("TRVALÉ PRAVIDLO ULOŽENO");
    } catch (error) {
      addMessage(`Pravidlo nebylo uloženo: ${error.message}`, "jarvis");
    }
    input.value = "";
    return;
  }
  if (/^(?:ukaž|ukaz|vypiš|vypis) pravidla$/i.test(command)) {
    addMessage(command, "user");
    addMessage(persistentRules.length ? `Trvalá pravidla:\n${persistentRules.map((rule, index) => `${index + 1}. ${rule}`).join("\n")}` : "Žádná trvalá pravidla nejsou uložena.", "jarvis");
    input.value = "";
    return;
  }
  const selectedModel = selectModel(command);
  addMessage(command, "user");
  conversation.push({ role: "user", content: command });
  saveConversation();
  setActivity("Zpracovávám požadavek", "thinking");
  input.value = "";
  activeRequest = new AbortController();
  stopButton.classList.add("active");
  addActivity(`MODEL ${selectedModel}: ${command.slice(0, 55)}`);
  try {
    const response = await fetch(`${api}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: activeRequest.signal,
      body: JSON.stringify({ model: selectedModel, messages: [{ role: "system", content: persistentRules.length ? `Trvalá uživatelská pravidla (dodržuj v rámci bezpečného a zákonného použití):\n${persistentRules.map(rule => `- ${rule}`).join("\n")}` : "" }, ...conversation], stream: false })
    });
    if (!response.ok) throw new Error(`Server vrátil ${response.status}`);
    const data = await response.json();
    const answer = data.choices?.[0]?.message?.content || "Odpověď nebyla vrácena.";
    addMessage(answer, "jarvis");
    conversation.push({ role: "assistant", content: answer });
    saveConversation();
    speak(answer);
    addActivity("ODPOVĚĎ DOKONČENA");
    setActivity("Připraven naslouchat");
  } catch (error) {
    if (error.name === "AbortError") addMessage("Tah byl zastaven.", "jarvis");
    else { addMessage(`Chyba spojení: ${error.message}`, "jarvis"); setActivity("Server není dostupný"); }
  } finally {
    activeRequest = null;
    stopButton.classList.remove("active");
  }
}

async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia) { addMessage("Prohlížeč nepodporuje mikrofon.", "jarvis"); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks = [];
    recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    recorder.ondataavailable = event => audioChunks.push(event.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      setActivity("Přepisuji řeč", "thinking");
      const formData = new FormData();
      formData.append("file", new Blob(audioChunks, { type: "audio/webm" }), "hlas.webm");
      formData.append("language", "cs");
      try {
        const response = await fetch(`${api}/v1/speech/transcribe`, { method: "POST", body: formData });
        if (!response.ok) throw new Error(`Přepis selhal (${response.status})`);
        const data = await response.json();
        await sendCommand(data.text || "");
      } catch (error) { addMessage(`Hlasový vstup: ${error.message}`, "jarvis"); setActivity("Připraven naslouchat"); }
    };
    recorder.start();
    setActivity("NASLOUCHÁM — klikni znovu pro odeslání", "listening");
  } catch (error) { addMessage(`Mikrofon není dostupný: ${error.message}`, "jarvis"); }
}

async function transcribe(blob) {
  const formData = new FormData();
  formData.append("file", blob, "hey-jarvis.webm");
  formData.append("language", "cs");
  const response = await fetch(`${api}/v1/speech/transcribe`, { method: "POST", body: formData });
  if (!response.ok) throw new Error(`Přepis selhal (${response.status})`);
  return (await response.json()).text || "";
}

async function startWakeListening() {
  try {
    wakeStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    wakeEnabled = true;
    wakeToggle.setAttribute("aria-pressed", "true");
    wakeToggle.textContent = "„HEY JARVIS“ AKTIVNÍ";
    voiceState.textContent = "MIKROFON: PRŮBĚŽNÝ POSLECH";
    voiceMeter.style.width = "82%";
    addActivity("WAKE-WORD AKTIVOVÁN");
    setActivity("ČEKÁM NA „HEY JARVIS“", "listening");
    recordWakeChunk();
  } catch (error) {
    addMessage(`Mikrofon je nutné povolit: ${error.message}`, "jarvis");
  }
}

function stopWakeListening() {
  wakeEnabled = false;
  recorder?.stop();
  wakeStream?.getTracks().forEach(track => track.stop());
  wakeStream = null;
  wakeToggle.setAttribute("aria-pressed", "false");
  wakeToggle.textContent = "AKTIVOVAT „HEY JARVIS“";
  voiceState.textContent = "MIKROFON: VYPNUT";
  voiceMeter.style.width = "10%";
  setActivity("Připraven naslouchat");
}

function recordWakeChunk() {
  if (!wakeEnabled || !wakeStream) return;
  const chunkRecorder = new MediaRecorder(wakeStream, { mimeType: "audio/webm" });
  const chunks = [];
  chunkRecorder.ondataavailable = event => chunks.push(event.data);
  chunkRecorder.onstop = async () => {
    if (!wakeEnabled) return;
    try {
      const heard = await transcribe(new Blob(chunks, { type: "audio/webm" }));
      const match = heard.match(/(?:\b(?:hej|hey)\s+jarvis|\bjarvis)\b[,.!]?\s*(.*)/i);
      if (match) {
        // Barge-in: jméno přeruší čtení a rozpracovanou odpověď okamžitě po lokálním rozpoznání.
        stopPlayback();
        activeRequest?.abort();
        const command = match[1].trim();
        addActivity("AKTIVAČNÍ SLOVO ROZPOZNÁNO");
        setActivity("JARVIS AKTIVOVÁN", "thinking");
        if (command) await sendCommand(command);
        else await recordFollowUpCommand();
      }
    } catch (error) { console.warn("Wake-word transcription failed", error); }
    if (wakeEnabled) recordWakeChunk();
  };
  chunkRecorder.start();
  setTimeout(() => { if (chunkRecorder.state === "recording") chunkRecorder.stop(); }, 1200);
}

function recordFollowUpCommand() {
  return new Promise(resolve => {
    if (!wakeEnabled || !wakeStream) { resolve(); return; }
    const commandRecorder = new MediaRecorder(wakeStream, { mimeType: "audio/webm" });
    const chunks = [];
    commandRecorder.ondataavailable = event => chunks.push(event.data);
    commandRecorder.onstop = async () => {
      try {
        setActivity("Přepisuji příkaz", "thinking");
        const command = await transcribe(new Blob(chunks, { type: "audio/webm" }));
        if (command.trim()) await sendCommand(command);
        else setActivity("ČEKÁM NA „HEY JARVIS“", "listening");
      } catch (error) {
        addMessage(`Hlasový příkaz nebyl rozpoznán: ${error.message}`, "jarvis");
      }
      resolve();
    };
    addMessage("Naslouchám příkazu.", "jarvis");
    commandRecorder.start();
    setActivity("NASLOUCHÁM PŘÍKAZU", "listening");
    setTimeout(() => { if (commandRecorder.state === "recording") commandRecorder.stop(); }, 7000);
  });
}

reactor.addEventListener("click", () => recorder?.state === "recording" ? recorder.stop() : startRecording());
form.addEventListener("submit", event => { event.preventDefault(); sendCommand(input.value); });
wakeToggle.addEventListener("click", () => {
  addMessage("Trvalý wake-word zajišťuje samostatný lokální klient. Je aktivní po spuštění Jarvise.", "jarvis");
});
muteButton.addEventListener("click", () => {
  muted = !muted;
  if (muted) stopPlayback();
  muteButton.setAttribute("aria-pressed", String(muted));
  muteButton.textContent = muted ? "🔇 HLAS ZTIŠEN" : "🔊 HLAS ZAPNUT";
  addActivity(muted ? "HLASOVÝ VÝSTUP ZTIŠEN" : "HLASOVÝ VÝSTUP AKTIVNÍ");
});
stopButton.addEventListener("click", stopCurrentRun);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") stopCurrentRun();
  if (event.key.toLowerCase() === "b") { document.body.className = "booting"; setTimeout(() => { document.body.className = "booted"; }, 30); }
  if (event.code === "Space" && document.activeElement !== input) { event.preventDefault(); reactor.click(); }
});
async function refreshHealth() {
  try {
    const speech = await fetch(`${api}/v1/speech/health`).then(response => response.json());
    document.getElementById("speech-state").textContent = speech.available ? "ONLINE" : "CHYBA";
    document.getElementById("api-state").textContent = "ONLINE";
    document.getElementById("ollama-state").textContent = "LOKÁLNÍ";
    document.getElementById("diagnostics").textContent = `ŘEČ: ${speech.backend || "n/a"} · VŠECHNY SLUŽBY ONLINE`;
  } catch (_) {
    document.getElementById("api-state").textContent = "NEDOSTUPNÉ";
    document.getElementById("diagnostics").textContent = "NELZE SE PŘIPOJIT K LOKÁLNÍMU API";
  }
}

function metric(value, suffix = "%") {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value)}${suffix}` : "N/A";
}

function safeText(value) {
  return String(value ?? "N/A").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function renderPerformance(data) {
  const performance = data.performance || {};
  const cards = [
    ["CPU FREKVENCE", metric(performance.cpu_clock_mhz, " MHz")],
    ["CPU PŘÍKON", metric(performance.cpu_power_w, " W")],
    ["GPU FREKVENCE", metric(performance.gpu_clock_mhz, " MHz")],
    ["GPU PŘÍKON", metric(performance.gpu_power_w, " W")],
    ["RAM POUŽITO", metric(performance.ram_used_gb, " GB")],
    ["RAM VOLNÁ", metric(performance.ram_available_gb, " GB")],
    ["SÍŤ", metric(performance.network_load)],
    ["VENTILÁTOR", metric(performance.fan_rpm, " RPM")]
  ];
  document.getElementById("performance-grid").innerHTML = cards.map(([label, value]) => `<div><small>${label}</small><strong>${value}</strong></div>`).join("");
  document.getElementById("thermal-list").innerHTML = (data.temperatures || []).slice(0, 10).map(item => `<span>${safeText(item.hardware)} · ${safeText(item.name)} <b>${safeText(item.unit)}</b></span>`).join("") || "TEPLOTY: N/A";
}

function renderDisks(disks) {
  const container = document.getElementById("disk-readout");
  container.innerHTML = (disks || []).map(disk => `<div class="disk-gauge"><span>${safeText(disk.name)}</span><b>${safeText(disk.used)}%</b><i><em style="width:${Math.max(0, Math.min(100, Number(disk.used) || 0))}%"></em></i><small>${safeText(disk.free_gb)} GB VOLNO</small></div>`).join("") || "DISKY: ČEKÁM NA DATA";
}

function bindProcessControls(target) {
  target.querySelectorAll("[data-kill-pid]").forEach(button => button.addEventListener("click", async () => {
    const pid = Number(button.dataset.killPid);
    if (!Number.isInteger(pid) || !window.confirm(`Ukončit proces PID ${pid}? Neuložená data programu mohou být ztracena.`)) return;
    button.disabled = true;
    try {
      await controlPost("/processes/terminate", { pid });
      addActivity(`UKONČEN PROCES PID ${pid}`);
      refreshHardware();
    } catch (error) {
      window.alert(`Proces nebyl ukončen: ${error.message}`);
      button.disabled = false;
    }
  }));
  target.querySelectorAll(".process-row").forEach(row => row.style.setProperty("display", "block", "important"));
}

function renderProcesses(processes) {
  const markup = '<div class="process-heading">LOKÁLNÍ PROCESY · CPU · PAMĚŤ · STAV</div>' + ((processes || []).map(item => `<article class="process-row"><div class="process-primary"><b title="${safeText(item.name)}">${safeText(item.name)}</b><button type="button" data-kill-pid="${safeText(item.pid)}">UKONČIT</button></div><div class="process-metrics"><span>PID <b>${safeText(item.pid)}</b></span><span>CPU <b>${safeText(item.cpu_percent)}%</b></span><span>RAM <b>${safeText(item.memory_mb)} MB</b></span><span>STAV <b>${safeText(item.status)}</b></span><span>VLÁKNA <b>${safeText(item.threads)}</b></span><span>HANDLY <b>${safeText(item.handles)}</b></span></div></article>`).join("") || "ŽÁDNÁ DATA");
  [document.getElementById("process-list"), document.getElementById("process-list-full")].filter(Boolean).forEach(target => {
    target.innerHTML = markup;
    bindProcessControls(target);
  });
}

async function refreshHardware() {
  const source = document.getElementById("sensor-source");
  if (!source) return;
  try {
    const response = await fetch(`/hardware-status.json?time=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Nedostupné");
    const data = await response.json();
    source.textContent = data.online ? "ŽIVÉ SENZORY // LOCALHOST" : (data.message || "ČEKÁM NA SENZORY");
    document.getElementById("cpu-temp").textContent = metric(data.cpu?.temperature, "°C");
    document.getElementById("gpu-temp").textContent = metric(data.gpu?.temperature, "°C");
    document.getElementById("ram-load").textContent = metric(data.ram?.load);
    const items = [["cpu-load", "cpu-load-text", data.cpu?.load], ["gpu-load", "gpu-load-text", data.gpu?.load], ["ram-load-bar", null, data.ram?.load]];
    items.forEach(([barId, textId, value]) => {
      document.getElementById(barId).style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
      if (textId) document.getElementById(textId).textContent = `VYTÍŽENÍ ${metric(value)}`;
    });
    renderDisks(data.disks);
    renderPerformance(data);
    renderProcesses(data.processes);
  } catch (_) { source.textContent = "SENZORY NEDOSTUPNÉ"; }
}

async function pollVoiceEvents() {
  try {
    const response = await fetch(`/voice-event.json?time=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return;
    const event = await response.json();
    if (!event.id || event.id === lastVoiceEventId) return;
    lastVoiceEventId = event.id;
    if (event.type === "voice_ready") {
      wakeToggle.setAttribute("aria-pressed", "true");
      wakeToggle.textContent = "„HEY JARVIS“ AKTIVNÍ";
      voiceState.textContent = "MIKROFON: LOKÁLNÍ WAKE-WORD";
      voiceMeter.style.width = "100%";
      addActivity("OPENWAKEWORD PŘIPRAVEN");
    } else if (event.type === "wake_detected") {
      stopPlayback();
      activeRequest?.abort();
      setActivity("HEY JARVIS ROZPOZNÁN", "listening");
      addActivity("AKTIVAČNÍ SLOVO ROZPOZNÁNO");
    } else if (event.type === "voice_command") {
      addMessage(event.text, "user");
      setActivity("ZPRACOVÁVÁM HLASOVÝ PŘÍKAZ", "thinking");
    } else if (event.type === "voice_answer") {
      addMessage(event.text, "jarvis");
      speak(event.text);
      setActivity("ČEKÁM NA HEY JARVIS", "listening");
      addActivity("HLASOVÁ ODPOVĎ DOKONČENA");
    } else if (event.type === "error") {
      addMessage(event.text, "jarvis");
      setActivity("CHYBA HLASOVÉHO MODULU");
    }
  } catch (_) { /* Hlasový klient se může právě spouštět. */ }
}
setInterval(() => { document.getElementById("clock").textContent = new Date().toLocaleTimeString("cs-CZ"); }, 1000);
setInterval(refreshHealth, 15000);
setInterval(pollVoiceEvents, 500);
setInterval(refreshHardware, 2000);
installFilmHud();
setTimeout(() => { document.body.className = "booted"; refreshHealth(); refreshHardware(); loadRules(); }, 90);
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}

function setHudView(view) {
  document.body.dataset.view = view;
  localStorage.setItem("jarvis-hud-view", view);
  const leftRail = document.querySelector(".left-rail");
  const corePanel = document.querySelector(".core-panel");
  const rightRail = document.querySelector(".right-rail");
  const processPanel = document.getElementById("process-panel");
  const processesOverlay = document.getElementById("processes-overlay");
  const panels = [...document.querySelectorAll(".right-rail > .panel")];
  [leftRail, corePanel, rightRail, processPanel, ...panels].forEach(element => element?.style.removeProperty("display"));
  rightRail?.style.removeProperty("grid-column");
  if (view === "processes") {
    processesOverlay?.style.setProperty("display", "block", "important");
    leftRail?.style.setProperty("display", "none", "important");
    corePanel?.style.setProperty("display", "none", "important");
    rightRail?.style.setProperty("grid-column", "1 / -1", "important");
    rightRail?.style.setProperty("display", "block", "important");
    panels.filter(panel => !panel.classList.contains("views-panel") && panel !== processPanel)
      .forEach(panel => panel.style.setProperty("display", "none", "important"));
    processPanel?.style.setProperty("display", "block", "important");
  } else if (view === "system") {
    processesOverlay?.style.setProperty("display", "none", "important");
    leftRail?.style.setProperty("display", "none", "important");
    corePanel?.style.setProperty("display", "none", "important");
    rightRail?.style.setProperty("grid-column", "1 / -1", "important");
    panels.filter(panel => panel === processPanel).forEach(panel => panel.style.setProperty("display", "none", "important"));
  } else {
    processesOverlay?.style.setProperty("display", "none", "important");
    panels.filter(panel => !panel.classList.contains("views-panel"))
      .forEach(panel => panel.style.setProperty("display", "none", "important"));
  }
  document.querySelectorAll(".view-button").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  addActivity(view === "system" ? "PŘEPNUTO NA SYSTÉMOVÝ POHLED" : "PŘEPNUTO NA KONVERZACI");
}

document.querySelectorAll(".view-button").forEach(button => {
  button.addEventListener("click", () => setHudView(button.dataset.view));
});
setHudView("chat");
