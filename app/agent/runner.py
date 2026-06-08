"""Anthropic SDK 기반 에이전트 루프.

v1(Claude Code CLI subprocess)의 stream-json 정규식 파싱을 제거하고,
Anthropic SDK의 tool use 루프를 직접 돌린다. 최종 결과는 정규식이 아니라
에이전트가 호출하는 report_result 도구로 구조화되어 들어온다.

흐름:
  build_messages → [SDK messages.create → tool_use 실행 → 결과 append] 반복
  → report_result 호출 시 종료 → AgentResult
"""
import asyncio
from typing import Optional

import anthropic

from app.config import settings
from app.webhooks.parser import DoorayEvent
from app.agent.prompt_builder import build_system_prompt, build_user_message
from app.agent.tools import TOOL_SCHEMAS, TERMINAL_TOOL, execute_tool
from app.utils.logger import logger


class AgentResult:
    def __init__(self, status: str, modified_files: list, commit_hash: Optional[str],
                 test_result: Optional[str], summary: str, error: Optional[str], raw_output: str):
        self.status = status
        self.modified_files = modified_files
        self.commit_hash = commit_hash
        self.test_result = test_result
        self.summary = summary
        self.error = error
        self.raw_output = raw_output


# 동시 실행 방지 — 단일 target_repo 작업 디렉토리를 공유하므로
# 여러 webhook이 동시에 들어와도 한 번에 하나의 에이전트만 실행한다.
_agent_lock = asyncio.Lock()

# SDK 클라이언트 (모듈 1회 생성). API Key는 .env의 ANTHROPIC_API_KEY.
_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)


async def run_claude_agent(event: DoorayEvent) -> AgentResult:
    """에이전트 루프를 직렬화하여 실행."""
    async with _agent_lock:
        try:
            return await asyncio.wait_for(
                _run_locked(event), timeout=settings.claude_timeout_sec
            )
        except asyncio.TimeoutError:
            logger.error(f"[AGENT] 전체 타임아웃 ({settings.claude_timeout_sec}s) task={event.task_id}")
            return AgentResult("failed", [], None, None,
                               "에이전트 실행 시간 초과", "timeout", "")


async def _run_locked(event: DoorayEvent) -> AgentResult:
    prefix = f"[AGENT:{event.task_id[:8]}]"
    logger.info(f"[AGENT] Starting SDK agent for task={event.task_id} event={event.event_type}")

    # 캐시 적용: 정적 system 프롬프트 + 도구 정의는 cache_control로 캐싱
    system = [{
        "type": "text",
        "text": build_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }]
    tools = _tools_with_cache(TOOL_SCHEMAS)
    messages = [{"role": "user", "content": build_user_message(event)}]

    transcript: list[str] = []  # 디버깅/댓글용 텍스트 누적

    for turn in range(1, settings.claude_max_turns + 1):
        try:
            response = await _client.messages.create(
                model=settings.claude_model,
                max_tokens=settings.claude_max_tokens,
                system=system,
                tools=tools,
                messages=messages,
            )
        except anthropic.APIError as e:
            logger.error(f"{prefix} Anthropic API 오류: {e}")
            return AgentResult("failed", [], None, None,
                               "Anthropic API 호출 실패", str(e), "\n".join(transcript))

        messages.append({"role": "assistant", "content": response.content})

        # 어시스턴트 텍스트 로깅
        for block in response.content:
            if block.type == "text" and block.text.strip():
                line = block.text.strip().replace("\n", " ")
                logger.info(f"{prefix} 💬 {line[:120]}")
                transcript.append(block.text.strip())

        if response.stop_reason != "tool_use":
            # 도구 호출 없이 종료 — report_result 누락. 텍스트를 요약으로 사용.
            logger.warning(f"{prefix} ⚠️ report_result 없이 종료 (stop={response.stop_reason}) turn={turn}")
            return AgentResult("success", [], None, None,
                               "\n".join(transcript) or "결과 미보고 (수동 검토 필요)",
                               None, "\n".join(transcript))

        # tool_use 블록들 처리
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == TERMINAL_TOOL:
                logger.info(f"{prefix} ✅ report_result 수신 turn={turn} status={block.input.get('status')}")
                return _to_result(block.input, "\n".join(transcript))

            detail = block.input.get("command") or block.input.get("path") or block.input.get("pattern") or ""
            logger.info(f"{prefix} 🔧 {block.name}({str(detail)[:100]})")
            output = await execute_tool(block.name, block.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })

        messages.append({"role": "user", "content": tool_results})

    # max_turns 도달
    logger.warning(f"{prefix} ⚠️ max_turns({settings.claude_max_turns}) 도달 — report_result 미수신")
    return AgentResult("failed", [], None, None,
                       f"최대 턴({settings.claude_max_turns}) 초과 — 작업 미완료",
                       "max_turns_reached", "\n".join(transcript))


def _tools_with_cache(schemas: list) -> list:
    """마지막 도구에 cache_control을 붙여 도구 정의 전체를 캐싱."""
    tools = [dict(s) for s in schemas]
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _to_result(inp: dict, raw: str) -> AgentResult:
    return AgentResult(
        status=inp.get("status", "success"),
        modified_files=inp.get("modified_files", []),
        commit_hash=inp.get("commit_hash") or None,
        test_result=inp.get("test_result") or None,
        summary=inp.get("summary", ""),
        error=inp.get("error") or None,
        raw_output=raw,
    )
