from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_runtime import AgentRuntime, AgentTask
import raven_control
from raven_control import RAVEN_SYSTEM_PROMPT, automatic_provider_order, generate_artifact_content, normalize_provider, provider_status
from raven_intelligence import detect_local_file_action, execute_file_action


ROOT = Path(__file__).resolve().parent.parent


def test_version_is_one_zero() -> None:
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() in {"1.0", "v1.0"}


def test_system_prompt_forbids_invented_provider_state() -> None:
    assert "nevkládej vlastní provozní stav" in RAVEN_SYSTEM_PROMPT
    assert "údaje zobrazuje rozhraní Ravenu samo" in RAVEN_SYSTEM_PROMPT


def test_free_provider_catalog_has_required_order_and_no_grok() -> None:
    payload = provider_status()
    ids = [item["id"] for item in payload["providers"]]
    assert payload["free_only"] is True
    order = automatic_provider_order()
    assert order[-1] == "local"
    if "gemini_free" in order and "openrouter_free" in order:
        assert order.index("gemini_free") < order.index("openrouter_free")
    assert "grok" not in " ".join(ids).lower()
    assert "xai" not in " ".join(ids).lower()


@pytest.mark.parametrize("provider", ["grok", "xai", "paid", "unknown"])
def test_forbidden_provider_is_rejected(provider: str) -> None:
    with pytest.raises(ValueError):
        normalize_provider(provider)


@pytest.mark.parametrize("model", ["grok-4", "xai/grok", "provider:paid-model"])
def test_forbidden_model_is_rejected(model: str) -> None:
    with pytest.raises(ValidationError):
        AgentTask(prompt="test", agent_id="tester", model=model)


def test_agent_runtime_never_allows_more_than_two_heavy_agents() -> None:
    assert AgentRuntime(limit=99).limit == 2


def test_hardware_status_contains_live_dashboard_data() -> None:
    payload = json.loads((ROOT / "hud" / "hardware-status.json").read_text(encoding="utf-8"))
    assert {"system_usage", "disks", "processes", "temperatures"}.issubset(payload)
    assert all(0 <= float(disk["used"]) <= 100 for disk in payload["disks"])


def test_frontend_assets_are_local_and_present() -> None:
    html = (ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    assert "https://" not in html
    for relative in ("app.css", "workbench.css", "hud.js", "workbench.js", "vendor/monaco/vs/loader.js"):
        assert (ROOT / "hud" / relative).is_file()


def test_czech_desktop_file_request_has_exact_filename() -> None:
    action = detect_local_file_action("Vytvoř soubor test.txt na ploše")
    assert action is not None
    assert Path(action["path"]).name == "test.txt"
    assert Path(action["path"]).parent.name.lower() in {"desktop", "plocha"}


def test_multiline_fenced_html_content_is_preserved(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    prompt = f'''Vytvoř soubor "{target}" s obsahem:\n```html\n<!doctype html>\n<html lang="cs"><body>Ahoj</body></html>\n```'''
    action = detect_local_file_action(prompt)
    assert action is not None
    assert Path(action["path"]) == target.resolve()
    assert action["content"].startswith("<!doctype html>")
    assert "<body>Ahoj</body>" in action["content"]


def test_html_page_request_is_detected_without_word_file(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    action = detect_local_file_action(f'Vygeneruj HTML stránku "{target}" s uvítací zprávou')
    assert action is not None
    assert action["action"] == "create_text_file"
    assert Path(action["path"]) == target.resolve()


def test_generated_artifact_strips_markdown_fence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(raven_control, "automatic_provider_request", lambda *_: ("gemini_free", "```html\n<!doctype html><html><style>body{color:white}</style><body>Ahoj</body></html>\n```", []))
    provider, content, fallbacks = generate_artifact_content("Vytvoř stránku", str(tmp_path / "index.html"))
    assert provider == "gemini_free"
    assert content.endswith("</html>")
    assert fallbacks == []


def test_generated_html_uses_local_template_for_truncated_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(raven_control, "automatic_provider_request", lambda *_: ("gemini_free", "<!doctype html><html><style>", []))
    provider, content, fallbacks = generate_artifact_content("Vytvoř stránku", str(tmp_path / "index.html"))
    assert provider == "local-template"
    assert content.endswith("</html>")
    assert "Vítejte v Raven AI" in content
    assert fallbacks[0]["provider"] == "gemini_free"


def test_generated_html_rejects_unrequested_controls_and_bad_welcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = "<!doctype html><html><style>body{color:white}</style><body><p>Vítejte v Raven AI - nesmysl.</p><button onclick='x()'>Klik</button></body></html>"
    monkeypatch.setattr(raven_control, "automatic_provider_request", lambda *_: ("gemini_free", bad, []))
    provider, content, fallbacks = generate_artifact_content("Přidej uvítací zprávu Vítejte v Raven AI", str(tmp_path / "index.html"))
    assert provider == "local-template"
    assert "<button" not in content
    assert "<p>Vítejte v Raven AI.</p>" in content
    assert fallbacks


def test_full_access_file_tool_writes_and_verifies(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    result = execute_file_action(
        {"action": "create_text_file", "path": str(target), "content": "Raven test"},
        "full",
    )
    assert result["status"] == "completed"
    assert result["verified"] is True
    assert target.read_text(encoding="utf-8") == "Raven test"


def test_simulation_and_denied_mode_never_write(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    action = {"action": "create_text_file", "path": str(target), "content": "nic"}
    result = execute_file_action(action, "full", simulate=True)
    assert result["status"] == "simulated"
    assert not target.exists()
    with pytest.raises(PermissionError):
        execute_file_action(action, "denied")
    assert not target.exists()


def test_existing_file_is_not_overwritten_by_create(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        execute_file_action({"action": "create_text_file", "path": str(target), "content": "new"}, "full")
    assert target.read_text(encoding="utf-8") == "original"


def test_delete_is_recoverable_and_always_requires_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "delete-me.txt"
    target.write_text("recoverable", encoding="utf-8")
    action = {"action": "delete_file", "path": str(target)}
    pending = execute_file_action(action, "full")
    assert pending["status"] == "confirmation_required"
    result = execute_file_action(action, "full", confirmed=True)
    assert result["verified"] is True
    assert not target.exists()
    recovery = Path(result["recovery_path"])
    assert recovery.read_text(encoding="utf-8") == "recoverable"
    recovery.unlink(missing_ok=True)
