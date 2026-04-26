"""Conservative MCP bridge for local Codex CLI experiments.

The first version intentionally keeps actual Codex prompting disabled unless
CODEX_BRIDGE_ENABLE_ASK=1 is set. This prevents accidental Codex -> MCP ->
Codex recursion while still giving Manager something real to supervise.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

if sys.stdout is None or sys.stderr is None:
    _log_path = Path(__file__).resolve().parent / "logs" / "bridge.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file

from mcp.server.fastmcp import FastMCP


def _resolve_codex_exe() -> str:
    env = os.environ.get("CODEX_EXE")
    if env and Path(env).is_file():
        return env
    found = shutil.which("codex")
    return found or env or "codex"


CODEX_EXE = _resolve_codex_exe()
HOST = os.environ.get("CODEX_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODEX_BRIDGE_PORT", "8001"))
TIMEOUT_SECS = int(os.environ.get("CODEX_BRIDGE_TIMEOUT", "300"))
ENABLE_ASK = os.environ.get("CODEX_BRIDGE_ENABLE_ASK", "0") == "1"
MAX_DEPTH = int(os.environ.get("CODEX_BRIDGE_MAX_DEPTH", "1"))
MAX_READ_BYTES = int(os.environ.get("CODEX_BRIDGE_MAX_READ", "200000"))

_raw_dirs = os.environ.get("CODEX_BRIDGE_ALLOWED_DIRS", r"E:\McpServer")
ALLOWED_ROOTS: list[Path] = [
    Path(d).resolve() for d in _raw_dirs.split(";") if d.strip()
]
DEFAULT_CWD = ALLOWED_ROOTS[0] if ALLOWED_ROOTS else Path.cwd()

print(f"[codex-bridge] CODEX_EXE resolved to: {CODEX_EXE}", file=sys.stderr)

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


@mcp.tool()
def codex_version() -> str:
    """Return Codex CLI version, bridge settings, and safety state."""
    try:
        result = subprocess.run(
            [CODEX_EXE, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        version = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            version = f"Codex version check failed (exit {result.returncode}): {version}"
    except FileNotFoundError:
        version = f"找不到 Codex 執行檔：{CODEX_EXE}（可用 CODEX_EXE 覆寫）"
    except OSError as e:
        version = f"Codex 執行失敗：{e}"

    return (
        f"{version}\n"
        f"ask_enabled：{ENABLE_ASK}\n"
        f"白名單：{[str(r) for r in ALLOWED_ROOTS]}\n"
        f"預設工作目錄：{DEFAULT_CWD}\n"
        f"max_depth：{MAX_DEPTH}"
    )


@mcp.tool()
def ask_codex(
    prompt: str,
    working_dir: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    """Ask local Codex CLI.

    First version is disabled by default. Set CODEX_BRIDGE_ENABLE_ASK=1 only
    after confirming the local Codex CLI can run safely from subprocess.
    """
    if not ENABLE_ASK:
        return (
            "ask_codex 目前預設停用。若要啟用，請先確認 Codex CLI 可由 subprocess "
            "穩定執行，然後設定 CODEX_BRIDGE_ENABLE_ASK=1。"
        )

    depth = _current_depth()
    if depth >= MAX_DEPTH:
        return f"拒絕執行：MCP_CALL_DEPTH={depth} 已達上限 {MAX_DEPTH}，避免巢狀 Codex 呼叫。"

    cwd = Path(working_dir) if working_dir else DEFAULT_CWD
    safe = _check_allowed(cwd)
    if safe is None:
        return (
            f"錯誤：working_dir 不在白名單內：{cwd}\n"
            f"白名單：{[str(r) for r in ALLOWED_ROOTS]}"
        )
    if not safe.is_dir():
        return f"錯誤：working_dir 不是目錄或不存在：{safe}"

    # CLI syntax is intentionally configurable while the local install is being verified.
    cmd = [CODEX_EXE]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(prompt)

    env = os.environ.copy()
    env["MCP_CALL_DEPTH"] = str(depth + 1)
    env["CODEX_BRIDGE_CHILD"] = "1"

    try:
        result = subprocess.run(
            cmd,
            cwd=safe,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return f"Codex 執行超時（{TIMEOUT_SECS} 秒）"
    except FileNotFoundError:
        return f"找不到 Codex 執行檔：{CODEX_EXE}（可用 CODEX_EXE 覆寫）"
    except OSError as e:
        return f"Codex 執行失敗：{e}"

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return (
            f"Codex 回傳錯誤（exit {result.returncode}）\n"
            f"指令：{shlex.join(cmd)}\n"
            f"stderr：{err}\n\n"
            f"stdout：{output}"
        )
    return output or "(Codex 沒有輸出)"


@mcp.tool()
def read_file(path: str, max_bytes: int | None = None) -> str:
    """Read a whitelisted file without launching Codex."""
    target = _check_allowed(Path(path))
    if target is None:
        return f"錯誤：path 不在白名單內：{path}"
    if not target.is_file():
        return f"錯誤：不是檔案或不存在：{target}"

    limit = max_bytes if (max_bytes and max_bytes > 0) else MAX_READ_BYTES
    size = target.stat().st_size
    try:
        data = target.read_bytes()[:limit]
    except OSError as e:
        return f"讀檔失敗：{e}"
    text = data.decode("utf-8", errors="replace")
    if size > limit:
        text += f"\n\n（已截斷，原檔 {size} bytes，僅顯示前 {limit}）"
    return text


@mcp.tool()
def list_dir(path: str | None = None) -> str:
    """List a whitelisted directory."""
    raw = Path(path) if path else DEFAULT_CWD
    target = _check_allowed(raw)
    if target is None:
        return f"錯誤：path 不在白名單內：{raw}"
    if not target.is_dir():
        return f"錯誤：不是目錄或不存在：{target}"

    rows: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        kind = "DIR " if entry.is_dir() else "FILE"
        try:
            size = entry.stat().st_size if entry.is_file() else "-"
        except OSError:
            size = "?"
        rows.append(f"{kind}  {entry.name}  ({size})")
    return "\n".join(rows) or "(空目錄)"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
