import os
import yaml

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_PATH = os.path.join(ORYN_ROOT, 'config.yaml')

DEFAULTS = {
    'ai': {
        'provider': 'openrouter',
        'model': 'anthropic/claude-3.5-sonnet',
        'max_tokens': 1000,
        'temperature': 0.7,
        'fallback': [
            {'provider': 'groq', 'model': 'llama-3.3-70b-versatile'}
        ]
    },
    'memory': {
        'stm_word_limit': 80,
        'compress_trigger': 60
    },
    'hotkey': {
        'aris': 'ctrl+alt+0',
        'voice': 'ctrl+alt+v'
    },
    'search': {
        'enabled': True,
        'max_results': 5
    },
    'voice': {
        'stt_enabled': True,
        'tts_enabled': True,
        'whisper_model': 'tiny',
        'tts_rate': 175,
        'tts_voice': 0,
        'listen_duration': 5
    },
    'plugins': {
        'enabled': True,
        'folder': 'plugins'
    },
    'update': {
        'check_on_start': True,
        'repo': 'jaya-prakash1123/none'
    },
    'logging': {
        'enabled': True,
        'level': 'INFO',
        'max_size_mb': 5,
        'backup_count': 3
    }
}


def _deep_merge(base, override):
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_config_cache = None


def load_config(path=None):
    """Load config.yaml with defaults fallback."""
    global _config_cache
    if _config_cache is not None and path is None:
        return _config_cache

    config_path = path or CONFIG_PATH
    config = DEFAULTS.copy()

    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = yaml.safe_load(f)
            if user_config:
                config = _deep_merge(config, user_config)
        except Exception:
            pass

    _config_cache = config
    return config


def get(key_path, default=None):
    """Get config value by dot path. e.g. get('ai.model')"""
    config = load_config()
    keys = key_path.split('.')
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


def save_config(config, path=None):
    """Save config to YAML file."""
    config_path = path or CONFIG_PATH
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    global _config_cache
    _config_cache = config
