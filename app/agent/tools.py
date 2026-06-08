"""에이전트가 사용할 도구(tool) 정의 + 실행 로직.

Claude Code CLI가 내장으로 제공하던 Bash/Read/Write/Edit/Glob/Grep을
Anthropic SDK tool use 방식으로 직접 구현한다.

설계 원칙:
- 모든 파일 작업은 target_repo 디렉토리 안으로 제한 (경로 탈출 차단)
- bash는 target_repo를 cwd로, GITHUB_TOKEN 등 인증 env를 주입해 실행
- 각 도구는 (성공/실패 무관) 항상 문자열을 반환 → tool_result content
- report_result는 종료 신호용 특수 도구 (실행 결과 없음, 루프에서 가로챔)
"""
import asyncio
import fnmatch
import os
import shutil
from app.config import settings
from app.utils.logger import logger


# ── 도구 스키마 (Anthropic tool use 포맷) ───────────────────────────
TOOL_SCHEMAS = [
    {
        "name": "bash",
        "description": (
            "target_repo 디렉토리에서 쉘 명령을 실행한다. "
            "git, npm/pnpm, npx tsc, 빌드/테스트 명령 등에 사용. "
            "stdout/stderr와 종료 코드를 반환한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "실행할 쉘 명령"},
                "timeout_sec": {
                    "type": "integer",
                    "description": f"명령 타임아웃(초). 기본 {settings.bash_timeout_sec}.",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "파일 내용을 읽어 라인 번호와 함께 반환한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "target_repo 기준 상대 경로"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "파일을 생성하거나 전체 내용을 덮어쓴다. 상위 디렉토리는 자동 생성.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "target_repo 기준 상대 경로"},
                "content": {"type": "string", "description": "파일 전체 내용"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "파일에서 old_string을 new_string으로 치환한다. "
            "old_string은 파일 내에서 유일해야 하며 정확히 일치해야 한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "target_repo 기준 상대 경로"},
                "old_string": {"type": "string", "description": "교체 대상 (정확히 일치, 유일)"},
                "new_string": {"type": "string", "description": "교체 후 문자열"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "glob",
        "description": "glob 패턴으로 파일 목록을 찾는다. 예: 'app/**/*.tsx'",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 패턴 (target_repo 기준)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "파일 내용에서 문자열/정규식을 검색한다. ripgrep(rg) 사용.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "검색할 문자열 또는 정규식"},
                "path": {"type": "string", "description": "검색 시작 경로 (기본: 전체)"},
                "glob": {"type": "string", "description": "파일 필터 (예: '*.tsx')"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "report_result",
        "description": (
            "작업을 종료하며 최종 결과를 보고한다. 모든 작업(성공/실패/건너뜀)의 "
            "마지막에 반드시 한 번 호출해야 한다. 호출 즉시 에이전트 루프가 종료된다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["success", "failed", "skipped"],
                    "description": "success=push 완료, failed=오류, skipped=파일 못찾음/범위불명확",
                },
                "modified_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "수정한 파일 경로 목록",
                },
                "commit_hash": {"type": "string", "description": "커밋 해시 (없으면 빈 문자열)"},
                "test_result": {
                    "type": "string",
                    "description": "pass | fail | N/A",
                },
                "summary": {"type": "string", "description": "작업 내용 요약 (한국어)"},
                "error": {"type": "string", "description": "실패 시 오류 내용 (없으면 빈 문자열)"},
            },
            "required": ["status", "summary"],
        },
    },
]

# report_result를 제외한 실제 실행 도구 이름
TERMINAL_TOOL = "report_result"


# ── 경로 안전성 ─────────────────────────────────────────────────────
def _safe_path(rel_path: str) -> str:
    """target_repo 내부 경로로 정규화. 디렉토리 탈출 시 ValueError."""
    repo_root = os.path.abspath(settings.target_repo_path)
    full = os.path.abspath(os.path.join(repo_root, rel_path))
    if full != repo_root and not full.startswith(repo_root + os.sep):
        raise ValueError(f"target_repo 밖의 경로 접근 거부: {rel_path}")
    return full


def _bash_env() -> dict:
    """bash 실행용 환경변수 — git/npm 인증 포함."""
    env = {**os.environ}
    if settings.github_token:
        env["GITHUB_TOKEN"] = settings.github_token
        env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("npm_config_store_dir", os.path.expanduser("~/.pnpm-store"))
    return env


# ── 도구 실행기 ─────────────────────────────────────────────────────
async def execute_tool(name: str, tool_input: dict) -> str:
    """도구 이름 + 입력을 받아 실행하고 결과 문자열을 반환한다.

    예외는 잡아서 'ERROR: ...' 문자열로 변환 — 에이전트가 보고 대응하게 한다.
    """
    try:
        if name == "bash":
            return await _tool_bash(tool_input)
        if name == "read_file":
            return _tool_read(tool_input)
        if name == "write_file":
            return _tool_write(tool_input)
        if name == "edit_file":
            return _tool_edit(tool_input)
        if name == "glob":
            return _tool_glob(tool_input)
        if name == "grep":
            return await _tool_grep(tool_input)
        return f"ERROR: 알 수 없는 도구 '{name}'"
    except Exception as e:
        logger.warning(f"[TOOL] {name} 실행 오류: {e}")
        return f"ERROR: {e}"


async def _tool_bash(inp: dict) -> str:
    command = inp["command"]
    timeout = int(inp.get("timeout_sec") or settings.bash_timeout_sec)

    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=settings.target_repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # stderr를 stdout에 합쳐 순서 보존
        env=_bash_env(),
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return f"ERROR: 명령 타임아웃 ({timeout}s) — killed: {command}"

    output = stdout.decode("utf-8", errors="replace")
    # 출력이 너무 길면 잘라서 토큰 절약 (앞뒤만 보존)
    if len(output) > 30000:
        output = output[:15000] + "\n... (중략) ...\n" + output[-15000:]
    return f"[exit={proc.returncode}]\n{output}"


def _tool_read(inp: dict) -> str:
    full = _safe_path(inp["path"])
    if not os.path.isfile(full):
        return f"ERROR: 파일 없음: {inp['path']}"
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    numbered = "".join(f"{i+1}\t{line}" for i, line in enumerate(lines))
    if len(numbered) > 50000:
        numbered = numbered[:50000] + "\n... (파일이 길어 일부만 표시) ..."
    return numbered or "(빈 파일)"


def _tool_write(inp: dict) -> str:
    full = _safe_path(inp["path"])
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(inp["content"])
    return f"파일 작성 완료: {inp['path']} ({len(inp['content'])} chars)"


def _tool_edit(inp: dict) -> str:
    full = _safe_path(inp["path"])
    if not os.path.isfile(full):
        return f"ERROR: 파일 없음: {inp['path']}"
    old, new = inp["old_string"], inp["new_string"]
    with open(full, "r", encoding="utf-8") as f:
        content = f.read()
    count = content.count(old)
    if count == 0:
        return f"ERROR: old_string을 찾지 못함: {inp['path']}"
    if count > 1:
        return f"ERROR: old_string이 {count}곳에 중복됨 — 더 길게 지정해 유일하게 만들 것: {inp['path']}"
    with open(full, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new, 1))
    return f"수정 완료: {inp['path']}"


