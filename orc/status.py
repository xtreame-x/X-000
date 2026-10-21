import os
import json
from datetime import datetime
from orc.memory import load_stm, load_ltm
from orc.sysbridge import get_sysinfo

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
START_TIME = datetime.now()


def _bar(percent, width=20):
    filled = int(width * percent / 100)
    bar = '█' * filled + '░' * (width - filled)
    color = '\033[32m' if percent < 60 else '\033[33m' if percent < 85 else '\033[31m'
    return f"{color}{bar}\033[0m"


def _uptime():
    delta = datetime.now() - START_TIME
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _aris_status():
    try:
        import keyboard
        return "\033[32marmed\033[0m"
    except Exception:
        return "\033[31mdisarmed\033[0m"


def _stm_status():
    stm = load_stm()
    if not stm:
        return "empty", 0
    words = len(stm.split())
    return stm[:40] + ('...' if len(stm) > 40 else ''), words


def _ltm_status():
    try:
        entries = load_ltm()
        return len(entries)
    except Exception:
        return 0


def _file_status():
    counts = {'py': 0, 'cpp': 0, 'exe': 0, 'other': 0}
    for root, dirs, files in os.walk(ORYN_ROOT):
        dirs[:] = [d for d in dirs if d != '.git']
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext == '.py':
                counts['py'] += 1
            elif ext == '.cpp':
                counts['cpp'] += 1
            elif ext == '.exe':
                counts['exe'] += 1
            else:
                counts['other'] += 1
    return counts


def render():
    W = 50
    div  = '\033[90m' + '─' * W + '\033[0m'
    hdiv = '\033[34m' + '═' * W + '\033[0m'

    lines = []
    lines.append(hdiv)
    lines.append('\033[34m  ORYN // STATUS\033[0m')
    lines.append(hdiv)

    # uptime + aris
    lines.append(f"  uptime   \033[97m{_uptime()}\033[0m")
    lines.append(f"  ARIS     {_aris_status()}")
    lines.append(div)

    # system
    data = get_sysinfo()
    if data:
        lines.append('\033[90m  SYSTEM\033[0m')
        lines.append(f"  CPU      {_bar(data['cpu_usage'])} {data['cpu_usage']:.1f}%")
        lines.append(f"  RAM      {_bar(data['ram_percent'])} {data['ram_used_mb']:.0f}/{data['ram_total_mb']:.0f} MB")
        lines.append(f"  Disk C   {_bar(data['disk_percent'])} {data['disk_used_gb']:.1f}/{data['disk_total_gb']:.1f} GB")

        lines.append(div)
        lines.append('\033[90m  TOP PROCESSES\033[0m')
        for p in data['processes'][:5]:
            name = p['name'][:22].ljust(22)
            lines.append(f"  \033[97m{name}\033[0m  {p['ram_mb']:.0f} MB")
    else:
        lines.append('  \033[31msysinfo unavailable\033[0m')

    lines.append(div)

    # memory
    lines.append('\033[90m  MEMORY\033[0m')
    stm_preview, stm_words = _stm_status()
    ltm_count = _ltm_status()
    lines.append(f"  STM      \033[97m{stm_words} words\033[0m  {stm_preview}")
    lines.append(f"  LTM      \033[97m{ltm_count} entries\033[0m")
    lines.append(div)

    # files
    lines.append('\033[90m  FILES\033[0m')
    fc = _file_status()
    lines.append(f"  .py      \033[97m{fc['py']}\033[0m   .cpp  \033[97m{fc['cpp']}\033[0m   .exe  \033[97m{fc['exe']}\033[0m")
    lines.append(hdiv)

    return '\n'.join(lines)
