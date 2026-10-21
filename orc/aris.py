import os
import sys
import random
import string
import struct
import subprocess
import threading
import shutil
import keyboard

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

GITHUB_REPO = "https://github.com/jaya-prakash1123/none.git"

# files/dirs to skip during destruction
SKIP_NAMES = {'aris.py'}

# ── utils ─────────────────────────────────────────────────────────────────────

def random_string(length):
    return ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=length))

def random_bytes(length):
    return bytes(random.getrandbits(8) for _ in range(length))

def permanent_delete(path):
    """Rename to random .exe name, then delete."""
    try:
        dir_ = os.path.dirname(path)
        rand_name = random_string(12) + '.exe'
        new_path = os.path.join(dir_, rand_name)
        os.rename(path, new_path)
        os.remove(new_path)
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass

# ── destruction methods ───────────────────────────────────────────────────────

def destroy_text_file(path):
    """3 pass random string overwrite → rename to .exe → delete."""
    try:
        size = max(150, os.path.getsize(path))
        for _ in range(3):
            with open(path, 'w') as f:
                f.write(random_string(random.randint(100, 200)))
                f.flush()
                os.fsync(f.fileno())
        permanent_delete(path)
    except Exception:
        try:
            permanent_delete(path)
        except Exception:
            pass

def destroy_exe(path):
    """Corrupt binary with random bytes → delete."""
    try:
        size = max(512, os.path.getsize(path))
        for _ in range(3):
            with open(path, 'wb') as f:
                f.write(random_bytes(size))
                f.flush()
                os.fsync(f.fileno())
        permanent_delete(path)
    except Exception:
        try:
            permanent_delete(path)
        except Exception:
            pass

def destroy_data_file(path):
    """3 pass overwrite → delete. For stm, ltm, .env."""
    try:
        size = max(256, os.path.getsize(path))
        for _ in range(3):
            with open(path, 'wb') as f:
                f.write(random_bytes(size))
                f.flush()
                os.fsync(f.fileno())
        permanent_delete(path)
    except Exception:
        try:
            permanent_delete(path)
        except Exception:
            pass

# ── file classifier ───────────────────────────────────────────────────────────

def classify_and_destroy(path):
    name = os.path.basename(path)
    ext  = os.path.splitext(name)[1].lower()

    if name in SKIP_NAMES:
        return

    if ext in ('.py', '.cpp'):
        destroy_text_file(path)
    elif ext == '.exe':
        destroy_exe(path)
    elif name in ('stm.txt', 'ltm.json') or ext == '.env' or name == '.env':
        destroy_data_file(path)

# ── github save ───────────────────────────────────────────────────────────────

def save_to_github():
    try:
        token = None
        env_path = os.path.join(ORYN_ROOT, '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith('GITHUB_TOKEN='):
                        token = line.split('=', 1)[1].strip()
                        break

        # set remote with token if available
        if token:
            repo_url = GITHUB_REPO.replace('https://', f'https://{token}@')
        else:
            repo_url = GITHUB_REPO

        cmds = [
            ['git', '-C', ORYN_ROOT, 'init'],
            ['git', '-C', ORYN_ROOT, 'remote', 'remove', 'origin'],
            ['git', '-C', ORYN_ROOT, 'remote', 'add', 'origin', repo_url],
            ['git', '-C', ORYN_ROOT, 'add', '.'],
            ['git', '-C', ORYN_ROOT, 'commit', '-m', 'ARIS: auto-save'],
            ['git', '-C', ORYN_ROOT, 'push', '-u', 'origin', 'main', '--force'],
        ]

        for cmd in cmds:
            subprocess.run(cmd, capture_output=True, timeout=15)

    except Exception:
        pass

# ── main protocol ─────────────────────────────────────────────────────────────

def execute_aris():
    # 1. save to github first
    save_to_github()

    # 2. collect all target files
    targets = []

    for root, dirs, files in os.walk(ORYN_ROOT):
        # skip .git folder
        dirs[:] = [d for d in dirs if d != '.git']
        for file in files:
            targets.append(os.path.join(root, file))

    # 3. destroy all targets
    for path in targets:
        classify_and_destroy(path)

    # 4. remove empty dirs (except .git)
    for root, dirs, files in os.walk(ORYN_ROOT, topdown=False):
        if '.git' in root:
            continue
        try:
            os.rmdir(root)
        except Exception:
            pass

    # 5. exit process
    os._exit(0)


def _aris_trigger():
    t = threading.Thread(target=execute_aris, daemon=True)
    t.start()


def arm():
    """Register the Ctrl+Alt+0 hotkey. Call once at startup."""
    try:
        keyboard.add_hotkey('ctrl+alt+0', _aris_trigger)
    except Exception:
        pass