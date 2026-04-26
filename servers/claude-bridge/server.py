"""MCP server：把 Claude Code CLI 包成 ChatGPT (Codex) 可呼叫的工具。"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

if sys.stdout is None or sys.stderr is None:
    _log_path = Path(__file__).resolve().parent / "bridge.log"
    _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file

from mcp.server.fastmcp import FastMCP

def _resolve_claude_exe() -> str:
    """找 Claude Code CLI 執行檔。優先順序：
    1. 環境變數 CLAUDE_EXE（必須存在）
    2. %USERPROFILE%\\AppData\\Roaming\\Claude\\claude-code\\<version>\\claude.exe，
       挑版號最大的子目錄
    3. 都找不到 → 回傳預期路徑（之後 subprocess 會 FileNotFoundError，工具會回明確錯誤）
    """
    env = os.environ.get("CLAUDE_EXE")
    if env and Path(env).is_file():
        return env

    parent = Path.home() / "AppData" / "Roaming" / "Claude" / "claude-code"
    if parent.is_dir():
        candidates: list[tuple[tuple[int, ...], Path]] = []
        for sub in parent.iterdir():
            if not sub.is_dir():
                continue
            exe = sub / "claude.exe"
            if not exe.is_file():
                continue
            version_tuple = tuple(
                int(p) if p.isdigit() else 0 for p in sub.name.split(".")
            )
            candidates.append((version_tuple, exe))
        if candidates:
            candidates.sort()
            return str(candidates[-1][1])

    return env or str(parent / "claude.exe")


CLAUDE_EXE = _resolve_claude_exe()
print(f"[claude-bridge] CLAUDE_EXE resolved to: {CLAUDE_EXE}", file=sys.stderr)

_raw_dirs = os.environ.get("CLAUDE_BRIDGE_ALLOWED_DIRS", r"E:\TwStockAdvisor")
ALLOWED_ROOTS: list[Path] = [
    Path(d).resolve() for d in _raw_dirs.split(";") if d.strip()
]
DEFAULT_CWD = ALLOWED_ROOTS[0] if ALLOWED_ROOTS else Path.cwd()
TIMEOUT_SECS = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "600"))
MAX_READ_BYTES = int(os.environ.get("CLAUDE_BRIDGE_MAX_READ", "200000"))
HOST = os.environ.get("CLAUDE_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLAUDE_BRIDGE_PORT", "8000"))

mcp = FastMCP("claude-bridge", host=HOST, port=PORT)


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


@mcp.tool()
def ask_claude(
    prompt: str,
    working_dir: str | None = None,
    resume_last: bool = False,
) -> str:
    """請 Claude Code 在指定工作目錄執行一段 prompt 並回傳純文字結果。

    Args:
        prompt: 給 Claude Code 的指令或問題。
        working_dir: 絕對路徑工作目錄，必須在白名單內；省略則用預設目錄。
        resume_last: True 時加上 --continue，延續上一次 -p session 的對話。
    """
    cwd = Path(working_dir) if working_dir else DEFAULT_CWD
    safe = _check_allowed(cwd)
    if safe is None:
        return (
            f"錯誤：working_dir 不在白名單內：{cwd}\n"
            f"白名單：{[str(r) for r in ALLOWED_ROOTS]}"
        )
    if not safe.is_dir():
        return f"錯誤：working_dir 不是目錄或不存在：{safe}"

    cmd = [
        CLAUDE_EXE, "-p", prompt,
        "--model", "claude-opus-4-7",
        "--output-format", "text",
    ]
    if resume_last:
        cmd.append("--continue")

    try:
        result = subprocess.run(
            cmd,
            cwd=safe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return f"Claude 執行超時（{TIMEOUT_SECS} 秒）"
    except FileNotFoundError:
        return f"找不到 Claude 執行檔：{CLAUDE_EXE}（用環境變數 CLAUDE_EXE 覆寫）"

    output = (result.stdout or "").strip()
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return (
            f"Claude 回傳錯誤（exit {result.returncode}）\n"
            f"指令：{shlex.join(cmd)}\n"
            f"stderr：{err}\n\n"
            f"stdout：{output}"
        )
    return output or "(Claude 沒有輸出)"


@mcp.tool()
def read_file(path: str, max_bytes: int | None = None) -> str:
    """直接讀取白名單內檔案內容（不啟動 Claude，速度快、省 token）。

    Args:
        path: 絕對路徑，必須在 CLAUDE_BRIDGE_ALLOWED_DIRS 範圍內。
        max_bytes: 最多讀取位元組數，預設 MAX_READ_BYTES。
    """
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
    """列出白名單內目錄的檔案與子目錄。

    Args:
        path: 絕對路徑；省略則用預設目錄。
    """
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


@mcp.tool()
def claude_version() -> str:
    """回傳目前橋接的 Claude Code 版本與白名單，連線測試用。"""
    try:
        result = subprocess.run(
            [CLAUDE_EXE, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        ver = (result.stdout or result.stderr).strip()
    except FileNotFoundError:
        ver = f"找不到 Claude 執行檔：{CLAUDE_EXE}"
    roots = [str(r) for r in ALLOWED_ROOTS]
    return f"{ver}\n白名單：{roots}\n預設工作目錄：{DEFAULT_CWD}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
