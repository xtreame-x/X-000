import os
import shutil
import fnmatch
import hashlib
import difflib
import re
from datetime import datetime
from pathlib import Path

ORYN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# ── utils ─────────────────────────────────────────────────────────────────────

def _size_fmt(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _safe_path(path):
    """Expand ~ and env vars, resolve to absolute."""
    expanded = Path(os.path.expandvars(os.path.expanduser(path)))
    if not expanded.is_absolute():
        cwd_resolved = expanded.resolve()
        if not cwd_resolved.exists() and (Path(ORYN_ROOT) / expanded).exists():
            return str((Path(ORYN_ROOT) / expanded).resolve())
        return str(cwd_resolved)
    return str(expanded.resolve())


# ── LEVEL 1 — Navigation ──────────────────────────────────────────────────────

def list_dir(path='.', show_hidden=False):
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"path not found: {path}"

    items = []
    try:
        for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
            if not show_hidden and entry.name.startswith('.'):
                continue
            if entry.is_dir():
                items.append(f"  \033[34m{entry.name}/\033[0m")
            else:
                size = _size_fmt(entry.stat().st_size)
                items.append(f"  {entry.name:<40} {size}")
    except PermissionError:
        return f"permission denied: {path}"

    header = f"\033[90m{path}\033[0m  ({len(items)} items)"
    return header + '\n' + '\n'.join(items)


def read_file(path, max_lines=100):
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"file not found: {path}"
    if os.path.getsize(path) > 5 * 1024 * 1024:
        return "file too large to read (>5MB)"

    try:
        with open(path, 'r', errors='replace') as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            shown = ''.join(lines[:max_lines])
            return shown + f"\n\033[90m... {len(lines) - max_lines} more lines\033[0m"
        return ''.join(lines)
    except Exception as e:
        return f"error reading file: {e}"


def find_files(pattern, search_path=None, file_type=None, max_results=50):
    """Find files by name pattern and optional extension."""
    base = _safe_path(search_path) if search_path else os.path.expanduser('~')
    results = []

    try:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                if file_type and not f.lower().endswith(file_type.lower()):
                    continue
                if fnmatch.fnmatch(f.lower(), pattern.lower()):
                    full = os.path.join(root, f)
                    size = _size_fmt(os.path.getsize(full))
                    results.append((full, size))
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
    except Exception:
        pass

    if not results:
        return f"no files found matching '{pattern}'"

    lines = [f"\033[90mfound {len(results)} file(s):\033[0m"]
    for path, size in results:
        lines.append(f"  {path}  \033[90m{size}\033[0m")
    return '\n'.join(lines)


# ── LEVEL 2 — Manipulation ────────────────────────────────────────────────────

def create_file(path, content=''):
    path = _safe_path(path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return f"created: {path}"
    except Exception as e:
        return f"error: {e}"


def delete_file(path):
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"not found: {path}"
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            return f"deleted directory: {path}"
        os.remove(path)
        return f"deleted: {path}"
    except Exception as e:
        return f"error: {e}"


def move_file(src, dst):
    src = _safe_path(src)
    dst = _safe_path(dst)
    try:
        shutil.move(src, dst)
        return f"moved: {src} -> {dst}"
    except Exception as e:
        return f"error: {e}"


def copy_file(src, dst):
    src = _safe_path(src)
    dst = _safe_path(dst)
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return f"copied: {src} -> {dst}"
    except Exception as e:
        return f"error: {e}"


def rename_file(path, new_name):
    path = _safe_path(path)
    new_path = os.path.join(os.path.dirname(path), new_name)
    try:
        os.rename(path, new_path)
        return f"renamed: {os.path.basename(path)} -> {new_name}"
    except Exception as e:
        return f"error: {e}"


def open_file(path):
    """Open a file with the OS default application."""
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"file not found: {path}"
    try:
        os.startfile(path)
        return f"opened: {path}"
    except Exception as e:
        return f"error opening file: {e}"


def batch_rename(folder, pattern, prefix, start=1):
    """Rename all files matching pattern to prefix_001, prefix_002..."""
    folder = _safe_path(folder)
    if not os.path.isdir(folder):
        return f"not a directory: {folder}"

    files = sorted([
        f for f in os.listdir(folder)
        if fnmatch.fnmatch(f.lower(), pattern.lower())
    ])

    if not files:
        return f"no files matching '{pattern}' in {folder}"

    renamed = []
    for i, f in enumerate(files, start=start):
        ext = os.path.splitext(f)[1]
        new_name = f"{prefix}_{i:03d}{ext}"
        try:
            os.rename(
                os.path.join(folder, f),
                os.path.join(folder, new_name)
            )
            renamed.append(f"  {f} -> {new_name}")
        except Exception as e:
            renamed.append(f"  {f} -> error: {e}")

    return f"batch renamed {len(renamed)} files:\n" + '\n'.join(renamed)


# ── LEVEL 3 — Intelligence ────────────────────────────────────────────────────

def find_largest(search_path=None, top_n=10):
    base = _safe_path(search_path) if search_path else os.path.expanduser('~')
    files = []

    try:
        for root, dirs, filenames in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules',)]
            for f in filenames:
                full = os.path.join(root, f)
                try:
                    files.append((os.path.getsize(full), full))
                except Exception:
                    pass
    except Exception:
        pass

    files.sort(reverse=True)
    top = files[:top_n]

    lines = [f"\033[90mtop {top_n} largest files:\033[0m"]
    for size, path in top:
        lines.append(f"  {_size_fmt(size):<10}  {path}")
    return '\n'.join(lines)


def find_duplicates(search_path=None):
    base = _safe_path(search_path) if search_path else os.path.expanduser('~')
    hashes = {}

    try:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules',)]
            for f in files:
                full = os.path.join(root, f)
                try:
                    if os.path.getsize(full) == 0:
                        continue
                    h = hashlib.md5(open(full, 'rb').read(65536)).hexdigest()
                    hashes.setdefault(h, []).append(full)
                except Exception:
                    pass
    except Exception:
        pass

    dupes = {h: paths for h, paths in hashes.items() if len(paths) > 1}
    if not dupes:
        return "no duplicate files found"

    lines = [f"\033[90mfound {len(dupes)} duplicate group(s):\033[0m"]
    for h, paths in list(dupes.items())[:20]:
        lines.append(f"\n  \033[90m[{h[:8]}]\033[0m")
        for p in paths:
            lines.append(f"    {p}")
    return '\n'.join(lines)


def clean_temp():
    temp_dirs = [
        os.environ.get('TEMP', ''),
        os.environ.get('TMP', ''),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp'),
    ]
    cleaned = 0
    freed = 0

    for d in temp_dirs:
        if not d or not os.path.isdir(d):
            continue
        for item in os.scandir(d):
            try:
                size = item.stat().st_size
                if item.is_dir():
                    shutil.rmtree(item.path, ignore_errors=True)
                else:
                    os.remove(item.path)
                freed += size
                cleaned += 1
            except Exception:
                pass

    return f"cleaned {cleaned} temp items, freed {_size_fmt(freed)}"


def find_recent(hours=24, search_path=None):
    base = _safe_path(search_path) if search_path else os.path.expanduser('~')
    import time
    cutoff = time.time() - (hours * 3600)
    results = []

    try:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                full = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(full)
                    if mtime > cutoff:
                        results.append((mtime, full))
                except Exception:
                    pass
    except Exception:
        pass

    results.sort(reverse=True)
    results = results[:30]

    if not results:
        return f"no files modified in the last {hours}h"

    lines = [f"\033[90mfiles modified in last {hours}h ({len(results)} found):\033[0m"]
    for mtime, path in results:
        dt = datetime.fromtimestamp(mtime).strftime('%H:%M:%S')
        lines.append(f"  {dt}  {path}")
    return '\n'.join(lines)


# ── LEVEL 4 — Content ─────────────────────────────────────────────────────────

def search_in_files(keyword, search_path=None, ext_filter=None, max_results=20):
    """Search for keyword inside file contents."""
    base = _safe_path(search_path) if search_path else os.path.expanduser('~')
    matches = []

    try:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__')]
            for f in files:
                if ext_filter and not f.lower().endswith(ext_filter.lower()):
                    continue
                full = os.path.join(root, f)
                try:
                    if os.path.getsize(full) > 2 * 1024 * 1024:
                        continue
                    with open(full, 'r', errors='replace') as fh:
                        for lineno, line in enumerate(fh, 1):
                            if keyword.lower() in line.lower():
                                matches.append((full, lineno, line.strip()))
                                if len(matches) >= max_results:
                                    break
                except Exception:
                    pass
                if len(matches) >= max_results:
                    break
    except Exception:
        pass

    if not matches:
        return f"'{keyword}' not found in any files"

    lines = [f"\033[90mfound '{keyword}' in {len(matches)} location(s):\033[0m"]
    for path, lineno, line in matches:
        lines.append(f"\n  \033[97m{path}:{lineno}\033[0m")
        lines.append(f"  \033[90m{line[:120]}\033[0m")
    return '\n'.join(lines)


def edit_file_line(path, line_number, new_content):
    """Replace a specific line in a file."""
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"file not found: {path}"

    try:
        with open(path, 'r', errors='replace') as f:
            lines = f.readlines()

        if line_number < 1 or line_number > len(lines):
            return f"line {line_number} out of range (file has {len(lines)} lines)"

        old = lines[line_number - 1].rstrip()
        lines[line_number - 1] = new_content + '\n'

        with open(path, 'w') as f:
            f.writelines(lines)

        return f"line {line_number} updated\nbefore: {old}\nafter:  {new_content}"
    except Exception as e:
        return f"error: {e}"


def summarize_file(path):
    """Return basic stats about a file."""
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"file not found: {path}"

    stat = os.stat(path)
    size = _size_fmt(stat.st_size)
    modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    ext = os.path.splitext(path)[1]

    info = [
        f"  path      {path}",
        f"  size      {size}",
        f"  modified  {modified}",
        f"  type      {ext or 'no extension'}",
    ]

    if stat.st_size < 2 * 1024 * 1024:
        try:
            with open(path, 'r', errors='replace') as f:
                content = f.read()
            lines = content.count('\n') + 1
            words = len(content.split())
            info.append(f"  lines     {lines}")
            info.append(f"  words     {words}")
        except Exception:
            pass

    return '\n'.join(info)


# ── LEVEL 5 — Smart Context Reading ───────────────────────────────────────

def read_file_context(path, target_line=None, pad=10, max_size=5*1024*1024):
    """Read a file with line numbers for AI context.

    If target_line given, show that line +/- pad lines.
    If not, show first pad lines.
    Returns (context_text, total_lines) or (error_msg, 0) on failure.
    """
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"file not found: {path}", 0
    if os.path.getsize(path) > max_size:
        return "file too large", 0
    try:
        with open(path, 'r', errors='replace') as f:
            all_lines = f.readlines()
    except Exception as e:
        return f"error: {e}", 0

    total = len(all_lines)

    if target_line:
        start = max(0, target_line - pad - 1)
        end = min(total, target_line + pad)
    else:
        start = 0
        end = min(total, pad)

    lines = []
    for i in range(start, end):
        lineno = i + 1
        marker = ">" if target_line and lineno == target_line else " "
        lines.append(f"{marker}{lineno:>5}| {all_lines[i].rstrip()}")

    header = f"{path} ({total} lines)"
    if total > end:
        lines.append(f"... ({total - end} more lines)")

    return header + "\n" + "\n".join(lines), total


def file_toc(path, max_items=30):
    """Extract function/class signatures as a compact index.

    Returns a string like:
      path/to/file.py (342 lines)
        L10:  class Foo:
        L15:    def bar(self):
        L42:  def helper():
    """
    path = _safe_path(path)
    if not os.path.exists(path):
        return f"file not found: {path}"
    try:
        with open(path, 'r', errors='replace') as f:
            lines = f.readlines()
    except Exception as e:
        return f"error: {e}"

    total = len(lines)
    entries = []
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if re.match(r'^\s*(def |class |async def |@\w+)', stripped):
            entries.append((i + 1, stripped))

    if not entries:
        return f"{path} ({total} lines, no signatures found)"

    result = [f"{path} ({total} lines)"]
    for lineno, sig in entries[:max_items]:
        indent = "  " * (len(sig) - len(sig.lstrip())) if sig.lstrip().startswith(('def ', 'class ')) else ""
        result.append(f"  L{lineno}:{indent}{sig.strip()}")
    if len(entries) > max_items:
        result.append(f"  ... (+{len(entries) - max_items} more)")
    return "\n".join(result)


def estimate_tokens(text):
    """Rough token estimate for code (average ~4 chars/token)."""
    return len(text) // 4


# ── LEVEL 6 — Search/Replace Matching Engine ────────────────────────────

def _search_replace_exact(content, search_text, replace_text):
    """Level 1: exact string match. Returns new_content or None."""
    count = content.count(search_text)
    if count == 0:
        return None
    if count > 1:
        return None  # ambiguous
    return content.replace(search_text, replace_text, 1)


def _search_replace_whitespace_flexible(content, search_text, replace_text):
    """Level 2: line-by-line comparison ignoring leading/trailing whitespace."""
    content_lines = content.splitlines(keepends=True)
    search_lines = search_text.splitlines()
    replace_lines = replace_text.splitlines()

    # Strip each line for comparison
    search_stripped = [l.rstrip('\n\r').strip() for l in search_lines]
    replace_stripped = [l.rstrip('\n\r').strip() for l in replace_lines]

    # Find matching block
    for start in range(len(content_lines) - len(search_lines) + 1):
        match = True
        for i in range(len(search_lines)):
            ci = content_lines[start + i].rstrip('\n\r').strip()
            si = search_stripped[i]
            if ci != si:
                match = False
                break
        if match:
            # Found match — build new content
            new_lines = content_lines[:start]
            for rl in replace_lines:
                new_lines.append(rl.rstrip('\n\r') + '\n')
            new_lines.extend(content_lines[start + len(search_lines):])
            return "".join(new_lines)
    return None


def _search_replace_fuzzy(content, search_text, replace_text, threshold=0.6):
    """Level 3: fuzzy match using character-level SequenceMatcher."""
    content_lines = content.splitlines(keepends=True)
    search_lines = search_text.splitlines(keepends=False)
    replace_lines = replace_text.splitlines(keepends=False)

    n = len(search_lines)
    if n == 0:
        return None

    # Normalize by stripping whitespace for comparison
    search_norm = [l.strip() for l in search_lines]
    content_norm = [l.strip() for l in content_lines]

    if n > len(content_norm):
        return None

    best_ratio = 0
    best_start = 0
    search_concat = '\n'.join(search_norm)

    for i in range(len(content_norm) - n + 1):
        chunk = content_norm[i:i + n]
        chunk_concat = '\n'.join(chunk)
        ratio = difflib.SequenceMatcher(None, search_concat, chunk_concat).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    if best_ratio < threshold:
        return None

    # Apply replacement at best_start
    new_lines = content_lines[:best_start]
    for rl in replace_lines:
        new_lines.append(rl + '\n')
    new_lines.extend(content_lines[best_start + n:])
    return "".join(new_lines)


def apply_search_replace(path, search_text, replace_text):
    """Apply a SEARCH/REPLACE block to a file.

    Tries exact → whitespace-flexible → fuzzy matching.
    Returns (result_msg, success_bool).
    """
    path = _safe_path(path)
    is_new = not os.path.exists(path)

    # New file creation
    if is_new and not search_text.strip():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(replace_text)
            return f"created: {path}", True
        except Exception as e:
            return f"error creating {path}: {e}", False

    if is_new:
        return f"file not found: {path}", False

    # Read existing file
    try:
        with open(path, 'r', errors='replace') as f:
            content = f.read()
    except Exception as e:
        return f"error reading {path}: {e}", False

    # Append mode (empty search on existing file)
    if not search_text.strip():
        try:
            with open(path, 'a') as f:
                f.write(replace_text)
            return f"appended to: {path}", True
        except Exception as e:
            return f"error appending to {path}: {e}", False

    # Try match levels in order
    new_content = _search_replace_exact(content, search_text, replace_text)
    level = "exact"

    if new_content is None:
        new_content = _search_replace_whitespace_flexible(content, search_text, replace_text)
        level = "whitespace-flexible"

    if new_content is None:
        new_content = _search_replace_fuzzy(content, search_text, replace_text)
        level = "fuzzy"

    if new_content is None:
        # Show closest match for debugging
        close = difflib.get_close_matches(
            search_text.strip()[:80],
            content.split('\n'),
            n=1,
            cutoff=0.3
        )
        hint = f"\n  closest line in file: {close[0][:80]}" if close else ""
        return f"SEARCH block did not match any content in {path}{hint}", False

    # Write back
    try:
        with open(path, 'w') as f:
            f.write(new_content)
        return f"updated: {path} (match: {level})", True
    except Exception as e:
        return f"error writing {path}: {e}", False


# ── LEVEL 7 — Search/Replace Block Parser ───────────────────────────────

def parse_search_replace_blocks(text):
    """Parse AI response into list of (path, search_text, replace_text).

    Format expected:
      path/to/file.py
      <<<<<<< SEARCH
      old content
      =======
      new content
      >>>>>>> REPLACE

    Returns list of (path, search, replace) tuples.
    """
    blocks = []
    lines = text.splitlines(keepends=True)
    i = 0
    n = len(lines)
    current_path = None

    while i < n:
        line = lines[i].rstrip('\n\r')

        # Detect path line: non-empty, not a delimiter, looks like a file path
        if (line.strip()
                and not line.startswith('<<<<<<<')
                and not line.startswith('=======')
                and not line.startswith('>>>>>>>')
                and not line.startswith('```')
                and ('.' in line or '/' in line or '\\' in line)):
            current_path = line.strip().strip('`*')  # strip fence remnants
            i += 1
            continue

        # Detect SEARCH start
        if line.lstrip().startswith('<<<<<<<') and 'SEARCH' in line:
            search_lines = []
            i += 1
            while i < n and not lines[i].lstrip().startswith('======='):
                search_lines.append(lines[i])
                i += 1
            if i >= n:
                break  # malformed, stop
            i += 1  # skip =======
            replace_lines = []
            while i < n and not lines[i].lstrip().startswith('>>>>>>>'):
                replace_lines.append(lines[i])
                i += 1
            if i >= n:
                break
            i += 1  # skip >>>>>>>

            search_text = "".join(search_lines).strip('\n')
            replace_text = "".join(replace_lines).strip('\n')

            if current_path:
                blocks.append((current_path, search_text, replace_text))
            continue

        i += 1

    return blocks


def handle_search_replace_blocks(text):
    """Parse and apply all SEARCH/REPLACE blocks from AI response.

    Returns (summary_string, success_count, fail_count).
    """
    blocks = parse_search_replace_blocks(text)
    results = []
    ok = 0
    fail = 0
    for path, search, replace in blocks:
        msg, success = apply_search_replace(path, search, replace)
        results.append(msg)
        if success:
            ok += 1
        else:
            fail += 1
    summary = f"{ok} block(s) applied"
    if fail:
        summary += f", {fail} failed"
    summary += "\n" + "\n".join(results)
    return summary, ok, fail


# ── dispatcher ────────────────────────────────────────────────────────────────

def handle_file_op(op_string):
    """
    Parse and execute a FILE_OP signal.
    Format: operation|arg1|arg2|...
    """
    parts = [p.strip() for p in op_string.split('|')]
    op = parts[0].lower()
    argv = parts[1:] + [''] * 5  # pad so index access is safe

    ops = {
        'list':         lambda: list_dir(argv[0] if argv[0] else '.'),
        'read':         lambda: read_file(argv[0]),
        'read_context': lambda: read_file_context(argv[0], int(argv[1]) if argv[1] else None, int(argv[2]) if len(argv) > 2 and argv[2] else 10)[0],
        'toc':          lambda: file_toc(argv[0]),
        'find':         lambda: find_files(argv[0], argv[1] if len(argv) > 1 else None),
        'create':       lambda: create_file(argv[0], argv[1] if len(argv) > 1 else ''),
        'open':         lambda: open_file(argv[0]),
        'delete':       lambda: delete_file(argv[0]),
        'move':         lambda: move_file(argv[0], argv[1]),
        'copy':         lambda: copy_file(argv[0], argv[1]),
        'rename':       lambda: rename_file(argv[0], argv[1]),
        'append':       lambda: create_file(argv[0], '\n' + argv[1] if len(argv) > 1 else ''),
        'batch_rename': lambda: batch_rename(argv[0], argv[1], argv[2]),
        'largest':      lambda: find_largest(argv[0] if argv[0] else None),
        'duplicates':   lambda: find_duplicates(argv[0] if argv[0] else None),
        'clean_temp':   lambda: clean_temp(),
        'recent':       lambda: find_recent(int(argv[0]) if argv[0] else 24),
        'search':       lambda: search_in_files(argv[0], argv[1] if len(argv) > 1 else None),
        'edit_line':    lambda: edit_file_line(argv[0], int(argv[1]), argv[2]),
        'summarize':    lambda: summarize_file(argv[0]),
        'apply_blocks': lambda: handle_search_replace_blocks(argv[0])[0],
    }

    if op not in ops:
        return f"unknown file op: {op}"

    try:
        return ops[op]()
    except Exception as e:
        return f"file op error: {e}"
    