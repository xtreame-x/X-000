"""Settings service for the Oryn dashboard.

The dashboard edits two files that people also edit by hand:

* ``config.yaml`` — runtime behaviour. It carries a lot of explanatory comments,
  so this module never re-dumps the file with PyYAML; it patches the exact
  ``section.key`` lines in place and keeps every other byte intact.
* ``.env`` — secrets and credentials. Comments and ordering are preserved the
  same way, reusing :func:`orc.oauth.update_env_file` for writes.

Everything the UI can change is declared once in :data:`SCHEMA` /
:data:`SECRETS`, which is also what the HTTP layer serialises to the browser.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from dotenv import load_dotenv

from agent.platform_compat import atomic_write, read_text_normalized
from orc import config as orc_config
from orc.oauth import update_env_file

ORYN_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ORYN_ROOT / "config.yaml"
ENV_FILE = ORYN_ROOT / ".env"


class SettingsError(ValueError):
    """Invalid settings payload; ``errors`` maps dot-paths to messages."""

    def __init__(self, errors: Mapping[str, str] | str) -> None:
        if isinstance(errors, str):
            errors = {"": errors}
        self.errors = dict(errors)
        super().__init__("; ".join(f"{k}: {v}" if k else v for k, v in self.errors.items()))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
# Field types: text | number | range | boolean | select | fallback
SCHEMA: list[dict[str, Any]] = [
    {
        "id": "ai",
        "label": "AI & Models",
        "icon": "spark",
        "description": "Primary model, coding model and the fallback chain used when a provider fails.",
        "fields": [
            {
                "path": "ai.provider",
                "label": "Primary provider",
                "type": "select",
                "options": ["gemini", "groq", "openrouter", "nvidia"],
                "help": "The provider Oryn tries first on every request.",
            },
            {
                "path": "ai.model",
                "label": "Primary model",
                "type": "text",
                "mono": True,
                "placeholder": "gemini-3.5-flash-lite",
                "help": "Exact model id, for example gemini-3.5-flash-lite.",
            },
            {
                "path": "ai.coding_provider",
                "label": "Coding provider",
                "type": "select",
                "options": ["", "gemini", "groq", "openrouter", "nvidia"],
                "help": "Used for complex or code-heavy turns. Leave on inherit to reuse the primary.",
            },
            {
                "path": "ai.coding_model",
                "label": "Coding model",
                "type": "text",
                "mono": True,
                "placeholder": "gemini-3.5-flash-lite",
            },
            {
                "path": "ai.max_tokens",
                "label": "Max output tokens",
                "type": "number",
                "min": 128,
                "max": 8192,
                "step": 1,
                "help": "Upper bound on the length of a reply.",
            },
            {
                "path": "ai.temperature",
                "label": "Temperature",
                "type": "range",
                "min": 0,
                "max": 2,
                "step": 0.05,
                "help": "Lower is more deterministic, higher is more creative.",
            },
            {
                "path": "ai.fallback",
                "label": "Fallback chain",
                "type": "fallback",
                "help": "Tried in order when the primary provider errors or is rate limited.",
            },
        ],
    },
    {
        "id": "memory",
        "label": "Memory",
        "icon": "memory",
        "description": "Short-term memory compression thresholds.",
        "fields": [
            {
                "path": "memory.stm_word_limit",
                "label": "Short-term memory limit",
                "type": "number",
                "min": 20,
                "max": 400,
                "step": 10,
                "suffix": "words",
            },
            {
                "path": "memory.compress_trigger",
                "label": "Compression trigger",
                "type": "number",
                "min": 10,
                "max": 400,
                "step": 10,
                "suffix": "words",
                "help": "Once the thread passes this size it is compressed into long-term memory.",
            },
        ],
    },
    {
        "id": "voice",
        "label": "Voice",
        "icon": "mic",
        "description": "Speech-to-text and text-to-speech behaviour.",
        "fields": [
            {"path": "voice.stt_enabled", "label": "Speech input", "type": "boolean"},
            {"path": "voice.tts_enabled", "label": "Spoken replies", "type": "boolean"},
            {
                "path": "voice.whisper_model",
                "label": "Whisper model",
                "type": "select",
                "options": ["tiny", "base", "small", "medium", "large"],
            },
            {
                "path": "voice.tts_rate",
                "label": "Speech rate",
                "type": "number",
                "min": 100,
                "max": 300,
                "step": 5,
                "suffix": "wpm",
            },
            {"path": "voice.tts_voice", "label": "Voice index", "type": "number", "min": 0, "max": 10, "step": 1},
            {
                "path": "voice.listen_duration",
                "label": "Listen duration",
                "type": "number",
                "min": 1,
                "max": 30,
                "step": 1,
                "suffix": "sec",
            },
        ],
    },
    {
        "id": "hotkey",
        "label": "Hotkeys",
        "icon": "keyboard",
        "description": "Global shortcuts, in the form ctrl+alt+0.",
        "fields": [
            {"path": "hotkey.aris", "label": "ARIS hotkey", "type": "text", "mono": True, "placeholder": "ctrl+alt+0"},
            {"path": "hotkey.voice", "label": "Voice hotkey", "type": "text", "mono": True, "placeholder": "ctrl+alt+v"},
        ],
    },
    {
        "id": "location",
        "label": "Location",
        "icon": "pin",
        "description": "Fallback place used for weather, places and time questions.",
        "fields": [
            {"path": "location.city", "label": "City", "type": "text", "placeholder": "Delhi"},
            {"path": "location.country", "label": "Country", "type": "text", "placeholder": "India"},
        ],
    },
    {
        "id": "search",
        "label": "Search",
        "icon": "globe",
        "description": "Web search behaviour.",
        "fields": [
            {"path": "search.enabled", "label": "Web search", "type": "boolean"},
            {"path": "search.max_results", "label": "Results per search", "type": "number", "min": 1, "max": 20, "step": 1},
        ],
    },
    {
        "id": "plugins",
        "label": "Plugins",
        "icon": "puzzle",
        "description": "Third-party tools that extend the agent.",
        "fields": [
            {"path": "plugins.enabled", "label": "Load plugins", "type": "boolean"},
            {"path": "plugins.folder", "label": "Plugin folder", "type": "text", "mono": True, "placeholder": "plugins"},
        ],
    },
    {
        "id": "update",
        "label": "Updates",
        "icon": "download",
        "description": "Startup update checks.",
        "fields": [
            {"path": "update.check_on_start", "label": "Check on start", "type": "boolean"},
            {"path": "update.repo", "label": "Repository", "type": "text", "mono": True, "placeholder": "owner/repo"},
        ],
    },
    {
        "id": "logging",
        "label": "Logging",
        "icon": "terminal",
        "description": "Log verbosity and rotation.",
        "fields": [
            {"path": "logging.enabled", "label": "Write logs", "type": "boolean"},
            {
                "path": "logging.level",
                "label": "Level",
                "type": "select",
                "options": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            },
            {"path": "logging.max_size_mb", "label": "Max file size", "type": "number", "min": 1, "max": 100, "step": 1, "suffix": "MB"},
            {"path": "logging.backup_count", "label": "Backups kept", "type": "number", "min": 1, "max": 20, "step": 1},
        ],
    },
]

FIELD_BY_PATH: dict[str, dict[str, Any]] = {
    field["path"]: field for section in SCHEMA for field in section["fields"]
}

# Secrets the dashboard can read (masked) and write. Non-secret entries are
# plain configuration that happens to live in .env.
SECRETS: list[dict[str, Any]] = [
    {
        "id": "providers",
        "label": "AI provider keys",
        "description": "Add a key to enable that provider in the fallback chain.",
        "items": [
            {"key": "GEMINI_API_KEY", "label": "Gemini", "secret": True, "url": "https://aistudio.google.com/apikey"},
            {
                "key": "GROQ_API_KEY",
                "label": "Groq",
                "secret": True,
                "url": "https://console.groq.com/keys",
            },
            {
                "key": "OPENROUTER_API_KEY",
                "label": "OpenRouter",
                "secret": True,
                "url": "https://openrouter.ai/keys",
            },
            {
                "key": "NVIDIA_API_KEY",
                "label": "NVIDIA NIM",
                "secret": True,
                "url": "https://build.nvidia.com/",
            },
            {
                "key": "SERPER_API_KEY",
                "label": "Serper search",
                "secret": True,
                "url": "https://serper.dev/api-key",
            },
        ],
    },
    {
        "id": "smtp",
        "label": "Email (SMTP)",
        "description": "For Gmail use an App Password, not your account password.",
        "items": [
            {"key": "AGENT_SMTP_HOST", "label": "Host", "secret": False, "placeholder": "smtp.gmail.com"},
            {"key": "AGENT_SMTP_PORT", "label": "Port", "secret": False, "placeholder": "587"},
            {"key": "AGENT_SMTP_USER", "label": "Username", "secret": False, "placeholder": "you@gmail.com"},
            {"key": "AGENT_SMTP_PASSWORD", "label": "App password", "secret": True},
            {"key": "AGENT_SMTP_FROM", "label": "From address", "secret": False, "placeholder": "you@gmail.com"},
        ],
    },
    {
        "id": "oauth",
        "label": "OAuth apps",
        "description": "Only needed once, to register Oryn with Google or Notion.",
        "items": [
            {"key": "GOOGLE_CLIENT_ID", "label": "Google client ID", "secret": False},
            {"key": "GOOGLE_CLIENT_SECRET", "label": "Google client secret", "secret": True},
            {"key": "NOTION_CLIENT_ID", "label": "Notion client ID", "secret": False},
            {"key": "NOTION_CLIENT_SECRET", "label": "Notion client secret", "secret": True},
        ],
    },
]

SECRET_KEYS: dict[str, dict[str, Any]] = {
    item["key"]: item for group in SECRETS for item in group["items"]
}

# Tokens written by the OAuth flows or the Notion paste dialog. They can be
# replaced or removed through the API without being offered as editable fields
# in the Credentials screen (the Connections screen owns those flows).
EXTRA_SECRET_KEYS: dict[str, dict[str, Any]] = {
    "NOTION_TOKEN": {"key": "NOTION_TOKEN", "label": "Notion token", "secret": True},
    "GOOGLE_ACCESS_TOKEN": {"key": "GOOGLE_ACCESS_TOKEN", "label": "Google access token", "secret": True},
    "GOOGLE_REFRESH_TOKEN": {"key": "GOOGLE_REFRESH_TOKEN", "label": "Google refresh token", "secret": True},
}
WRITABLE_SECRET_KEYS: dict[str, dict[str, Any]] = {**SECRET_KEYS, **EXTRA_SECRET_KEYS}

PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "nvidia": ("NVIDIA_API_KEY",),
}

_HOTKEY_RE = re.compile(r"^(ctrl|alt|shift|win)(\+(ctrl|alt|shift|win))*\+[a-z0-9]+$", re.IGNORECASE)
_NUMBER_TEXT_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)$")
_PLAIN_SCALAR_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9 _./:+@-]*$")
_NEWLINE_RE = re.compile(r"[\r\n\x00]")


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------
def _flatten(config: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for section, values in config.items():
        if isinstance(values, dict):
            for key, value in values.items():
                flat[f"{section}.{key}"] = value
        else:
            flat[section] = values
    return flat


def _mask(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 4:
        return "•" * len(cleaned)
    return "••••••••" + cleaned[-4:]


def secret_status() -> list[dict[str, Any]]:
    """Secret groups with masked previews; values never leave the machine."""
    load_dotenv(ENV_FILE, override=True)
    groups: list[dict[str, Any]] = []
    for group in SECRETS:
        items = []
        for item in group["items"]:
            value = (os.environ.get(item["key"]) or "").strip()
            items.append(
                {
                    "key": item["key"],
                    "label": item["label"],
                    "secret": item.get("secret", True),
                    "url": item.get("url"),
                    "placeholder": item.get("placeholder"),
                    "configured": bool(value),
                    "preview": _mask(value) if item.get("secret", True) else value,
                }
            )
        groups.append(
            {
                "id": group["id"],
                "label": group["label"],
                "description": group["description"],
                "items": items,
            }
        )
    return groups


def provider_catalog() -> list[dict[str, Any]]:
    """Providers that Oryn can talk to, their models, and key requirements."""
    try:
        from orc.providers import PROVIDER_MAP
    except Exception:  # pragma: no cover - import problems surface in the UI
        return []

    load_dotenv(ENV_FILE, override=True)
    catalog: list[dict[str, Any]] = []
    for name, provider_cls in PROVIDER_MAP.items():
        models: list[str] = []
        try:
            provider = provider_cls()
            models = list(provider.list_models())
        except Exception:
            models = list(getattr(provider_cls, "AVAILABLE_MODELS", []))
        keys = PROVIDER_KEYS.get(name, ())
        catalog.append(
            {
                "name": name,
                "label": name.replace("openrouter", "OpenRouter").replace("nvidia", "NVIDIA").title(),
                "models": models,
                "available": any((os.environ.get(k) or "").strip() for k in keys),
                "key_env": keys[0] if keys else None,
            }
        )
    return catalog


def current_version() -> str:
    try:
        return (ORYN_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


def load_settings() -> dict[str, Any]:
    """Everything the settings screen needs in one payload."""
    orc_config._config_cache = None  # always reflect the file on disk
    flat = _flatten(orc_config.load_config())
    values = {path: flat.get(path) for path in FIELD_BY_PATH}
    return {
        "schema": SCHEMA,
        "values": values,
        "secrets": secret_status(),
        "providers": provider_catalog(),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _coerce(field: Mapping[str, Any], raw: Any) -> Any:
    path = field["path"]
    kind = field["type"]

    if kind in ("text", "select"):
        if raw is None:
            raw = ""
        if not isinstance(raw, (str, int, float, bool)):
            raise SettingsError({path: "expected text"})
        text = str(raw).strip()
        if _NEWLINE_RE.search(text):
            raise SettingsError({path: "must be a single line"})
        if len(text) > 300:
            raise SettingsError({path: "is too long (max 300 characters)"})
        if kind == "select":
            options = field.get("options", [])
            if text not in options:
                raise SettingsError({path: f"must be one of {', '.join(o or 'inherit' for o in options)}"})
        if path in ("hotkey.aris", "hotkey.voice") and text and not _HOTKEY_RE.match(text):
            raise SettingsError({path: "use a combination like ctrl+alt+0"})
        return text

    if kind in ("number", "range"):
        if isinstance(raw, bool) or raw is None or raw == "":
            raise SettingsError({path: "is required"})
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise SettingsError({path: "must be a number"}) from None
        if number != number:  # NaN
            raise SettingsError({path: "must be a number"})
        low, high = field.get("min"), field.get("max")
        if low is not None and number < low:
            raise SettingsError({path: f"must be at least {low:g}"})
        if high is not None and number > high:
            raise SettingsError({path: f"must be at most {high:g}"})
        if kind == "number" and float(number).is_integer():
            return int(number)
        return round(number, 4)

    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            if raw.lower() in ("true", "1", "on", "yes"):
                return True
            if raw.lower() in ("false", "0", "off", "no", ""):
                return False
        if isinstance(raw, (int, float)):
            return bool(raw)
        raise SettingsError({path: "must be true or false"})

    if kind == "fallback":
        if not isinstance(raw, list):
            raise SettingsError({path: "expected a list"})
        if len(raw) > 5:
            raise SettingsError({path: "at most 5 fallbacks are supported"})
        cleaned: list[dict[str, str]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise SettingsError({path: f"entry {index + 1} is invalid"})
            provider = str(item.get("provider", "")).strip()
            model = str(item.get("model", "")).strip()
            if not provider and not model:
                continue
            if provider not in PROVIDER_KEYS:
                raise SettingsError({f"{path}[{index}].provider": f"unknown provider {provider!r}"})
            if not model:
                raise SettingsError({f"{path}[{index}].model": "model is required"})
            if _NEWLINE_RE.search(model):
                raise SettingsError({f"{path}[{index}].model": "must be a single line"})
            cleaned.append({"provider": provider, "model": model})
        return cleaned

    raise SettingsError({path: f"unsupported field type {kind}"})


def validate_settings(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a partial settings payload; unknown paths are rejected."""
    errors: dict[str, str] = {}
    cleaned: dict[str, Any] = {}
    for path, raw in payload.items():
        field = FIELD_BY_PATH.get(path)
        if field is None:
            errors[path] = "unknown setting"
            continue
        try:
            cleaned[path] = _coerce(field, raw)
        except SettingsError as exc:
            errors.update(exc.errors)
    if errors:
        raise SettingsError(errors)
    if not cleaned:
        raise SettingsError("no changes submitted")
    return cleaned


