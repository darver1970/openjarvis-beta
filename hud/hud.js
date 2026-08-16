"use strict";

const api = "http://127.0.0.1:8000";
const controlApi = "http://127.0.0.1:8126";
const providerLabels = {
  local: "LOKÁLNÍ OLLAMA",
  gemini_free: "GEMINI FREE",
  openrouter_free: "OPENROUTER FREE",
  automatic: "AUTOMATICKY"
};
const providerModels = {
  local: "QWEN 3.5 4B",
  gemini_free: "GEMINI 3.5 FLASH",
  openrouter_free: "OPENROUTER FREE",
  automatic: "GEMINI 3.5 FLASH · PRVNÍ VOLBA"
};
let configuredProvider = "local";
let activeProvider = null;
let lastProviderAt = "";
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
let muted = false;
let activeRequest;
let lastVoiceEventId = "";
let activeVoiceMessage = null;
let voiceAudio = null;
let voiceUtterance = null;
let voiceQueue = [];
let voicePlaybackEpoch = 0;
let conversation = [];
let persistentRules = [];
let projectMemory = { project: "OpenJarvis Beta", summary: "", entries: [] };
let jarvisSettings = {};
let agentRegistry = {
  agents: [{ id: "jarvis", name: "JARVIS", role: "Koordinátor", model: "qwen3.5:4b", status: "ready" }],
  active_agent_id: "jarvis"
};
let sidebarSection = "recent";
let workbenchTab = "live";
let workbenchActivity = [];
let workbenchFile = "";
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
  link.href = `/film-hud.css?version=34`;
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
  const processesButton = document.querySelector('.view-button[data-view="processes"]');
  if (processesButton && !document.getElementById("exit-hud")) {
    processesButton.insertAdjacentHTML("afterend", '<button id="exit-hud" class="view-button exit-hud" type="button" aria-label="Ukončit JARVIS">UKONČIT</button>');
    document.getElementById("exit-hud").addEventListener("click", () => {
      stopPlayback();
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.close();
        return;
      }
      window.close();
    });
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
  closeAgentsSidebar();
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

function nativeWorkspaceApi() {
  return window.pywebview?.api || null;
}

function openWorkbench(tab = workbenchTab) {
  closeSidebar();
  closeAgentsSidebar();
  workbenchTab = tab;
  document.body.classList.add("workbench-open");
  document.getElementById("workbench")?.setAttribute("aria-hidden", "false");
  document.getElementById("workbench-toggle")?.setAttribute("aria-expanded", "true");
  renderWorkbench();
}

function closeWorkbench() {
  document.body.classList.remove("workbench-open");
  document.getElementById("workbench")?.setAttribute("aria-hidden", "true");
  document.getElementById("workbench-toggle")?.setAttribute("aria-expanded", "false");
}

function setWorkbenchWidth(width) {
  const safeWidth = Math.max(360, Math.min(920, Math.round(width)));
  document.documentElement.style.setProperty("--workbench-width", `${safeWidth}px`);
  localStorage.setItem("jarvis-workbench-width", String(safeWidth));
}

function bindWorkbenchResize() {
  const handle = document.getElementById("workbench-resizer");
  if (!handle) return;
  setWorkbenchWidth(Number(localStorage.getItem("jarvis-workbench-width")) || 520);
  handle.addEventListener("pointerdown", event => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const move = moveEvent => setWorkbenchWidth(window.innerWidth - moveEvent.clientX);
    const end = endEvent => {
      handle.releasePointerCapture(endEvent.pointerId);
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
  });
}

async function openWorkbenchFile(path) {
  const api = nativeWorkspaceApi();
  if (!api) throw new Error("Pracovna vyžaduje vlastní Jarvis-HUD.exe.");
  const file = await api.read_file(path);
  workbenchFile = file.path;
  document.getElementById("workbench-detail").innerHTML = `<div class="workbench-file-head"><b>${safeText(file.path)}</b><button id="workbench-context" type="button" title="Vložit soubor do konverzace">↗</button><button id="workbench-diff" type="button" title="Zobrazit Git diff">Δ</button></div><pre>${safeText(file.content)}</pre>`;
  document.getElementById("workbench-context")?.addEventListener("click", () => {
    input.value = `Použij jako kontext soubor ${file.path}:\n\n${file.content.slice(0, 12000)}`;
    input.focus();
    closeWorkbench();
  });
  document.getElementById("workbench-diff")?.addEventListener("click", async () => {
    const diff = await api.git_diff(file.path);
    document.getElementById("workbench-detail").innerHTML = `<div class="workbench-file-head"><b>DIFF · ${safeText(diff.path)}</b><button id="workbench-back-file" type="button">←</button></div><pre>${safeText(diff.content)}</pre>`;
    document.getElementById("workbench-back-file")?.addEventListener("click", () => openWorkbenchFile(file.path));
  });
}

