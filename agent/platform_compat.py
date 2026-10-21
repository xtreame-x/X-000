"""The single place where operating-system differences live.

The agent is developed on Linux but runs on Windows, so every OS-specific
behaviour is funnelled through this module and made testable by injecting the
platform name.  Nothing else in the codebase may branch on ``os.name``.

Windows hazards handled here:

* ``bash_tool`` -> pwsh / powershell / cmd selection with UTF-8 console forcing
* process-tree termination via ``taskkill /T`` instead of process groups
* ``os.replace`` failing with "Access is denied" while a file is being read by
  an editor or antivirus scanner -> retry with backoff
* CRLF source files making exact-match patching silently fail
* reserved device names, trailing dots/spaces, illegal characters, MAX_PATH
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"

#: Characters Windows forbids anywhere in a path component.
WINDOWS_ILLEGAL_CHARS = set('<>:"|?*')
#: Characters Windows forbids in a *file name* (colon is legal only as drive sep).
WINDOWS_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
WINDOWS_MAX_PATH = 260
_TASK_KILL_TIMEOUT = 10


def get_platform() -> str:
    """Indirection so tests can simulate the host platform."""
    return "windows" if IS_WINDOWS else "posix"


# ---------------------------------------------------------------------------
# Shell selection
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ShellSpec:
    """How to turn a command string into an argv the OS will execute."""

    name: str
    executable: str

    def argv(self, command: str) -> list[str]:
        if self.name == "cmd":
            # Force the console code page to UTF-8 so non-ASCII file names and
            # content survive the round trip.
            return [self.executable, "/d", "/s", "/c", f"chcp 65001>nul & {command}"]
        if self.name in {"pwsh", "powershell"}:
            preamble = (
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
                "$OutputEncoding=[System.Text.UTF8Encoding]::new();"
            )
            return [
                self.executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                preamble + command,
            ]
        return [self.executable, "-lc", command]


def select_shell(platform: str | None = None, which=shutil.which) -> ShellSpec:
    """Pick the best available shell for ``platform``.

    Windows preference order mirrors what a developer machine actually has:
    PowerShell 7 (``pwsh``) > Windows PowerShell > ``cmd``.  An explicit
    ``AGENT_SHELL`` is honoured by the caller, not here.
    """
    platform = (platform or get_platform()).lower()
    if platform in {"windows", "win32", "nt"}:
        for candidate in ("pwsh", "powershell"):
            path = which(candidate)
            if path:
                return ShellSpec(candidate, path)
        comspec = os.environ.get("COMSPEC") or which("cmd") or "cmd.exe"
        return ShellSpec("cmd", comspec)

    for candidate in ("bash", "sh"):
        path = which(candidate)
        if path:
            return ShellSpec(candidate, path)
    return ShellSpec("sh", "/bin/sh")


def default_shell(platform: str | None = None) -> str:
    """Return the shell *name* used by :class:`agent.config.Settings`."""
    return select_shell(platform).name


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------
@dataclass
class CommandResult:
    command: str
    shell: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "shell": self.shell,
            "exitCode": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "durationSeconds": round(self.duration_s, 4),
            "timedOut": self.timed_out,
        }


def _decode(raw: bytes, platform: str) -> str:
    """Decode subprocess bytes without ever raising on legacy code pages."""
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if platform.startswith("win"):
        # cmd.exe on a non-UTF-8 console emits the OEM code page (cp437/cp850).
        for encoding in ("oem", "cp1252"):
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
    return raw.decode("utf-8", errors="replace")


def kill_process_tree(pid: int, platform: str | None = None) -> None:
    """Terminate ``pid`` and its children.

    Windows has no process groups in the POSIX sense; ``taskkill /T`` is the
    supported way to take down a tree started by ``cmd``/``pwsh``.
    """
    platform = (platform or get_platform()).lower()
    if platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def run_command(
    command: str,
    *,
    cwd: str | Path | None = None,
    timeout: int = 120,
    shell: str | None = None,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> CommandResult:
    """Run ``command`` through the platform shell and capture output."""
    platform = (platform or get_platform()).lower()
    spec = select_shell(platform) if shell in (None, "") else _resolve_shell(shell)
    argv = spec.argv(command)
    started = time.monotonic()
    popen_kwargs: dict = {
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "env": {**os.environ, **(env or {})},
    }
    if platform.startswith("win"):
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(argv, **popen_kwargs)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_tree(proc.pid, platform)
        try:
            out, err = proc.communicate(timeout=_TASK_KILL_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = b"", b""

    return CommandResult(
        command=command,
        shell=spec.name,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=_decode(out or b"", platform),
        stderr=_decode(err or b"", platform),
        duration_s=time.monotonic() - started,
        timed_out=timed_out,
    )


def _resolve_shell(shell: str) -> ShellSpec:
    lowered = shell.lower()
    if Path(lowered).stem in {"pwsh", "powershell", "cmd"}:
        return ShellSpec(Path(lowered).stem, shell)
    if lowered in {"bash", "sh", "zsh"}:
        return ShellSpec(lowered, shutil.which(lowered) or lowered)
    return ShellSpec(lowered, shell)


# ---------------------------------------------------------------------------
# Line endings
# ---------------------------------------------------------------------------
def detect_newline(text: str) -> str:
    """Return the dominant newline sequence of ``text`` (``\\r\\n`` or ``\\n``)."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    if crlf > lf:
        return "\r\n"
    return "\n"