# ---------------------------------------------------------------------------
# config.yaml writing (comment preserving)
# ---------------------------------------------------------------------------
def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value}"
    text = "" if value is None else str(value)
    if text == "":
        return '""'
    if (
        _PLAIN_SCALAR_RE.match(text)
        and not _NUMBER_TEXT_RE.match(text)
        and text.lower() not in {"true", "false", "yes", "no", "null", "on", "off", "~"}
        and not text.endswith(":")
    ):
        return text
    return json.dumps(text, ensure_ascii=False)


def _section_bounds(lines: list[str], section: str) -> tuple[int, int] | None:
    header = re.compile(rf"^{re.escape(section)}:\s*(?:#.*)?$")
    for index, line in enumerate(lines):
        if not header.match(line):
            continue
        end = len(lines)
        for probe in range(index + 1, len(lines)):
            candidate = lines[probe]
            if candidate.strip() and not candidate[0].isspace() and not candidate.lstrip().startswith("#"):
                end = probe
                break
        return index, end
    return None


def _replace_fallback(lines: list[str], section: str, entries: list[dict[str, str]]) -> list[str]:
    bounds = _section_bounds(lines, section)
    if bounds is None:
        return lines
    start, end = bounds
    block_start = None
    for index in range(start + 1, end):
        if re.match(r"^\s+fallback:\s*(?:#.*)?$", lines[index]):
            block_start = index
            break
    if block_start is None:
        return lines

    block_end = block_start + 1
    while block_end < end and lines[block_end].startswith((" ", "\t")):
        block_end += 1

    if entries:
        rendered = ["  fallback:"]
        for entry in entries:
            rendered.append(f"    - provider: {_yaml_scalar(entry['provider'])}")
            rendered.append(f"      model: {_yaml_scalar(entry['model'])}")
    else:
        rendered = ["  fallback: []"]
    return lines[:block_start] + rendered + lines[block_end:]


