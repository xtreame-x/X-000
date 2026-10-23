"""Dashboard settings service and HTTP API.

These tests never touch the repository's config.yaml or .env: both paths are
redirected into tmp_path, and the environment variables they write are restored
afterwards.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from orc import config as orc_config
from orc import settings, ui_server

SAMPLE_CONFIG = """\
# Oryn test configuration
ai:
  # Primary provider: gemini | groq
  provider: gemini
  model: gemini-3.5-flash-lite
  max_tokens: 1500
  temperature: 0.7
  fallback:
    - provider: groq
      model: openai/gpt-oss-120b

memory:
  stm_word_limit: 80
  compress_trigger: 60

voice:
  stt_enabled: true
  tts_enabled: true
  whisper_model: tiny
  tts_rate: 175
  tts_voice: 0
  listen_duration: 5
"""

MANAGED_ENV = (
    "GEMINI_API_KEY",
    "NOTION_TOKEN",
    "GOOGLE_ACCESS_TOKEN",
    "GOOGLE_REFRESH_TOKEN",
    "AGENT_SMTP_USER",
)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(SAMPLE_CONFIG, encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# credentials\n"
        "GEMINI_API_KEY=gemini-secret-key-1234\n"
        "GOOGLE_REFRESH_TOKEN=1//refresh-token\n"
        "GOOGLE_ACCESS_TOKEN=ya29.access-token\n"
        "NOTION_TOKEN=secret_notion_token\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(settings, "CONFIG_FILE", config_file)
    monkeypatch.setattr(settings, "ENV_FILE", env_file)
    monkeypatch.setattr(orc_config, "CONFIG_PATH", str(config_file))
    monkeypatch.setattr(orc_config, "_config_cache", None)

    saved_env = {key: os.environ.get(key) for key in MANAGED_ENV}
    for key in MANAGED_ENV:
        os.environ.pop(key, None)

    yield {"config": config_file, "env": env_file}

    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    orc_config._config_cache = None


@pytest.fixture
def api(sandbox):
    server = ui_server.create_server(port=0)
    ui_server._server_instance = server
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{int(server.server_address[1])}"
    try:
        yield base, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        ui_server._server_instance = None


def call(base: str, path: str, *, method: str = "GET", body=None, headers=None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------
def test_save_settings_preserves_comments_and_parses_back(sandbox):
    settings.save_settings(
        {
            "ai.model": "gemini-3.8-flash",
            "ai.temperature": 0.35,
            "voice.tts_enabled": False,
            "ai.fallback": [{"provider": "gemini", "model": "gemini-3.8-flash"}],
        }
    )

    text = sandbox["config"].read_text(encoding="utf-8")
    assert "# Oryn test configuration" in text
    assert "# Primary provider: gemini | groq" in text

    parsed = yaml.safe_load(text)
    assert parsed["ai"]["model"] == "gemini-3.8-flash"
    assert parsed["ai"]["temperature"] == 0.35
    assert parsed["ai"]["fallback"] == [{"provider": "gemini", "model": "gemini-3.8-flash"}]
    assert parsed["voice"]["tts_enabled"] is False

    assert orc_config.get("ai.model") == "gemini-3.8-flash"


def test_new_section_is_appended_when_missing(sandbox):
    settings.save_settings({"logging.level": "DEBUG", "logging.enabled": False})

    parsed = yaml.safe_load(sandbox["config"].read_text(encoding="utf-8"))
    assert parsed["logging"] == {"level": "DEBUG", "enabled": False}
    assert parsed["ai"]["model"] == "gemini-3.5-flash-lite"


def test_load_settings_exposes_schema_values_and_no_secret_values(sandbox):
    data = settings.load_settings()

    assert len(data["schema"]) == 9
    assert data["values"]["ai.model"] == "gemini-3.5-flash-lite"
    assert data["values"]["voice.tts_rate"] == 175
    assert {provider["name"] for provider in data["providers"]} >= {"gemini", "groq"}

    rendered = json.dumps(data)
    assert "gemini-secret-key-1234" not in rendered
    assert "secret_notion_token" not in rendered


def test_validation_reports_every_bad_field():
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.validate_settings(
            {
                "ai.provider": "not-a-provider",
                "ai.temperature": 9,
                "ai.max_tokens": "many",
                "hotkey.aris": "banana",
                "ai.fallback": [{"provider": "groq", "model": ""}],
                "nope.nope": True,
            }
        )

    errors = excinfo.value.errors
    assert "ai.provider" in errors
    assert "ai.temperature" in errors
    assert "ai.max_tokens" in errors
    assert "hotkey.aris" in errors
    assert "nope.nope" in errors
    assert any(path.startswith("ai.fallback[0]") for path in errors)


def test_more_than_five_fallbacks_are_rejected():
    with pytest.raises(settings.SettingsError) as excinfo:
        settings.validate_settings(
            {
                "ai.fallback": [
                    {"provider": "groq", "model": f"model-{index}"} for index in range(6)
                ]
            }
        )
    assert "ai.fallback" in excinfo.value.errors


def test_validation_accepts_a_valid_payload():
    cleaned = settings.validate_settings(
        {
            "ai.temperature": 0.05,
            "ai.max_tokens": 256,
            "voice.tts_enabled": False,
            "hotkey.voice": "ctrl+shift+v",
        }
    )
    assert cleaned["ai.temperature"] == 0.05
    assert cleaned["ai.max_tokens"] == 256
    assert cleaned["voice.tts_enabled"] is False


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
def test_secret_status_masks_values(sandbox):
    status = settings.secret_status()
    items = {item["key"]: item for group in status for item in group["items"]}

    assert items["GEMINI_API_KEY"]["configured"] is True
    assert items["GEMINI_API_KEY"]["preview"].endswith("1234")
    assert "gemini-secret-key-1234" not in json.dumps(status)


def test_save_and_clear_extra_tokens(sandbox):
    settings.save_secret("NOTION_TOKEN", "secret_updated_token")
    text = sandbox["env"].read_text(encoding="utf-8")
    assert "NOTION_TOKEN=secret_updated_token" in text
    assert "# credentials" in text

    settings.clear_secret("NOTION_TOKEN")
    text = sandbox["env"].read_text(encoding="utf-8")
    assert "NOTION_TOKEN" not in text
    assert "GEMINI_API_KEY=gemini-secret-key-1234" in text


def test_unknown_secret_is_rejected(sandbox):
    with pytest.raises(settings.SettingsError):
        settings.save_secret("NOT_A_REAL_KEY", "value")


def test_clear_service_credentials_only_removes_that_service(sandbox):
    removed = settings.clear_service_credentials("drive")

    text = sandbox["env"].read_text(encoding="utf-8")
    assert "GOOGLE_ACCESS_TOKEN" in removed
    assert "GOOGLE_REFRESH_TOKEN" not in text
    assert "GOOGLE_ACCESS_TOKEN" not in text
    assert "NOTION_TOKEN=secret_notion_token" in text
    assert "GEMINI_API_KEY" in text


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
def test_settings_api_reads_and_writes(sandbox, api):
    base, port = api

    status, data = call(base, "/api/settings")
    assert status == 200
    assert data["values"]["ai.model"] == "gemini-3.5-flash-lite"

    status, data = call(
        base,
        "/api/settings",
        method="POST",
        body={"values": {"ai.model": "gemini-3.8-flash", "voice.tts_rate": 180}},
        headers={"Origin": f"http://localhost:{port}"},
    )
    assert status == 200, data
    assert "ai.model" in data["saved"]
    assert data["values"]["ai.model"] == "gemini-3.8-flash"

    parsed = yaml.safe_load(sandbox["config"].read_text(encoding="utf-8"))
    assert parsed["ai"]["model"] == "gemini-3.8-flash"


def test_settings_api_returns_field_errors(sandbox, api):
    base, _ = api
    status, data = call(
        base,
        "/api/settings",
        method="POST",
        body={"values": {"ai.temperature": 99}},
    )

    assert status == 400
    assert data["ok"] is False
    assert "ai.temperature" in data["errors"]
    assert yaml.safe_load(sandbox["config"].read_text(encoding="utf-8"))["ai"]["temperature"] == 0.7


def test_notion_token_modal_can_save_a_token(sandbox, api):
    base, _ = api
    status, data = call(
        base,
        "/api/settings/secret",
        method="POST",
        body={"key": "NOTION_TOKEN", "value": "secret_from_modal"},
    )

    assert status == 200, data
    assert data["ok"] is True
    assert "NOTION_TOKEN=secret_from_modal" in sandbox["env"].read_text(encoding="utf-8")


def test_cross_origin_writes_are_blocked(sandbox, api):
    base, _ = api
    status, data = call(
        base,
        "/api/settings",
        method="POST",
        body={"values": {"ai.model": "hijacked"}},
        headers={"Origin": "https://evil.example"},
    )

    assert status == 403
    assert data["ok"] is False
    assert yaml.safe_load(sandbox["config"].read_text(encoding="utf-8"))["ai"]["model"] == "gemini-3.5-flash-lite"


def test_cross_origin_origin_on_another_port_is_blocked(api):
    base, port = api
    status, _ = call(
        base,
        "/api/clear",
        method="POST",
        body={},
        headers={"Origin": f"http://localhost:{port + 1}"},
    )
    assert status == 403


def test_static_assets_are_served_and_traversal_is_blocked(api):
    base, _ = api

    with urllib.request.urlopen(base + "/", timeout=10) as response:
        html = response.read().decode("utf-8")
        assert response.headers.get_content_type() == "text/html"
    assert "<title>Oryn" in html
    assert "/assets/app.js" in html

    with urllib.request.urlopen(base + "/assets/styles.css", timeout=10) as response:
        assert response.headers.get_content_type() == "text/css"

    request = urllib.request.Request(base + "/assets/..%2F..%2Fsettings.py")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=10)
    assert excinfo.value.code == 404


def test_tools_api_returns_the_catalog(api):
    base, _ = api
    status, data = call(base, "/api/tools")

    assert status == 200
    assert len(data["tools"]) >= 300
    assert data["totals"]["grandTotal"] >= 300
    assert isinstance(data["native"], list)


def test_chat_rejects_empty_message_and_clear_resets_history(api):
    base, _ = api

    status, data = call(base, "/api/chat", method="POST", body={"message": "   "})
    assert status == 400
    assert data["ok"] is False

    status, data = call(base, "/api/clear", method="POST", body={})
    assert status == 200
    assert data["ok"] is True