def _tool_glob(inp: dict) -> str:
    repo_root = os.path.abspath(settings.target_repo_path)
    pattern = inp["pattern"]
    matches = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # 무거운/불필요 디렉토리 스킵
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".git", ".next", "dist", ".turbo"}]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, repo_root)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(fn, pattern):
                matches.append(rel)
    matches.sort()
    if not matches:
        return f"(일치하는 파일 없음: {pattern})"
    return "\n".join(matches[:200]) + ("" if len(matches) <= 200 else f"\n... (+{len(matches)-200}개)")


async def _tool_grep(inp: dict) -> str:
    pattern = inp["pattern"]
    path = inp.get("path") or "."
    glob_filter = inp.get("glob")

    # ripgrep 우선, 없으면 시스템 grep으로 폴백
    if shutil.which("rg"):
        cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
        if glob_filter:
            cmd += ["--glob", glob_filter]
        cmd += ["-e", pattern, path]
    else:
        cmd = ["grep", "-rn", "--color=never",
               "--exclude-dir=node_modules", "--exclude-dir=.git", "--exclude-dir=.next"]
        if glob_filter:
            cmd += [f"--include={glob_filter}"]
        cmd += ["-e", pattern, path]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=settings.target_repo_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_bash_env(),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    out = stdout.decode("utf-8", errors="replace")
    if proc.returncode == 1:  # rg/grep: 매치 없음
        return f"(일치 없음: {pattern})"
    if proc.returncode not in (0, 1):
        return f"ERROR: grep 실패: {stderr.decode('utf-8', errors='replace')}"
    if len(out) > 20000:
        out = out[:20000] + "\n... (결과가 많아 일부만 표시) ..."
    return out or "(일치 없음)"