def _set_scalar(lines: list[str], section: str, key: str, value: Any) -> list[str]:
    rendered = _yaml_scalar(value)
    bounds = _section_bounds(lines, section)
    if bounds is None:
        while lines and lines[-1] == "":
            lines.pop()
        if lines:
            lines.append("")
        lines.extend([f"{section}:", f"  {key}: {rendered}"])
        return lines

    start, end = bounds
    key_re = re.compile(rf"^(\s+){re.escape(key)}:\s*(?:[^#]*?)\s*(#.*)?$")
    for index in range(start + 1, end):
        match = key_re.match(lines[index])
        if match:
            comment = f"  {match.group(2)}" if match.group(2) else ""
            lines[index] = f"{match.group(1)}{key}: {rendered}{comment}"
            return lines

    # Insert new keys directly under the section header so they can never land
    # inside a nested block (such as ai.fallback) that is rewritten separately.
    lines.insert(start + 1, f"  {key}: {rendered}")
    return lines


def _config_target(config_file: str | Path | None) -> Path:
    return Path(config_file) if config_file is not None else CONFIG_FILE


def _env_target(env_file: str | Path | None) -> Path:
    return Path(env_file) if env_file is not None else ENV_FILE


def write_settings(paths: Mapping[str, Any], config_file: str | Path | None = None) -> None:
    """Apply validated settings to config.yaml, preserving comments and order."""
    target = _config_target(config_file)
    if target.exists():
        text, newline = read_text_normalized(target)
    else:
        text, newline = "", "\n"

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    for path, value in paths.items():
        if "." not in path:
            raise SettingsError({path: "settings need a section"})
        section, key = path.split(".", 1)
        if isinstance(value, list):
            lines = _replace_fallback(lines, section, value)
        else:
            lines = _set_scalar(lines, section, key, value)

    atomic_write(target, "\n".join(lines) + "\n", newline=newline)
    orc_config._config_cache = None