async function renderWorkbench() {
  const content = document.getElementById("workbench-content");
  if (!content) return;
  document.querySelectorAll("[data-workbench-tab]").forEach(button => button.classList.toggle("active", button.dataset.workbenchTab === workbenchTab));
  const api = nativeWorkspaceApi();
  if (!api) { content.innerHTML = '<p class="empty-state">Vlastní Jarvis-HUD.exe není aktivní.</p>'; return; }
  try {
    if (workbenchTab === "live") {
      const status = await api.git_status();
      const feed = workbenchActivity.length ? workbenchActivity.map(item => `<li><time>${safeText(item.time)}</time>${safeText(item.text)}</li>`).join("") : '<li>Čekám na první úkol.</li>';
      const changes = status.entries.length ? status.entries.map(item => `<li>${safeText(item)}</li>`).join("") : '<li>ČISTÝ STAV</li>';
      content.innerHTML = `<section class="workbench-section"><h3>AKTUÁLNÍ ČINNOST</h3><ol class="workbench-feed">${feed}</ol></section><section class="workbench-section"><h3>GIT</h3><ul class="workbench-git">${changes}</ul></section><button id="create-snapshot" class="primary-side" type="button">VYTVOŘIT NÁVRATOVÝ BOD</button><section class="workbench-section"><h3>KARANTÉNA</h3><ul id="quarantine-list" class="workbench-git"></ul></section>`;
      document.getElementById("create-snapshot")?.addEventListener("click", async () => {
        const snapshot = await api.create_snapshot(window.prompt("Název návratového bodu:", "před úpravou") || "bod");
        addActivity(`NÁVRATOVÝ BOD: ${snapshot.name}`);
        renderWorkbench();
      });
      const quarantine = await api.list_quarantine();
      document.getElementById("quarantine-list").innerHTML = quarantine.length ? quarantine.map(item => `<li>${safeText(item.name)} · ${safeText(item.size)} B</li>`).join("") : '<li>PRÁZDNÁ</li>';
    } else if (workbenchTab === "files") {
      const directory = await api.list_files(workbenchFile && workbenchFile.includes("/") ? workbenchFile.slice(0, workbenchFile.lastIndexOf("/")) : "");
      const parent = directory.parent === null ? "" : `<button class="workbench-entry" data-workbench-dir="${safeText(directory.parent)}" type="button">..</button>`;
      const entries = directory.entries.map(item => `<button class="workbench-entry" data-workbench-${item.kind === "directory" ? "dir" : "file"}="${safeText(item.path)}" type="button"><b>${item.kind === "directory" ? "▸" : "·"}</b>${safeText(item.name)}</button>`).join("");
      content.innerHTML = `<div class="workbench-path">/${safeText(directory.path)}</div><div class="workbench-files">${parent}${entries || '<p class="empty-state">Prázdný adresář.</p>'}</div><section id="workbench-detail" class="workbench-detail"><p class="empty-state">Vyber soubor pro náhled.</p></section>`;
      content.querySelectorAll("[data-workbench-dir]").forEach(button => button.addEventListener("click", async () => { workbenchFile = `${button.dataset.workbenchDir}/.`.replace(/^\//, ""); await renderWorkbench(); }));
      content.querySelectorAll("[data-workbench-file]").forEach(button => button.addEventListener("click", () => openWorkbenchFile(button.dataset.workbenchFile).catch(error => window.alert(error.message))));
    } else {
      const browserState = await api.list_browser_tabs();
      const tabs = browserState.tabs.map(tab => {
        const active = tab.id === browserState.active_tab_id ? " active" : "";
        const label = tab.url.replace(/^https?:\/\//, "").slice(0, 24);
        return `<div class="workbench-tab${active}"><button data-browser-tab="${safeText(tab.id)}" type="button" title="${safeText(tab.url)}">${safeText(label)}</button><button data-browser-close="${safeText(tab.id)}" type="button" aria-label="Zavřít kartu">×</button></div>`;
      }).join("") || '<p class="empty-state">Otevři první kartu.</p>';
      content.innerHTML = `<div class="workbench-browser-tabs">${tabs}</div><form id="workbench-browser-form" class="workbench-browser"><input id="workbench-address" value="https://github.com/" aria-label="Adresa webové stránky"><button type="submit" title="Otevřít aktuální kartu">↗</button></form><div class="workbench-browser-actions"><button id="new-browser-tab" type="button" title="Nová karta">＋</button><button data-browser-action="back" type="button" title="Zpět">←</button><button data-browser-action="forward" type="button" title="Vpřed">→</button><button data-browser-action="reload" type="button" title="Obnovit">↻</button><button data-browser-url="https://github.com/" type="button">GITHUB</button></div><form id="quarantine-download-form" class="workbench-download"><input id="quarantine-download-url" type="url" inputmode="url" placeholder="https://.../soubor.zip" aria-label="Adresa souboru ke stažení do karantény"><button type="submit">STÁHNOUT</button></form><p id="browser-state" class="empty-state">Přihlášení provádíš přímo ve vlastním okně JARVIS WEB.</p>`;
      const openBrowser = async url => {
        const result = await api.open_browser(url);
        document.getElementById("browser-state").textContent = `OTEVŘENO: ${result.url}`;
        addActivity(`JARVIS WEB: ${result.url}`);
        renderWorkbench();
      };
      document.getElementById("workbench-browser-form").addEventListener("submit", event => { event.preventDefault(); openBrowser(document.getElementById("workbench-address").value).catch(error => window.alert(error.message)); });
      document.getElementById("new-browser-tab")?.addEventListener("click", async () => {
        try { await api.new_browser_tab(document.getElementById("workbench-address").value); addActivity("JARVIS WEB: NOVÁ KARTA"); renderWorkbench(); } catch (error) { window.alert(error.message); }
      });
      document.getElementById("quarantine-download-form")?.addEventListener("submit", async event => {
        event.preventDefault();
        const url = document.getElementById("quarantine-download-url").value;
        if (!window.confirm("Stáhnout tento soubor pouze do místní karantény JARVISu?")) return;
        try { const file = await api.download_to_quarantine(url); document.getElementById("browser-state").textContent = `KARANTÉNA: ${file.name}`; addActivity(`KARANTÉNA: ${file.name}`); } catch (error) { window.alert(error.message); }
      });
      content.querySelectorAll("[data-browser-tab]").forEach(button => button.addEventListener("click", async () => { try { await api.select_browser_tab(button.dataset.browserTab); renderWorkbench(); } catch (error) { window.alert(error.message); } }));
      content.querySelectorAll("[data-browser-close]").forEach(button => button.addEventListener("click", async () => { try { await api.close_browser_tab(button.dataset.browserClose); renderWorkbench(); } catch (error) { window.alert(error.message); } }));
      content.querySelectorAll("[data-browser-url]").forEach(button => button.addEventListener("click", () => openBrowser(button.dataset.browserUrl).catch(error => window.alert(error.message))));
      content.querySelectorAll("[data-browser-action]").forEach(button => button.addEventListener("click", () => api.browser_action(button.dataset.browserAction).catch(error => window.alert(error.message))));
    }
  } catch (error) { content.innerHTML = `<p class="empty-state">${safeText(error.message)}</p>`; }
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
    } else if (sidebarSection === "agents") {
      agentRegistry = await controlGet("/agents");
      const agents = agentRegistry.agents || [];
      const cards = agents.map(agent => {
        const active = agent.id === agentRegistry.active_agent_id;
        const paused = agent.status === "paused";
        return `<article class="agent-card${active ? " active" : ""}"><button class="agent-select" data-agent-select="${safeText(agent.id)}" type="button" ${agent.status === "planned" ? "disabled" : ""}><b>${safeText(agent.name)}</b><small>${safeText(agent.role)}</small><span>${agent.status === "planned" ? "ČEKÁ NA PŘÍPRAVU" : paused ? "POZASTAVEN" : agent.status === "working" ? "PRACUJE" : agent.status === "error" ? "CHYBA" : active ? "AKTIVNÍ" : "PŘIPRAVEN"}</span>${agent.current_task ? `<small>ÚKOL: ${safeText(agent.current_task)}</small>` : ""}${agent.last_result ? `<small>POSLEDNÍ: ${safeText(agent.last_result)}</small>` : ""}</button><button class="agent-toggle" data-agent-toggle="${safeText(agent.id)}" type="button" ${agent.id === "jarvis" || agent.status === "working" || agent.status === "planned" ? "disabled" : ""}>${agent.status === "planned" ? "ČEKÁ" : paused ? "SPUSTIT" : "POZASTAVIT"}</button></article>`;
      }).join("") || '<p class="empty-state">Žádní agenti nejsou nainstalováni.</p>';
      content.innerHTML = `<button id="show-agent-catalog" class="primary-side" type="button">＋ INSTALOVAT AGENTA</button><p class="agent-note">Agent pracuje pouze v rozsahu svých pravidel a oprávnění.</p><div class="agent-list">${cards}</div>`;
      content.querySelectorAll("[data-agent-select]").forEach(button => button.addEventListener("click", async () => {
        agentRegistry = await controlPost("/agents/activate", { agent_id: button.dataset.agentSelect });
        addActivity(`AKTIVNÍ AGENT: ${button.textContent.trim()}`);
        renderSidebar();
      }));
      content.querySelectorAll("[data-agent-toggle]").forEach(button => button.addEventListener("click", async () => {
        if (!button.dataset.agentToggle) return;
        agentRegistry = await controlPost("/agents/toggle", { agent_id: button.dataset.agentToggle });
        renderSidebar();
      }));
      document.getElementById("show-agent-catalog")?.addEventListener("click", () => renderAgentCatalog());
    } else {
      jarvisSettings = await controlGet("/settings");
      const [audio, voiceConfig] = await Promise.all([controlGet("/audio/devices"), controlGet("/voice/config")]);
      const outputs = await audioOutputDevices();
      let providerData = await controlGet("/providers");
      const inputOptions = (audio.inputs || []).map(device => `<option value="${safeText(device.id)}">${safeText(device.name)}</option>`).join("") || '<option value="">Mikrofon nebyl nalezen</option>';
      const outputOptions = outputs.map(device => `<option value="${safeText(device.id)}">${safeText(device.name)}</option>`).join("");
      content.innerHTML = `<form id="settings-form" class="settings-form"><label>Výchozí model<select name="default_model"><option value="qwen3.5:4b">Qwen 3.5 4B</option><option value="qwen3.5:9b">Qwen 3.5 9B</option><option value="qwen2.5-coder:7b">Qwen 2.5 Coder 7B</option></select></label><label>Vstupní mikrofon<select name="input_device">${inputOptions}</select></label><label>Výstup hlasu<select name="audio_output">${outputOptions}</select></label><label><input type="checkbox" name="voice_output"> Hlasový výstup</label><label><input type="checkbox" name="continuous_transcription"> Trvalý hlasový režim bez „Hey Jarvis“</label><label><input type="checkbox" name="wake_word"> Wake-word „Hey Jarvis“</label><label><input type="checkbox" name="start_with_windows"> Spustit JARVIS po přihlášení do Windows</label><label><input type="checkbox" name="borderless_window"> Okno bez rámečku prohlížeče</label><label>Internet<select name="internet_mode"><option value="on_request">Pouze na pokyn</option><option value="always_online">Stále online</option><option value="offline">Pouze offline</option></select></label><label><input type="checkbox" name="project_start_required"> Projekty až po START</label><dl><dt>Vstup řeči</dt><dd>LOKÁLNÍ · REAGUJE PO 0,55 S TICHA</dd><dt>Cloudové API</dt><dd>VYPNUTO</dd><dt>Open source</dt><dd>ANO</dd><dt>Úložiště</dt><dd>A:\projekty\OpenJarvis</dd></dl><button class="primary-side" type="submit">ULOŽIT NASTAVENÍ</button></form>`;
      const formSettings = document.getElementById("settings-form");
      const providerOptions = (providerData.providers || []).map(provider => `<option value="${safeText(provider.id)}">${safeText(provider.label)}${provider.configured ? "" : " · VYŽADUJE KLÍČ"}</option>`).join("");
      formSettings.insertAdjacentHTML("afterbegin", `<label>Režim AI<select name="ai_provider">${providerOptions}</select></label><section id="cloud-key-box" class="cloud-key-box" hidden><p id="cloud-key-help"></p><label>API klíč<input id="cloud-api-key" type="password" autocomplete="off" spellcheck="false" placeholder="Vložit klíč pro vybraný režim"></label><button id="save-cloud-key" class="primary-side" type="button">ULOŽIT A OTESTOVAT KLÍČ</button></section>`);
      formSettings.default_model.value = jarvisSettings.default_model || modelRouter.default;
      formSettings.voice_output.checked = jarvisSettings.voice_output !== false;
      formSettings.wake_word.checked = jarvisSettings.wake_word !== false;
      formSettings.continuous_transcription.checked = voiceConfig.continuous_transcription === true;
      formSettings.start_with_windows.checked = jarvisSettings.start_with_windows === true;
      formSettings.borderless_window.checked = jarvisSettings.borderless_window !== false;
      formSettings.internet_mode.value = jarvisSettings.internet_mode || "on_request";
      formSettings.project_start_required.checked = jarvisSettings.project_start_required !== false;
      formSettings.ai_provider.value = jarvisSettings.ai_provider || "local";
      const selectedInput = (audio.inputs || []).find(device => device.name === audio.selected_input);
      formSettings.input_device.value = selectedInput?.id || formSettings.input_device.options[0]?.value || "";
      formSettings.audio_output.value = localStorage.getItem("jarvis-audio-output") || "default";
      const refreshProviderKey = () => {
        const selected = (providerData.providers || []).find(provider => provider.id === formSettings.ai_provider.value);
        const remoteProvider = selected && selected.id !== "local";
        const keyInput = document.getElementById("cloud-api-key");
        document.getElementById("cloud-key-box").hidden = !remoteProvider;
        keyInput.value = "";
        keyInput.placeholder = selected ? `Klíč pouze pro ${selected.label}` : "Vložit klíč pro vybraný režim";
        document.getElementById("cloud-key-help").textContent = remoteProvider
          ? `${selected.label}: ${selected.configured ? "klíč je uložen pro tento režim. Vložte nový pouze pro změnu." : "vložte vlastní klíč a uložte jej před použitím."} Ukládá se šifrovaně pouze pro tento účet Windows.`
          : "";
      };
      formSettings.ai_provider.addEventListener("change", refreshProviderKey);
      document.getElementById("save-cloud-key").addEventListener("click", async () => {
        const provider = formSettings.ai_provider.value;
        const selected = (providerData.providers || []).find(item => item.id === provider);
        const keyInput = document.getElementById("cloud-api-key");
        const apiKey = keyInput.value.trim();
        if (!selected || provider === "local") return;
        if (!apiKey) {
          keyInput.focus();
          addMessage(`Vložte API klíč pro ${selected.label}.`, "jarvis");
          return;
        }
        const button = document.getElementById("save-cloud-key");
        button.disabled = true;
        try {
          await controlPost("/providers/key", { provider, api_key: apiKey, test: true });
          providerData = await controlGet("/providers");
          keyInput.value = "";
          refreshProviderKey();
          addActivity(`KLÍČ ${provider.toUpperCase()} ULOŽEN A OVĚŘEN`);
          addMessage(`${selected.label}: API klíč byl úspěšně uložen a ověřen.`, "jarvis");
        } catch (error) {
          addMessage(`${selected.label}: klíč nebyl uložen. ${error.message}`, "jarvis");
          keyInput.focus();
        } finally {
          button.disabled = false;
        }
      });
      refreshProviderKey();
      formSettings.addEventListener("submit", async event => {
        event.preventDefault();
        const provider = formSettings.ai_provider.value;
        const selected = (providerData.providers || []).find(item => item.id === provider);
        if (provider !== "local" && !selected?.configured) {
          const apiKey = document.getElementById("cloud-api-key").value.trim();
          if (!apiKey) {
            document.getElementById("cloud-api-key").focus();
            addMessage("Pro vybraný online režim nejdříve vložte API klíč.", "jarvis");
            return;
          }
          await controlPost("/providers/key", { provider, api_key: apiKey, test: true });
        }
        jarvisSettings = await controlPost("/settings", {
          default_model: formSettings.default_model.value, voice_output: formSettings.voice_output.checked,
          wake_word: formSettings.wake_word.checked, start_with_windows: formSettings.start_with_windows.checked,
          borderless_window: formSettings.borderless_window.checked, internet_mode: formSettings.internet_mode.value,
          project_start_required: formSettings.project_start_required.checked, ai_provider: provider
        });
        if (formSettings.input_device.value) await controlPost("/audio/input", { input_device: formSettings.input_device.value });
        await controlPost("/voice/config", { continuous_transcription: formSettings.continuous_transcription.checked });
        localStorage.setItem("jarvis-audio-output", formSettings.audio_output.value || "default");
        modelRouter.default = jarvisSettings.default_model;
        muted = !jarvisSettings.voice_output;
        muteButton.setAttribute("aria-pressed", String(muted));
        muteButton.textContent = muted ? "🔇 HLAS ZTIŠEN" : "🔊 HLAS ZAPNUT";
        addActivity(`NASTAVENÍ ULOŽENO · REŽIM ${provider.toUpperCase()}`);
      });
    }
  } catch (error) { content.innerHTML = `<p class="empty-state">${safeText(error.message)}</p>`; }
}

async function loadProjectMemory() {
  try {
    projectMemory = await controlGet("/project-memory");
  } catch (_) {
    projectMemory = { project: "OpenJarvis Beta", summary: "", entries: [] };
  }
}

async function saveProjectMemory(type, title, summary, source = "jarvis") {
  projectMemory = await controlPost("/project-memory", { type, title, summary, source });
}

function projectMemoryPrompt() {
  const entries = (projectMemory.entries || []).slice(-12).map(entry =>
    `- [${entry.type || "insight"}] ${entry.title}: ${entry.summary}`
  );
  return [
    `Projektová paměť: ${projectMemory.summary || "Lokální JARVIS pro Windows."}`,
    entries.length ? `Ověřené poznatky:\n${entries.join("\n")}` : "",
    "Tyto poznatky používej při návrhu zlepšení. Nic neinstaluj, nemaž, neměň systém ani nepublikuj bez potvrzení uživatele.",
    "GitHub: commit a push jsou povolené pouze po přesném příkazu 'nahraj na github'.",
    "Když zjistíš trvalý ověřený poznatek, zakonči odpověď přesně značkou [PROJECT_LOG: krátký název | stručné shrnutí]. Značka se uloží lokálně a uživateli se nezobrazí. Nikdy do ní nevkládej příkazy, hesla, tokeny ani osobní údaje."
  ].filter(Boolean).join("\n\n");
}

async function captureProjectMemory(answer) {
  const match = answer.match(/\[PROJECT_LOG:\s*([^|\]]{3,120})\|\s*([^\]]{5,900})\]/i);
  if (!match) return answer;
  try {
    await saveProjectMemory("insight", match[1].trim(), match[2].trim());
    addActivity("POZNATEK PROJEKTU ULOŽEN");
  } catch (_) {
    addActivity("POZNATEK PROJEKTU NELZE ULOŽIT");
  }
  return answer.replace(match[0], "").trim();
}

function openAgentsSidebar() {
  closeSidebar();
  document.body.classList.add("agents-sidebar-open");
  document.getElementById("agents-sidebar")?.setAttribute("aria-hidden", "false");
  document.getElementById("agents-sidebar-toggle")?.setAttribute("aria-expanded", "true");
  renderAgentsSidebar();
}

function closeAgentsSidebar() {
  document.body.classList.remove("agents-sidebar-open");
  document.getElementById("agents-sidebar")?.setAttribute("aria-hidden", "true");
  document.getElementById("agents-sidebar-toggle")?.setAttribute("aria-expanded", "false");
}

async function renderAgentsSidebar() {
  const content = document.getElementById("agents-sidebar-content");
  if (!content) return;
  try {
    agentRegistry = await controlGet("/agents");
  } catch (_) {
    // Při restartu lokální služby zůstane panel použitelný s koordinátorem.
  }
  const agents = agentRegistry.agents || [];
  content.innerHTML = agents.map(agent => `<article class="agent-card ${agent.id === agentRegistry.active_agent_id ? "active" : ""}"><button class="agent-select" type="button" data-agent-select="${safeText(agent.id)}" ${agent.status === "planned" ? "disabled" : ""}><b>${safeText(agent.name)}</b><small>${safeText(agent.role || "Agent")}</small><span>${agent.status === "planned" ? "ČEKÁ NA PŘÍPRAVU" : agent.status === "paused" ? "POZASTAVEN" : "PŘIPRAVEN"}</span></button><button class="agent-toggle" type="button" data-agent-toggle="${safeText(agent.id)}" ${agent.id === "jarvis" || agent.status === "planned" ? "disabled" : ""}>${agent.id === "jarvis" ? "HLAVNÍ" : agent.status === "planned" ? "ČEKÁ" : agent.status === "paused" ? "SPUSTIT" : "PAUZA"}</button></article>`).join("") || '<p class="empty-state">Žádný agent není připraven.</p>';
  content.querySelectorAll("[data-agent-select]").forEach(button => button.addEventListener("click", async () => {
    try { agentRegistry = await controlPost("/agents/activate", { agent_id: button.dataset.agentSelect }); } catch (_) { agentRegistry.active_agent_id = button.dataset.agentSelect; }
    renderAgentsSidebar();
  }));
  content.querySelectorAll("[data-agent-toggle]").forEach(button => button.addEventListener("click", async () => {
    if (button.dataset.agentToggle === "jarvis") return;
    try { agentRegistry = await controlPost("/agents/toggle", { agent_id: button.dataset.agentToggle }); } catch (_) {
      const agent = agents.find(item => item.id === button.dataset.agentToggle);
      if (agent) agent.status = agent.status === "paused" ? "ready" : "paused";
    }
    renderAgentsSidebar();
  }));
}

async function renderAgentCatalog() {
  const content = document.getElementById("sidebar-content");
  if (!content) return;
  try {
    const catalog = (await controlGet("/agents/catalog")).agents || [];
    content.innerHTML = `<button id="back-to-agents" class="primary-side" type="button">← ZPĚT NA AGENTY</button><p class="agent-note">Instalace vždy ukáže zdroj, licenci a oprávnění. Placené položky nelze koupit automaticky.</p><div class="agent-list">${catalog.map(agent => `<article class="agent-card catalog"><div><b>${safeText(agent.name)}</b><small>${safeText(agent.role)}</small><span>${safeText(agent.price || "Zdarma")} · ${safeText(agent.license || "Bez licence")}</span><small>ZDROJ: ${safeText(agent.source || "Neuveden")}</small><small>OPRÁVNĚNÍ: ${safeText((agent.permissions || []).join(", ") || "Žádná")}</small></div><button class="agent-install" data-agent-install="${safeText(agent.id)}" type="button">${agent.price_type === "paid" ? "ZOBRAZIT CENU" : "INSTALOVAT"}</button></article>`).join("")}</div>`;
    document.getElementById("back-to-agents")?.addEventListener("click", () => renderSidebar());
    content.querySelectorAll("[data-agent-install]").forEach(button => button.addEventListener("click", async () => {
      const selected = catalog.find(agent => agent.id === button.dataset.agentInstall);
      if (!selected) return;
      if (selected.price_type === "paid") {
        window.alert(`${selected.name} je placený agent. Cena: ${selected.price || "neuvedena"}. Dodavatel: ${selected.vendor || selected.source || "neuveden"}. Nákup a platbu musíte dokončit ručně.`);
        return;
      }
      if (!window.confirm(`Nainstalovat bezplatného agenta ${selected.name}?\nZdroj: ${selected.source}\nOprávnění: ${(selected.permissions || []).join(", ")}`)) return;
      agentRegistry = await controlPost("/agents/install", { catalog_id: selected.id, confirmed: true });
      addActivity(`NAINSTALOVÁN AGENT: ${selected.name.toUpperCase()}`);
      renderSidebar();
    }));
  } catch (error) {
    content.innerHTML = `<p class="empty-state">${safeText(error.message)}</p>`;
  }
}

function installSidebar() {
  if (document.getElementById("sidebar")) return;
  document.querySelector("header")?.insertAdjacentHTML("beforeend", '<button id="agents-sidebar-toggle" class="agents-sidebar-toggle view-button" type="button" aria-label="Otevřít levý panel agentů" aria-expanded="false">AGENTI</button><button id="sidebar-toggle" class="sidebar-toggle view-button" type="button" aria-label="Otevřít pravý panel JARVISu" aria-expanded="false">PANEL</button><button id="workbench-toggle" class="workbench-toggle view-button" type="button" aria-label="Otevřít pracovní panel" aria-expanded="false">PRACOVNA</button>');
  document.body.insertAdjacentHTML("beforeend", '<aside id="agents-sidebar" class="agents-sidebar" aria-label="Panel agentů JARVISu" aria-hidden="true"><div class="sidebar-head"><b>AGENTI</b><button id="agents-sidebar-close" type="button" aria-label="Zavřít panel agentů">×</button></div><section id="agents-sidebar-content" class="sidebar-content"></section><div class="sidebar-foot">PŘEPNUTÍ AGENTA · PAUZA</div></aside><button id="agents-sidebar-backdrop" class="agents-sidebar-backdrop" type="button" aria-label="Zavřít překrytí agentů"></button><aside id="sidebar" class="sidebar" aria-label="Navigační panel JARVISu" aria-hidden="true"><div class="sidebar-head"><b>J.A.R.V.I.S.</b><button id="sidebar-close" type="button" aria-label="Zavřít panel">×</button></div><button id="new-chat" class="new-chat" type="button">＋ NOVÝ CHAT</button><nav class="sidebar-nav" aria-label="Sekce panelu"><button data-section="recent">◷ NEDÁVNÉ CHATY</button><button data-section="projects">▣ PROJEKTY</button><button data-section="rules">⌁ PRAVIDLA</button><button data-section="settings">⚙ NASTAVENÍ</button></nav><section id="sidebar-content" class="sidebar-content"></section><div id="sidebar-provider-state" class="sidebar-foot">OVĚŘUJI REŽIM AI…</div></aside><button id="sidebar-backdrop" class="sidebar-backdrop" type="button" aria-label="Zavřít překrytí panelu"></button>');
  document.body.insertAdjacentHTML("beforeend", '<aside id="workbench" class="workbench" aria-label="Pracovna JARVISu" aria-hidden="true"><div id="workbench-resizer" class="workbench-resizer" role="separator" aria-orientation="vertical" aria-label="Změnit šířku pracovny"></div><div class="sidebar-head"><b>PRACOVNA</b><button id="workbench-close" type="button" aria-label="Zavřít Pracovnu">×</button></div><nav class="workbench-tabs" aria-label="Sekce Pracovny"><button data-workbench-tab="live" type="button">ŽIVĚ</button><button data-workbench-tab="files" type="button">SOUBORY</button><button data-workbench-tab="browser" type="button">PROHLÍŽEČ</button></nav><section id="workbench-content" class="workbench-content"></section></aside>');
  document.getElementById("sidebar-toggle").addEventListener("click", () => openSidebar());
  document.getElementById("agents-sidebar-toggle").addEventListener("click", () => openAgentsSidebar());
  document.getElementById("workbench-toggle").addEventListener("click", () => openWorkbench());
  document.getElementById("sidebar-close").addEventListener("click", closeSidebar);
  document.getElementById("agents-sidebar-close").addEventListener("click", closeAgentsSidebar);
  document.getElementById("workbench-close").addEventListener("click", closeWorkbench);
  document.getElementById("sidebar-backdrop").addEventListener("click", closeSidebar);
  document.getElementById("agents-sidebar-backdrop").addEventListener("click", closeAgentsSidebar);
  document.querySelectorAll(".sidebar-nav button").forEach(button => button.addEventListener("click", () => openSidebar(button.dataset.section)));
  document.querySelectorAll("[data-workbench-tab]").forEach(button => button.addEventListener("click", () => { workbenchTab = button.dataset.workbenchTab; renderWorkbench(); }));
  bindWorkbenchResize();
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
  return item;
}

function addActivity(text) {
  const item = document.createElement("p");
  item.textContent = text;
  agentActivity.prepend(item);
  workbenchActivity = [{ time: new Date().toLocaleTimeString("cs-CZ"), text }, ...workbenchActivity].slice(0, 80);
  if (document.body.classList.contains("workbench-open") && workbenchTab === "live") renderWorkbench();
}

function setActivity(text, state = "") {
  activity.textContent = text;
  reactor.className = `reactor ${state}`;
}

function speak(text) {
  if (muted || !text) return;
  voiceQueue.push({ audioUrl: "", fallbackText: text });
  playQueuedVoice();
}

function stopPlayback() {
  voicePlaybackEpoch += 1;
  window.speechSynthesis?.cancel();
  voiceQueue = [];
  voiceUtterance = null;
  if (voiceAudio) { voiceAudio.pause(); voiceAudio.currentTime = 0; voiceAudio = null; }
}

function stopCurrentRun() {
  stopPlayback();
  activeRequest?.abort();
  activeRequest = null;
  stopButton.classList.remove("active");
  controlPost("/voice/stop", {}).then(state => {
    wakeToggle.setAttribute("aria-pressed", String(state.enabled));
    wakeToggle.textContent = "„HEY JARVIS“ AKTIVNÍ";
    voiceState.textContent = "MIKROFON: LOKÁLNÍ WAKE-WORD";
  }).catch(() => {});
  setActivity("TAH ZASTAVEN");
  addActivity("PŘERUŠENO UŽIVATELEM");
}

function playVoiceAudio(audioUrl, fallbackText) {
  voiceQueue.push({ audioUrl, fallbackText });
  playQueuedVoice();
}

function playQueuedVoice() {
  if (voiceAudio || voiceUtterance || !voiceQueue.length) return;
  const { audioUrl, fallbackText } = voiceQueue.shift();
  const epoch = voicePlaybackEpoch;
  const finish = () => {
    if (epoch !== voicePlaybackEpoch) return;
    playQueuedVoice();
  };
  const speakFallback = () => {
    if (!fallbackText || muted || !("speechSynthesis" in window)) {
      finish();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(fallbackText);
    voiceUtterance = utterance;
    utterance.lang = "cs-CZ";
    utterance.rate = 1.03;
    let completed = false;
    const complete = () => {
      if (completed) return;
      completed = true;
      if (voiceUtterance === utterance) voiceUtterance = null;
      finish();
    };
    utterance.addEventListener("end", complete, { once: true });
    utterance.addEventListener("error", complete, { once: true });
    window.speechSynthesis.speak(utterance);
  };
  if (!audioUrl) {
    speakFallback();
    return;
  }
  const audio = new Audio(`${audioUrl}?time=${Date.now()}`);
  voiceAudio = audio;
  let completed = false;
  const completeAudio = () => {
    if (completed) return;
    completed = true;
    if (voiceAudio === audio) voiceAudio = null;
    finish();
  };
  const failAudio = () => {
    if (completed) return;
    completed = true;
    if (voiceAudio === audio) voiceAudio = null;
    speakFallback();
  };
  audio.addEventListener("ended", completeAudio, { once: true });
  audio.addEventListener("error", failAudio, { once: true });
  const outputId = localStorage.getItem("jarvis-audio-output") || "default";
  const selectOutput = typeof audio.setSinkId === "function" && outputId !== "default"
    ? audio.setSinkId(outputId).catch(() => {})
    : Promise.resolve();
  selectOutput.then(() => audio.play()).catch(failAudio);
}

async function audioOutputDevices() {
  const fallback = [{ id: "default", name: "Výchozí zařízení Windows" }];
  if (!navigator.mediaDevices?.enumerateDevices) return fallback;
  try {
    const allowed = /reproduktory|speakers|sluchátka|headphones|headset|hands-free/i;
    const blocked = /mapper|steam|stereo|line|kabel|cable|primární|primary|virtual/i;
    const outputs = (await navigator.mediaDevices.enumerateDevices())
      .filter(device => device.kind === "audiooutput" && allowed.test(device.label) && !blocked.test(device.label))
      .map((device, index) => ({ id: device.deviceId, name: device.label || `Zvukový výstup ${index + 1}` }));
    return [...fallback, ...outputs.filter(device => device.id !== "default")];
  } catch (_) { return fallback; }
}

async function getActiveAgent() {
  try {
    agentRegistry = await controlGet("/agents");
    return agentRegistry.agents.find(agent => agent.id === agentRegistry.active_agent_id) || agentRegistry.agents[0] || null;
  } catch (_) {
    return null;
  }
}

function agentSystemPrompt(agent) {
  return [
    agent ? `Aktivní agent: ${agent.name}.` : "Aktivní agent: JARVIS.",
    agent?.role ? `Role: ${agent.role}.` : "",
    agent?.rules?.length ? `Pravidla agenta:\n${agent.rules.map(rule => `- ${rule}`).join("\n")}` : "",
    persistentRules.length ? `Trvalá uživatelská pravidla:\n${persistentRules.map(rule => `- ${rule}`).join("\n")}` : "",
    projectMemoryPrompt(),
  ].filter(Boolean).join("\n\n");
}

function selectSpecialistAgents(command, agents) {
  const request = command.toLowerCase();
  const has = pattern => pattern.test(request);
  const byId = id => agents.find(agent => agent.id === id && agent.status === "ready");
  if (has(/\b(web|internet|vyhledej|vyhledat|porovnej|výzkum|vyzkum|aktuální|aktualni|zdroj)\b/)) return [byId("research")].filter(Boolean);
  if (has(/\b(soubor|složk|slozk|adresář|adresar|přejmenuj|prejmenuj|tř[ií]d|trid)\b/)) return [byId("files")].filter(Boolean);
  if (has(/\b(powershell|automatiz|plánovač|planovac|spusť program|spust program|skript)\b/)) return [byId("automation")].filter(Boolean);
  if (has(/\b(kód|kod|program|skript|debug|chyba|html|css|javascript|python|api|plugin|refaktor|test)\b/)) return [byId("developer")].filter(Boolean);
  return [];
}

async function dispatchToAgents(command) {
  const match = command.match(/^(?:agenti|všichni agenti|vsichni agenti)\s*[:,-]\s*(.+)$/i);
  agentRegistry = await controlGet("/agents");
  const readyAgents = (agentRegistry.agents || []).filter(agent => agent.status === "ready");
  const agents = match ? readyAgents : selectSpecialistAgents(command, readyAgents);
  if (!match && !agents.length) return false;
  const task = (match ? match[1] : command).trim();
  if (!agents.length) {
    addMessage(command, "user");
    addMessage("Pro souběžnou práci není připraven žádný agent.", "jarvis");
    return true;
  }
  if (match && !window.confirm(`Spustit úkol paralelně pro ${agents.length} agentů?\n${task}`)) return true;
  addMessage(command, "user");
  const assignment = await controlPost("/agents/tasks", { task, agent_ids: agents.map(agent => agent.id) });
  addActivity(match ? `PARALELNÍ ÚKOL: ${agents.length} AGENTŮ` : `JARVIS PŘEDAL ÚKOL: ${agents[0].name.toUpperCase()}`);
  await Promise.all(assignment.agents.map(async agent => {
    try {
      const data = agent.engine === "openclaw"
        ? await controlPost("/agents/openclaw/run", { task })
        : await controlPost("/chat", {
          model: agent.model || modelRouter.default,
          messages: [{ role: "system", content: agentSystemPrompt(agent) }, { role: "user", content: task }],
        });
      const answer = await captureProjectMemory(data.answer || "Agent nevrátil odpověď.");
      addMessage(`[${agent.name}]\n${answer}`, "jarvis");
      await controlPost("/agents/tasks/complete", { task_id: assignment.task_id, agent_id: agent.id, success: true, summary: answer });
    } catch (error) {
      addMessage(`[${agent.name}] Úkol selhal: ${error.message}`, "jarvis");
      await controlPost("/agents/tasks/complete", { task_id: assignment.task_id, agent_id: agent.id, success: false, summary: error.message });
    }
  }));
  agentRegistry = await controlGet("/agents");
  addActivity(match ? "PARALELNÍ ÚKOL DOKONČEN" : "ÚKOL SPECIALISTY DOKONČEN");
  return true;
}

async function installAgentFromCommand(command) {
  const match = command.match(/^(?:jarvisi,?\s*)?(?:nainstaluj|instaluj)\s+(?:mi\s+)?(?:bezplatn(?:ého|y|ou)\s+)?agenta?\s*(?:pro|na)?\s*(.*)$/i);
  if (!match) return false;
  addMessage(command, "user");
  const query = match[1].trim().toLowerCase();
  const catalog = (await controlGet("/agents/catalog")).agents || [];
  const selected = catalog.find(agent => `${agent.name} ${agent.role} ${agent.id}`.toLowerCase().includes(query)) || catalog.find(agent => agent.price_type === "free");
  if (!selected) {
    addMessage("V lokálním katalogu není vhodný agent.", "jarvis");
    return true;
  }
  if (selected.price_type === "paid") {
    addMessage(`${selected.name} je placený agent. Cena: ${selected.price || "neuvedena"}. Nákup musíte ručně potvrdit a dokončit u dodavatele.`, "jarvis");
    return true;
  }
  if (!window.confirm(`Nainstalovat bezplatného agenta ${selected.name}?\nZdroj: ${selected.source}\nOprávnění: ${(selected.permissions || []).join(", ")}`)) return true;
  agentRegistry = await controlPost("/agents/install", { catalog_id: selected.id, confirmed: true });
  addMessage(`Agent ${selected.name} je nainstalovaný a připravený v levém panelu Agentů.`, "jarvis");
  addActivity(`NAINSTALOVÁN AGENT: ${selected.name.toUpperCase()}`);
  return true;
}

async function sendCommand(text) {
  const command = text.trim();
  if (!command) return;
  await loadProjectMemory();
  const powershellRequest = command.match(/^(?:(admin)\s+)?(?:powershell|pwsh)\s*:\s*([\s\S]+)$/i);
  if (powershellRequest) {
    const elevated = Boolean(powershellRequest[1]);
    const script = powershellRequest[2].trim();
    const prompt = elevated
      ? "Spustit tento PowerShell jako správce? Windows zobrazí UAC potvrzení."
      : "Spustit tento lokální PowerShell příkaz?";
    if (!window.confirm(`${prompt}\n\n${script}`)) return;
    addMessage(command, "user");
    input.value = "";
    setActivity(elevated ? "ČEKÁM NA UAC" : "SPOUŠTÍM POWERSHELL", "thinking");
    try {
      const result = await controlPost("/powershell/execute", {
        command: script,
        elevated,
        confirmed: true,
      });
      const mode = elevated ? "ADMIN POWERSHELL" : "POWERSHELL";
      const output = result.output || "Příkaz byl dokončen bez textového výstupu.";
      addMessage(`${mode} · KÓD ${result.exit_code}\n${output}`, "jarvis");
      addActivity(`${mode} DOKONČEN`);
      setActivity("Připraven naslouchat");
    } catch (error) {
      addMessage(`PowerShell selhal: ${error.message}`, "jarvis");
      setActivity("CHYBA POWERSHELLU");
    }
    return;
  }
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
  const projectNote = command.match(/^(?:zapiš|zapis) (?:do )?(?:projektové paměti|projektove pameti)\s*[:,-]?\s*(.{5,900})$/i);
  if (projectNote) {
    try {
      await saveProjectMemory("user_preference", "Poznatek uživatele", projectNote[1].trim(), "user");
      addMessage(command, "user");
      addMessage("Poznatek byl uložen do lokální projektové paměti.", "jarvis");
      addActivity("PROJEKTOVÝ POZNATEK ULOŽEN");
    } catch (error) {
      addMessage(`Poznatek nelze uložit: ${error.message}`, "jarvis");
    }
    input.value = "";
    return;
  }
  try {
    if (await dispatchToAgents(command)) {
      input.value = "";
      return;
    }
  } catch (error) {
    addMessage(`Souběžný úkol nelze spustit: ${error.message}`, "jarvis");
    input.value = "";
    return;
  }
  try {
    if (await installAgentFromCommand(command)) {
      input.value = "";
      return;
    }
  } catch (error) {
    addMessage(`Agenta nelze nainstalovat: ${error.message}`, "jarvis");
    input.value = "";
    return;
  }
  const activeAgent = await getActiveAgent();
  const selectedModel = selectModel(command);
  addMessage(command, "user");
  conversation.push({ role: "user", content: command });
  saveConversation();
  setActivity("Zpracovávám požadavek", "thinking");
  input.value = "";
  activeRequest = new AbortController();
  stopButton.classList.add("active");
  updateProviderIndicators();
  const requestedMode = configuredProvider === "automatic"
    ? "AUTOMATICKY · PRVNÍ GEMINI FREE"
    : providerLabels[configuredProvider] || configuredProvider.toUpperCase();
  addActivity(`AGENT ${activeAgent?.name || "JARVIS"} · ${requestedMode}: ${command.slice(0, 42)}`);
  try {
    const chatMessages = [{ role: "system", content: agentSystemPrompt(activeAgent) }, ...conversation];
    let rawAnswer = "";
    const data = await controlPost("/chat", { model: activeAgent?.model || selectedModel, messages: chatMessages });
    activeProvider = data.provider || configuredProvider;
    lastProviderAt = new Date().toISOString();
    updateProviderIndicators();
    const fallbacks = Array.isArray(data.fallbacks) ? data.fallbacks : [];
    if (fallbacks.length) {
      const changes = fallbacks.map(item => `${providerLabels[item.provider] || item.provider}: ${item.reason || "nedostupné"}`).join(" | ");
      addMessage(`Systém: online zdroj není dostupný nebo nemá volnou kvótu. ${changes}. Pokračuji přes ${providerLabels[activeProvider] || activeProvider}.`, "jarvis");
      addActivity(`PŘEPNUTO NA ${providerLabels[activeProvider] || activeProvider}`);
    } else {
      addActivity(`ODPOVĚĎ: ${providerLabels[activeProvider] || activeProvider}`);
    }
    rawAnswer = data.answer || "Model nevrátil odpověď.";
    const answer = await captureProjectMemory(rawAnswer);
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

reactor.addEventListener("click", () => {
  controlPost("/voice/listen", {}).then(() => {
    wakeToggle.setAttribute("aria-pressed", "true");
    wakeToggle.textContent = "„HEY JARVIS“ AKTIVNÍ";
    voiceState.textContent = "MIKROFON: LOKÁLNÍ WAKE-WORD";
    voiceMeter.style.width = "8%";
    setActivity("NASLOUCHÁM RUČNÍMU PŘÍKAZU", "listening");
    addActivity("RUČNÍ HLASOVÝ VSTUP AKTIVOVÁN");
  }).catch(error => addMessage(`Hlasový modul: ${error.message}`, "jarvis"));
});
form.addEventListener("submit", event => { event.preventDefault(); sendCommand(input.value); });
wakeToggle.addEventListener("click", () => {
  const enabled = wakeToggle.getAttribute("aria-pressed") !== "true";
  controlPost("/voice/state", { enabled }).then(state => {
    wakeToggle.setAttribute("aria-pressed", String(state.enabled));
    wakeToggle.textContent = state.enabled ? "„HEY JARVIS“ AKTIVNÍ" : "„HEY JARVIS“ NEAKTIVNÍ";
    voiceState.textContent = state.enabled ? "MIKROFON: LOKÁLNÍ WAKE-WORD" : "MIKROFON: VYPNUT";
    voiceMeter.style.width = state.enabled ? "8%" : "0%";
    if (!state.enabled) stopPlayback();
  }).catch(error => addMessage(`Hlasový přepínač: ${error.message}`, "jarvis"));
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
  if (event.key === "F8") {
    event.preventDefault();
    stopCurrentRun();
  }
  if (event.key === "F9") {
    event.preventDefault();
    reactor.click();
  }
});
async function refreshHealth() {
  try {
    const settings = await controlGet("/settings");
    configuredProvider = settings.ai_provider || "local";
    activeProvider = settings.last_provider || activeProvider;
    lastProviderAt = settings.last_provider_at || lastProviderAt;
    updateProviderIndicators();
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

function updateProviderIndicators() {
  const configuredLabel = providerLabels[configuredProvider] || configuredProvider.toUpperCase();
  const effectiveProvider = activeProvider || (configuredProvider === "automatic" ? "gemini_free" : configuredProvider);
  const effectiveLabel = providerLabels[effectiveProvider] || effectiveProvider.toUpperCase();
  const model = providerModels[effectiveProvider] || "OVĚŘUJI…";
  const online = effectiveProvider === "gemini_free" || effectiveProvider === "openrouter_free" || configuredProvider === "automatic";
  const isProcessing = Boolean(activeRequest);
  const modeText = configuredProvider === "automatic"
    ? isProcessing
      ? `AUTOMATICKY · ZKOUŠÍ ${effectiveLabel}`
      : activeProvider
        ? `AUTOMATICKY · POSLEDNÍ ${effectiveLabel}`
        : "AUTOMATICKY · PŘIPRAVEN GEMINI FREE"
    : configuredLabel;
  const credentialsText = online ? "KLÍČ OVĚŘEN" : "NEPOUŽÍVÁ SE";
  document.getElementById("active-model")?.replaceChildren(document.createTextNode(model));
  document.getElementById("provider-mode")?.replaceChildren(document.createTextNode(modeText));
  document.getElementById("api-credentials")?.replaceChildren(document.createTextNode(credentialsText));
  const sidebarState = document.getElementById("sidebar-provider-state");
  if (sidebarState) {
    sidebarState.textContent = configuredProvider === "automatic"
      ? isProcessing
        ? `AUTOMATICKY · ZKOUŠÍ ${effectiveLabel}`
        : activeProvider
          ? `AUTOMATICKY · POSLEDNÍ ODPOVĚĎ ${effectiveLabel}`
          : "AUTOMATICKY · PŘIPRAVEN GEMINI FREE"
      : `REŽIM: ${effectiveLabel}`;
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
      voiceMeter.style.width = "8%";
      addActivity("OPENWAKEWORD PŘIPRAVEN");
    } else if (event.type === "voice_mode") {
      voiceState.textContent = event.provider === "gemini_live" ? "MIKROFON: GEMINI LIVE" : "MIKROFON: LOKÁLNÍ ZÁLOHA";
      addActivity(event.text);
    } else if (event.type === "wake_detected") {
      stopPlayback();
      activeRequest?.abort();
      setActivity("HEY JARVIS ROZPOZNÁN", "listening");
      addActivity("AKTIVAČNÍ SLOVO ROZPOZNÁNO");
    } else if (event.type === "voice_transcript") {
      input.value = event.text || "";
      setActivity("PŘEPIS HLASU DOKONČEN", "thinking");
      addActivity(`PŘEPIS: ${event.text || "BEZ TEXTU"}`);
    } else if (event.type === "voice_command") {
      activeVoiceMessage = null;
      addMessage(event.text, "user");
      setActivity("ZPRACOVÁVÁM HLASOVÝ PŘÍKAZ", "thinking");
    } else if (event.type === "voice_sentence") {
      if (!activeVoiceMessage) activeVoiceMessage = addMessage("", "jarvis");
      activeVoiceMessage.textContent = `${activeVoiceMessage.textContent}${event.text}`.trim();
      messages.scrollTop = messages.scrollHeight;
      playVoiceAudio(event.audio_url, event.text);
      setActivity("JARVIS MLUVÍ", "listening");
    } else if (event.type === "voice_complete") {
      if (activeVoiceMessage) activeVoiceMessage.textContent = event.text;
      else addMessage(event.text, "jarvis");
      activeVoiceMessage = null;
      setActivity("ČEKÁM NA HEY JARVIS", "listening");
      addActivity("HLASOVÁ ODPOVĚĎ DOKONČENA");
    } else if (event.type === "voice_answer") {
      activeVoiceMessage = null;
      addMessage(event.text, "jarvis");
      playVoiceAudio(event.audio_url, event.text);
      setActivity("ČEKÁM NA HEY JARVIS", "listening");
      addActivity("HLASOVÁ ODPOVĎ DOKONČENA");
    } else if (event.type === "voice_stop") {
      activeVoiceMessage = null;
      stopPlayback();
      activeRequest?.abort();
      setActivity("HLASOVÝ ÚKOL ZASTAVEN");
      addActivity("PŘERUŠENO HLASOVÝM POVELEM");
    } else if (event.type === "error") {
      activeVoiceMessage = null;
      addMessage(event.text, "jarvis");
      setActivity("CHYBA HLASOVÉHO MODULU");
    }
  } catch (_) { /* Hlasový klient se může právě spouštět. */ }
}
async function pollVoiceMeter() {
  try {
    const meter = await controlGet("/voice/meter");
    const level = Number(meter.level || 0);
    const percent = Math.max(4, Math.min(100, Math.round((level - 250) / 8)));
    voiceMeter.style.width = `${percent}%`;
  } catch (_) { /* Hlasový klient se může právě spouštět. */ }
}

setInterval(() => { document.getElementById("clock").textContent = new Date().toLocaleTimeString("cs-CZ"); }, 1000);
setInterval(refreshHealth, 15000);
setInterval(pollVoiceEvents, 500);
setInterval(pollVoiceMeter, 200);
setInterval(refreshHardware, 2000);
installFilmHud();
setTimeout(() => { document.body.className = "booted"; refreshHealth(); refreshHardware(); loadRules(); loadProjectMemory(); }, 90);
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations().then(registrations => {
    registrations.forEach(registration => registration.unregister());
  }).catch(() => {});
}

function setHudView(view) {
  if (view !== "chat") {
    closeSidebar();
    closeAgentsSidebar();
    closeWorkbench();
  }
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
  document.querySelectorAll(".view-button[data-view]").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  addActivity(view === "system" ? "PŘEPNUTO NA SYSTÉMOVÝ POHLED" : "PŘEPNUTO NA KONVERZACI");
}

document.querySelectorAll(".view-button[data-view]").forEach(button => {
  button.addEventListener("click", () => setHudView(button.dataset.view));
});
setHudView("chat");
