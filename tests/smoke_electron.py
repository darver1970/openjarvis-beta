"""Smoke test the real Electron UI through local CDP."""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "runtime" / "test-results"
RESULTS.mkdir(parents=True, exist_ok=True)


with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
    pages = [page for context in browser.contexts for page in context.pages]
    hud = next(page for page in pages if page.url.startswith("http://127.0.0.1:5174/"))
    errors: list[str] = []
    hud.on("pageerror", lambda error: errors.append(str(error)))
    hud.reload(wait_until="domcontentloaded")
    hud.wait_for_timeout(1200)
    hud.evaluate("document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close())")
    assert hud.title() == "Jarvis 1.0"
    assert hud.locator(".app-menu-brand").text_content().strip() == "Jarvis 1.0"
    assert hud.locator(".app-menu").count() == 3
    assert hud.evaluate("Boolean(window.jarvisDesktop)") is True
    assert hud.locator("#composer-access").input_value() == "full"
    assert hud.locator("#composer-simulate").get_attribute("aria-pressed") in {"true", "false"}
    assert "Groq Free" in hud.locator("#composer-provider").text_content()
    drives = hud.evaluate("window.jarvisDesktop.listFiles({path: '::drives'})")
    assert any(item["path"].upper().startswith("C:") for item in drives["entries"])
    files = hud.evaluate("window.jarvisDesktop.listFiles({path: 'C:\\\\projektjarvis'})")
    assert any(item["name"] == "jarvis_control.py" for item in files["entries"])
    source = hud.evaluate("window.jarvisDesktop.readFile('C:\\\\projektjarvis\\\\jarvis_control.py')")
    assert "JARVIS_SYSTEM_PROMPT" in source["content"]
    version_file = hud.evaluate("window.jarvisDesktop.readFile('C:\\\\projektjarvis\\\\VERSION')")
    assert version_file["content"].strip() in {"1.0", "v1.0"}
    first = hud.evaluate("window.jarvisDesktop.browser.list()")
    assert len(first["tabs"]) >= 1
    second = hud.evaluate("window.jarvisDesktop.browser.create('https://example.com/')")
    assert len(second["tabs"]) >= 2
    active = second["activeTabId"]
    hud.evaluate("id => window.jarvisDesktop.browser.close(id)", active)
    closed = hud.evaluate("window.jarvisDesktop.browser.list()")
    assert len(closed["tabs"]) >= 1
    hud.locator('[data-view="agents"]').first.click()
    hud.wait_for_timeout(400)
    assert hud.get_by_text("Memory Manager", exact=True).count() == 1
    assert hud.get_by_text("Project Indexer", exact=True).count() == 1
    hud.locator('[data-view="web"]').first.click()
    hud.wait_for_timeout(500)
    assert hud.locator(".native-browser-surface").count() >= 1
    hud.locator('[data-view="telemetry"]').first.click()
    hud.wait_for_timeout(2200)
    assert hud.locator(".disk-card").count() >= 1
    assert hud.locator(".disk-bar i").count() >= 1
    assert hud.locator(".telemetry-chart").count() == 5
    hud.locator('[data-view="settings"]').first.click()
    hud.wait_for_timeout(300)
    assert hud.locator("#library-locations").count() == 1
    assert hud.locator("#add-library-folder").count() == 1
    assert hud.locator("#run-diagnostics").count() == 1
    hud.locator('[data-view="chat"]').first.click()
    hud.wait_for_timeout(300)
    hud.evaluate("""async () => {
      await fetch('http://127.0.0.1:8126/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({permission_mode:'full', simulation_mode:true})});
      await fetch('http://127.0.0.1:8126/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({simulate:true, model:'automatic', messages:[{role:'user', content:'Vytvoř soubor jarvis-ui-live-log-test.txt na ploše'}]})});
    }""")
    hud.wait_for_selector(".live-work-log article", timeout=5000)
    hud.wait_for_timeout(500)
    work_log = hud.locator(".live-work-log")
    assert work_log.locator("article").count() >= 5
    assert "Planner" in work_log.text_content() or "planner" in work_log.text_content()
    assert "Files" in work_log.text_content() or "files" in work_log.text_content()
    assert "Úkol dokončen" in work_log.text_content()
    hud.evaluate("""async () => {
      await fetch('http://127.0.0.1:8126/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({permission_mode:'full', simulation_mode:false})});
    }""")
    hud.screenshot(path=str(RESULTS / "jarvis-main.png"), full_page=True)
    summary = hud.evaluate("window.jarvisDesktop.gitSummary()")
    assert summary["count"] > 0
    if errors:
        raise AssertionError(json.dumps(errors, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "tabs": len(closed["tabs"]), "files": len(files["entries"]), "agents": 2, "git_changes": summary["count"], "screenshot": str(RESULTS / "jarvis-main.png")}, ensure_ascii=False))