def save_settings(payload: Mapping[str, Any], config_file: str | Path | None = None) -> dict[str, Any]:
    cleaned = validate_settings(payload)
    write_settings(cleaned, config_file=config_file)

    global _provider_manager_invalidator
    if _provider_manager_invalidator is not None:
        _provider_manager_invalidator()

    result = load_settings()
    result["saved"] = sorted(cleaned)
    return result


# The UI server registers a callback here so a settings save drops its cached
# ProviderManager and the new model takes effect immediately.
_provider_manager_invalidator = None


def set_provider_invalidator(callback) -> None:
    global _provider_manager_invalidator
    _provider_manager_invalidator = callback


# ---------------------------------------------------------------------------
# .env writing
# ---------------------------------------------------------------------------
def save_secret(key: str, value: str, env_file: str | Path | None = None) -> None:
    item = WRITABLE_SECRET_KEYS.get(key)
    if item is None:
        raise SettingsError({key: "unknown credential"})
    text = (value or "").strip()
    if not text:
        raise SettingsError({key: "value is required"})
    if _NEWLINE_RE.search(text):
        raise SettingsError({key: "must be a single line"})
    if len(text) > 4000:
        raise SettingsError({key: "is too long"})
    target = _env_target(env_file)
    update_env_file({key: text}, env_file=target)
    os.environ[key] = text
    load_dotenv(dotenv_path=target, override=True)
    if _provider_manager_invalidator is not None:
        _provider_manager_invalidator()