def normalize_newlines(text: str) -> str:
    """Normalise CRLF/CR to LF for internal processing."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def to_newline(text: str, newline: str) -> str:
    """Apply ``newline`` to LF-normalised ``text``."""
    if newline == "\r\n":
        return text.replace("\r\n", "\n").replace("\n", "\r\n")
    return normalize_newlines(text)


def read_text_normalized(path: str | Path) -> tuple[str, str]:
    """Read a text file as ``(lf_text, newline_style)``.

    Newlines are normalised so exact-match edits keep working on files written
    by Windows editors, while the original style is returned so a write can
    restore it.
    """
    raw = Path(path).read_bytes()
    if b"\x00" in raw[:8192]:
        raise ValueError(f"{path} looks like a binary file")
    text = raw.decode("utf-8-sig", errors="replace")
    return normalize_newlines(text), detect_newline(text)


# ---------------------------------------------------------------------------
# Atomic writes with Windows-friendly retries
# ---------------------------------------------------------------------------
_RETRYABLE_WINERRORS = {5, 32, 33}  # ACCESS_DENIED, SHARING_VIOLATION, LOCK_VIOLATION


def _is_retryable(exc: PermissionError) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror in _RETRYABLE_WINERRORS:
        return True
    return exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY}


def atomic_write(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
    retries: int = 6,
    backoff: float = 0.05,
) -> Path:
    """Write ``text`` to ``path`` atomically, retrying transient Windows locks.

    The temporary file is created in the destination directory so ``os.replace``
    stays a same-volume rename.  ``os.replace`` is retried because on Windows it
    raises ``PermissionError`` while another process (editor, indexer, antivirus)
    holds the target open.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = to_newline(text, newline) if newline else normalize_newlines(text)
    data = payload.encode(encoding)

    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp_path, target, retries=retries, backoff=backoff)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def _replace_with_retry(src: Path, dst: Path, *, retries: int, backoff: float) -> None:
    last: PermissionError | None = None
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:  # pragma: no cover - platform dependent
            if not _is_retryable(exc):
                raise
            last = exc
            time.sleep(backoff * (2**attempt))
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------
class InvalidPath(ValueError):
    """Raised when a path is not valid for the target platform."""


def validate_windows_path(path: str | Path, *, max_path: int = WINDOWS_MAX_PATH) -> None:
    """Reject paths Windows will refuse or silently mishandle.

    Checks per component: illegal characters, reserved device names (including
    ``CON.txt``), trailing dots/spaces, empty components; plus total length.
    """
    text = str(path)
    if len(text) >= max_path:
        raise InvalidPath(
            f"path is {len(text)} characters, over the Windows MAX_PATH limit of {max_path}: {text}"
        )
    if "\x00" in text:
        raise InvalidPath("path contains a NUL byte")

    stripped = text
    if len(text) >= 2 and text[1] == ":":
        stripped = text[2:]
    stripped = stripped.replace("/", "\\")

    for part in stripped.split("\\"):
        if part in {"", "."}:
            continue
        if part == "..":
            continue
        bad = sorted(WINDOWS_ILLEGAL_CHARS.intersection(part))
        if bad:
            raise InvalidPath(f"path component {part!r} contains illegal characters {bad}")
        if part != part.rstrip(" ."):
            raise InvalidPath(f"path component {part!r} ends with a space or dot")
        stem = part.split(".")[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise InvalidPath(f"path component {part!r} uses the reserved Windows name {stem}")


def validate_path(path: str | Path, *, windows: bool | None = None) -> None:
    """Validate ``path`` against the *target* platform's rules."""
    if windows is None:
        windows = IS_WINDOWS
    if windows:
        validate_windows_path(path)


def resolved(path: str | Path, *, base: Path | None = None) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute() and base is not None:
        p = base / p
    return Path(os.path.abspath(p))


def is_within(child: str | Path, parent: str | Path, *, windows: bool | None = None) -> bool:
    """True when ``child`` is inside ``parent`` (case-insensitive on Windows)."""
    if windows is None:
        windows = IS_WINDOWS
    c = os.path.normcase(str(resolved(child)))
    p = os.path.normcase(str(resolved(parent)))
    if c == p:
        return True
    return c.startswith(p.rstrip(os.sep) + os.sep)


def safe_join(base: Path, relative: str, *, windows: bool | None = None, escape_ok: bool = False) -> Path:
    """Join ``relative`` onto ``base``, rejecting traversal and invalid names."""
    validate_path(relative, windows=windows)
    candidate = resolved(relative, base=base)
    if not escape_ok and not is_within(candidate, base, windows=windows):
        raise InvalidPath(f"path escapes the workspace: {relative}")
    return candidate


def resolve_workspace_path(workspace: Path, path: str | Path) -> Path:
    """Resolve ``path`` against ``workspace`` and validate it for this OS.

    Relative paths are workspace-relative; absolute paths are honoured and
    validated.  Containment is checked by the caller so the policy (and its
    override) lives in one place.
    """
    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        candidate = Path(workspace) / candidate
    validate_windows_path(candidate) if IS_WINDOWS else None
    return resolved(candidate)


def slugify(text: str, *, max_length: int = 60) -> str:
    """Filesystem-safe slug that is valid on Windows too."""
    value = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    value = value[:max_length].strip("-") or "untitled"
    if value.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
        value = f"_{value}"
    return value


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def python_executable() -> str:
    return sys.executable


def is_binary_bytes(data: bytes) -> bool:
    return b"\x00" in data[:8192]
