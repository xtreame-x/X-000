import os
import requests
from orc.logger import get_logger

logger = get_logger('updater')

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
VERSION_FILE = os.path.join(ORYN_ROOT, 'VERSION')
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


def get_version():
    """Get current Oryn version."""
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, 'r') as f:
            return f.read().strip()
    return "0.2.0"


def check_update(config: dict) -> dict:
    """Check for updates on GitHub."""
    repo = config.get('update', {}).get('repo', 'jaya-prakash1123/none')
    current = get_version()

    try:
        url = GITHUB_API.format(repo=repo)
        resp = requests.get(url, timeout=10, headers={'Accept': 'application/vnd.github.v3+json'})

        if resp.status_code != 200:
            return {'available': False, 'error': f'GitHub API error: {resp.status_code}'}

        data = resp.json()
        latest = data.get('tag_name', '').lstrip('v')

        if not latest:
            return {'available': False, 'error': 'no release tag found'}

        # Simple version comparison
        available = _version_gt(latest, current)

        return {
            'available': available,
            'current': current,
            'latest': latest,
            'name': data.get('name', ''),
            'url': data.get('html_url', ''),
            'notes': data.get('body', '')[:500],
        }

    except requests.exceptions.Timeout:
        return {'available': False, 'error': 'timeout checking for updates'}
    except Exception as e:
        logger.error(f"Update check failed: {e}")
        return {'available': False, 'error': str(e)}


def _version_gt(v1: str, v2: str) -> bool:
    """Check if v1 > v2."""
    try:
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        return parts1 > parts2
    except Exception:
        return False


def format_update_status(result: dict) -> str:
    """Format update check result for display."""
    if result.get('error'):
        return f"\033[33mupdate check: {result['error']}\033[0m"

    if not result.get('available'):
        return f"\033[32moryn is up to date (v{result['current']})\033[0m"

    lines = [
        f"\033[33mupdate available: v{result['current']} -> v{result['latest']}\033[0m",
    ]

    if result.get('name'):
        lines.append(f"  {result['name']}")

    if result.get('url'):
        lines.append(f"  \033[90m{result['url']}\033[0m")

    if result.get('notes'):
        lines.append(f"\n  {result['notes'][:200]}...")

    return '\n'.join(lines)
