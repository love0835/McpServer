"""Conservative MCP bridge for a portable local Codex CLI install."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

if sys.stdout is None or sys.stderr is None:
    _log_path = Path(__file__).resolve().parent / "logs" / "bridge.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file

from mcp.server.fastmcp import FastMCP


BRIDGE_DIR = Path(__file__).resolve().parent
BRIDGE_TEMP_DIR = BRIDGE_DIR / "temp"
BRIDGE_TEMP_DIR.mkdir(parents=True, exist_ok=True)

NODE_EXE = os.environ.get("CODEX_NODE_EXE", "")
CODEX_JS_ENTRY = os.environ.get("CODEX_JS_ENTRY", "")
HOST = os.environ.get("CODEX_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODEX_BRIDGE_PORT", "8001"))
TIMEOUT_SECS = int(os.environ.get("CODEX_BRIDGE_TIMEOUT", "300"))
ENABLE_ASK = os.environ.get("CODEX_BRIDGE_ENABLE_ASK", "0") == "1"
MAX_DEPTH = int(os.environ.get("CODEX_BRIDGE_MAX_DEPTH", "1"))
MAX_READ_BYTES = int(os.environ.get("CODEX_BRIDGE_MAX_READ", "200000"))
MAX_PROMPT_CHARS = int(os.environ.get("CODEX_BRIDGE_MAX_PROMPT_CHARS", "12000"))

_raw_dirs = os.environ.get("CODEX_BRIDGE_ALLOWED_DIRS", str(BRIDGE_DIR.parent.parent))
ALLOWED_ROOTS: list[Path] = [
    Path(d).resolve() for d in _raw_dirs.split(";") if d.strip()
]
DEFAULT_CWD = ALLOWED_ROOTS[0] if ALLOWED_ROOTS else BRIDGE_DIR

_ask_lock = threading.Semaphore(1)

print(f"[codex-bridge] CODEX_NODE_EXE={NODE_EXE}", file=sys.stderr)
print(f"[codex-bridge] CODEX_JS_ENTRY={CODEX_JS_ENTRY}", file=sys.stderr)

mcp = FastMCP("codex-bridge", host=HOST, port=PORT)


def _check_allowed(p: Path) -> Path | None:
    try:
        abs_p = p.resolve()
    except OSError:
        return None
    for root in ALLOWED_ROOTS:
        try:
            abs_p.relative_to(root)
            return abs_p
        except ValueError:
            continue
    return None


def _current_depth() -> int:
    raw = os.environ.get("MCP_CALL_DEPTH", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def _codex_command(*args: str) -> list[str]:
    if not NODE_EXE or not Path(NODE_EXE).is_file():
        raise FileNotFoundError(
            f"CODEX_NODE_EXE is missing or invalid: {NODE_EXE}. Run scripts/setup-codex-cli.ps1."
        )
    if not CODEX_JS_ENTRY or not Path(CODEX_JS_ENTRY).is_file():
        raise FileNotFoundError(
            f"CODEX_JS_ENTRY is missing or invalid: {CODEX_JS_ENTRY}. Run scripts/setup-codex-cli.ps1."
        )
    return [NODE_EXE, CODEX_JS_ENTRY, *args]


def _child_env(depth: int) -> dict[str, str]:
    keep = [
        "APPDATA",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    ]
    env = {k: v for k, v in os.environ.items() if k in keep and v}
    node_dir = str(Path(NODE_EXE).resolve().parent) if NODE_EXE else ""
    system32 = str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32")
    env["PATH"] = os.pathsep.join([p for p in [node_dir, system32] if p])
    env["MCP_CALL_DEPTH"] = str(depth + 1)
    env["CODEX_BRIDGE_CHILD"] = "1"
    return env


def _audit(message: str) -> None:
    audit_path = BRIDGE_DIR / "logs" / "audit.log"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(message + "\n")


@mcp.tool()
def codex_version() -> str:
    """Return Codex CLI version, bridge settings, and safety state."""
    try:
        cmd = _codex_command("--version")
        result = subprocess.run(
            cmd,
            cwd=BRIDGE_DIR,
            env=_child_env(0),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        version = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            version = f"Codex version check failed (exit {result.returncode}): {version}"
    except (FileNotFoundError, OSError) as e:
        version = str(e)

    return (
        f"{version}\n"
        f"ask_enabled: {ENABLE_ASK}\n"
        f"allowed_roots: {[str(r) for r in ALLOWED_ROOTS]}\n"
        f"default_cwd: {DEFAULT_CWD}\n"
        f"max_depth: {MAX_DEPTH}\n"
        f"max_prompt_chars: {MAX_PROMPT_CHARS}"
    )


@mcp.tool()
def ask_codex(
    prompt: str,
    working_dir: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    """Ask local Codex CLI through `codex exec`.

    Disabled by default. Enable with CODEX_BRIDGE_ENABLE_ASK=1 only after the
    portable CLI setup and security policy have been reviewed.
    """
    if not ENABLE_ASK:
        return "ask_codex is disabled. Set CODEX_BRIDGE_ENABLE_ASK=1 only after review."

    if len(prompt) > MAX_PROMPT_CHARS:
        return f"Prompt too large: {len(prompt)} chars exceeds {MAX_PROMPT_CHARS}."

    if not _ask_lock.acquire(blocking=False):
        return "ask_codex is busy. Only one Codex child process is allowed at a time."

    try:
        depth = _current_depth()
        if depth >= MAX_DEPTH:
            return f"Refusing nested call: MCP_CALL_DEPTH={depth}, max_depth={MAX_DEPTH}."

        cwd = Path(working_dir) if working_dir else DEFAULT_CWD
        safe = _check_allowed(cwd)
        if safe is None:
            return f"working_dir is outside allowed roots: {cwd}. allowed_roots={[str(r) for r in ALLOWED_ROOTS]}"
        if not safe.is_dir():
            return f"working_dir does not exist or is not a directory: {safe}"

        output_fd, output_path = tempfile.mkstemp(prefix="codex-output-", suffix=".txt", dir=BRIDGE_TEMP_DIR)
        os.close(output_fd)
        output_file = Path(output_path)

        cmd = _codex_command(
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(safe),
            "--output-last-message",
            str(output_file),
        )
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(prompt)

        _audit(f"ask_codex cwd={safe} chars={len(prompt)} cmd={shlex.join(cmd[:-1])} <prompt>")

        result = subprocess.run(
            cmd,
            cwd=safe,
            env=_child_env(depth),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return f"Codex timed out after {TIMEOUT_SECS} seconds."
    except (FileNotFoundError, OSError) as e:
        return f"Codex execution failed: {e}"
    finally:
        try:
            _ask_lock.release()
        except ValueError:
            pass

    output = (result.stdout or "").strip()
    if output_file.is_file():
        try:
            final_message = output_file.read_text(encoding="utf-8", errors="replace").strip()
            if final_message:
                output = final_message
        except OSError:
            pass
        try:
            output_file.unlink()
        except OSError:
            pass

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return (
            f"Codex failed (exit {result.returncode})\n"
            f"command: {shlex.join(cmd[:-1])} <prompt>\n"
            f"stderr: {err}\n\n"
            f"stdout: {output}"
        )
    return output or "(Codex produced no output)"


@mcp.tool()
def read_file(path: str, max_bytes: int | None = None) -> str:
    """Read a whitelisted file without launching Codex."""
    target = _check_allowed(Path(path))
    if target is None:
        return f"path is outside allowed roots: {path}"
    if not target.is_file():
        return f"not a file or does not exist: {target}"

    limit = max_bytes if (max_bytes and max_bytes > 0) else MAX_READ_BYTES
    size = target.stat().st_size
    try:
        data = target.read_bytes()[:limit]
    except OSError as e:
        return f"read failed: {e}"
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text += f"\n\n(truncated: file is {size} bytes, showing first {limit})"
    return text


@mcp.tool()
def list_dir(path: str | None = None) -> str:
    """List a whitelisted directory."""
    raw = Path(path) if path else DEFAULT_CWD
    target = _check_allowed(raw)
    if target is None:
        return f"path is outside allowed roots: {raw}"
    if not target.is_dir():
        return f"not a directory or does not exist: {target}"

    rows: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        kind = "DIR " if entry.is_dir() else "FILE"
        try:
            size = entry.stat().st_size if entry.is_file() else "-"
        except OSError:
            size = "?"
        rows.append(f"{kind}  {entry.name}  ({size})")
    return "\n".join(rows) or "(empty directory)"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
