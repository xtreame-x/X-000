import json
import subprocess
import os
import signal


def get_actual_username():
    """Get the actual logged-in Windows username (not from memory)."""
    return os.environ.get('USERNAME', 'unknown')


def get_sysinfo():
    """Call sysinfo.exe and parse the JSON output."""
    try:
        exe_path = os.path.join(os.path.dirname(__file__), '..', 'exe', 'sysinfo.exe')
        result = subprocess.run(
            [exe_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


def format_sysinfo(data):
    """Format system info data into a readable string."""
    if not data:
        return "sys: unavailable"
    
    lines = []
    
    # CPU
    cpu = data.get('cpu_usage', -1)
    if cpu >= 0:
        lines.append(f"CPU:  {cpu:.1f}%")
    
    # RAM
    ram_pct = data.get('ram_percent', -1)
    if ram_pct >= 0:
        ram_used = data.get('ram_used_mb', 0)
        ram_total = data.get('ram_total_mb', 0)
        lines.append(f"RAM:  {ram_pct:.1f}% ({ram_used:.0f}MB / {ram_total:.0f}MB)")
    
    # Disk
    disk_pct = data.get('disk_percent', -1)
    if disk_pct >= 0:
        disk_used = data.get('disk_used_gb', 0)
        disk_total = data.get('disk_total_gb', 0)
        lines.append(f"DISK: {disk_pct:.1f}% ({disk_used:.1f}GB / {disk_total:.1f}GB)")
    
    # Top 3 processes by RAM
    processes = data.get('processes', [])
    if processes:
        lines.append("\nTop processes:")
        for proc in processes[:3]:
            name = proc.get('name', 'unknown')
            ram_mb = proc.get('ram_mb', 0)
            lines.append(f"  {name:<20} {ram_mb:>8.1f}MB")
    
    return "\033[90m" + "\n".join(lines) + "\033[0m"


TIMEOUT = 10  # seconds

# commands that are too dangerous to run
BLACKLIST = [
    'format', 'mkfs', 'dd if=', 'rm -rf /',
    'deltree', 'rd /s', 'shutdown', 'reboot',
]


def is_blacklisted(cmd):
    cmd_lower = cmd.lower()
    return any(b in cmd_lower for b in BLACKLIST)


def run_command(cmd, timeout=TIMEOUT):
    """
    Run a shell command and return (stdout, stderr, returncode).
    Non-blocking with timeout.
    """
    if is_blacklisted(cmd):
        return '', 'blocked: command not permitted', -1

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.expanduser('~'),
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        return stdout, stderr, result.returncode

    except subprocess.TimeoutExpired:
        return '', f'timed out after {timeout}s', -1
    except Exception as e:
        return '', str(e), -1


def format_output(stdout, stderr, returncode):
    """Format command output for display in terminal."""
    lines = []
    if stdout:
        lines.append(stdout)
    if stderr:
        lines.append(f'\033[31m{stderr}\033[0m')
    if not stdout and not stderr:
        lines.append('done.' if returncode == 0 else f'exited with code {returncode}')
    return '\n'.join(lines)
