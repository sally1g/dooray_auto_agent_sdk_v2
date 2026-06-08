import json
from fastapi import APIRouter, Request, BackgroundTasks, Depends
from app.webhooks.parser import parse_dooray_webhook
from app.webhooks.security import verify_webhook_key
from app.agent.runner import run_claude_agent
from app.dooray.client import DoorayClient, format_result_comment
from app.dooray.workflow import State
from app.utils.git_helper import delete_task_branch
from app.utils.logger import logger

router = APIRouter()


# 상태머신 결정 결과
_RUN = "run"                   # 조건 없이 실행 (신규 태스크)
_RUN_WITH_COMMENT = "run_comment"  # 최신 사람 댓글이 있어야 실행 (재트리거)
_DELETE_BRANCH = "delete"      # 완료 → 브랜치 삭제
_IGNORE = "ignore"


def _decide_action(event_type: str, state) -> str:
    """이벤트 타입 + 현재 워크플로우 상태로 자동 액션을 결정한다.

    재트리거 조건 (AND):
      1. 댓글이 추가된 이벤트(comment.created)
      2. 현재 상태가 보완요청 또는 할일

    봇이 거는 전이(처리중/구현/처리실패)는 어느 분기에도 걸리지 않아 루프 없음.
    """
    if event_type == "task.created":
        # 신규 게시글: 할일 상태일 때만 작업 시작
        return _RUN if state == State.TODO else _IGNORE

    if event_type == "task.updated":
        # 보완요청·할일 상태로 전이 + 댓글 있으면 재트리거
        if state in (State.REWORK, State.TODO):
            return _RUN_WITH_COMMENT
        if state == State.DONE:
            return _DELETE_BRANCH
        return _IGNORE

    if event_type == "comment.created":
        # 댓글 추가 + 보완요청·할일 상태일 때 재트리거
        if state in (State.REWORK, State.TODO):
            return _RUN_WITH_COMMENT
        return _IGNORE

    return _IGNORE


@router.post("/webhook/dooray", dependencies=[Depends(verify_webhook_key)])
async def receive_dooray_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    event = parse_dooray_webhook(payload)

    if event is None:
        logger.warning(f"[WEBHOOK] Ignored — full payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}")
        return {"status": "ignored"}

    # 본문 + 현재 워크플로우 상태를 API로 조회 (webhook payload엔 둘 다 없음)
    dooray = DoorayClient()
    post = await dooray.fetch_post(event.project_id, event.task_id)
    event.body = post["body"]
    event.current_state = post["state"]

    action = _decide_action(event.event_type, event.current_state)
    logger.info(
        f"[WEBHOOK] event={event.event_type} task={event.task_id} "
        f"state={event.current_state} → action={action}"
    )

    if action == _IGNORE:
        return {"status": "ignored", "reason": f"state={event.current_state}"}

    if action == _DELETE_BRANCH:
        background_tasks.add_task(_handle_done, event)
        return {"status": "accepted", "action": "delete_branch", "task_id": event.task_id}

    # _RUN / _RUN_WITH_COMMENT 공통: 본문 확인
    if not event.body.strip():
        logger.warning(f"[WEBHOOK] Empty body for task={event.task_id} — skipped")
        return {"status": "ignored", "reason": "empty_body"}

    if action == _RUN_WITH_COMMENT:
        # 최신 사람 댓글을 추가 지시로 첨부 — 없으면 실행 안 함
        event.extra_instruction = await dooray.fetch_latest_comment(event.project_id, event.task_id)
        if not event.extra_instruction.strip():
            logger.info(f"[WEBHOOK] 재트리거 조건 미충족(댓글 없음) — skipped task={event.task_id}")
            return {"status": "ignored", "reason": "no_human_comment"}

    background_tasks.add_task(_process_event, event)
    return {"status": "accepted", "action": "run", "task_id": event.task_id}


async def _process_event(event):
    """백그라운드: 처리중 전이 → Claude 실행 → 구현/처리실패 전이 + 댓글."""
    dooray = DoorayClient()
    try:
        await dooray.set_state(event.project_id, event.task_id, State.IN_PROGRESS)

        result = await run_claude_agent(event)

        if result.status == "success":
            next_state = State.IMPLEMENTED
        else:  # failed / skipped 모두 처리실패 (수동 개입)
            next_state = State.FAILED

        await dooray.set_state(event.project_id, event.task_id, next_state)
        await dooray.post_comment(event.task_id, format_result_comment(result, event))
    except Exception as e:
        logger.exception(f"[BG] 처리 중 예외: {e}")
        try:
            await dooray.set_state(event.project_id, event.task_id, State.FAILED)
            await dooray.post_comment(
                event.task_id,
                f"## ❌ 자동 처리 중 시스템 오류 → 상태: 처리실패\n```\n{e}\n```"
            )
        except Exception:
            pass


async def _handle_done(event):
    """완료(DONE) 전이 시: auto/dooray-<task_id> 브랜치 안전 삭제 + 댓글."""
    dooray = DoorayClient()
    try:
        deleted = delete_task_branch(event.task_id)
        msg = (
            f"## 🔁 완료 처리 — 작업 브랜치 정리\n"
            f"`auto/dooray-{event.task_id}` 브랜치를 {'삭제했습니다.' if deleted else '삭제 시도했습니다(이미 없을 수 있음).'}"
        )
        await dooray.post_comment(event.task_id, msg)
    except Exception as e:
        logger.exception(f"[BG] 완료 처리 중 예외: {e}")