def _remove_env_values(keys: Iterable[str], env_file: str | Path | None = None) -> None:
    target = _env_target(env_file)
    if not target.exists():
        return
    text, newline = read_text_normalized(target)
    key_re = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    doomed = set(keys)
    kept: list[str] = []
    for line in text.split("\n"):
        match = key_re.match(line)
        if match and match.group(1) in doomed:
            continue
        kept.append(line)
    atomic_write(target, "\n".join(kept).rstrip("\n") + "\n", newline=newline)


def clear_secret(key: str, env_file: str | Path | None = None) -> None:
    if key not in WRITABLE_SECRET_KEYS:
        raise SettingsError({key: "unknown credential"})
    target = _env_target(env_file)
    _remove_env_values([key], env_file=target)
    os.environ.pop(key, None)
    load_dotenv(dotenv_path=target, override=True)
    if _provider_manager_invalidator is not None:
        _provider_manager_invalidator()


def clear_service_credentials(service: str, env_file: str | Path | None = None) -> list[str]:
    """Remove stored tokens for a connected service."""
    target = _env_target(env_file)
    service = (service or "").strip().lower()
    keys = {
        "drive": (
            "GOOGLE_ACCESS_TOKEN",
            "GOOGLE_REFRESH_TOKEN",
            "GOOGLE_TOKEN_EXPIRES_AT",
        ),
        "notion": (
            "NOTION_TOKEN",
            "NOTION_WORKSPACE_ID",
            "NOTION_WORKSPACE_NAME",
        ),
        "smtp": (
            "AGENT_SMTP_HOST",
            "AGENT_SMTP_PORT",
            "AGENT_SMTP_USER",
            "AGENT_SMTP_PASSWORD",
            "AGENT_SMTP_FROM",
        ),
    }.get(service)
    if keys is None:
        raise SettingsError({"service": f"unknown service {service!r}"})
    _remove_env_values(keys, env_file=target)
    for key in keys:
        os.environ.pop(key, None)
    load_dotenv(dotenv_path=target, override=True)
    return list(keys)


__all__ = [
    "CONFIG_FILE",
    "ENV_FILE",
    "FIELD_BY_PATH",
    "SCHEMA",
    "SECRETS",
    "WRITABLE_SECRET_KEYS",
    "SettingsError",
    "clear_secret",
    "clear_service_credentials",
    "current_version",
    "load_settings",
    "provider_catalog",
    "save_secret",
    "save_settings",
    "secret_status",
    "set_provider_invalidator",
    "validate_settings",
    "write_settings",
]
