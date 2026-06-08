from typing import Literal, Optional
from pydantic import BaseModel
from app.dooray.workflow import State


# Dooray webhookType → 내부 이벤트 타입 매핑
_EVENT_MAP = {
    "postCreated":          "task.created",
    "postUpdated":          "task.updated",
    "postWorkflowChanged":  "task.updated",    # 상태(워크플로우) 변경
    "postCommentCreated":   "comment.created", # 사용자가 댓글 작성
    "postCommentUpdated":   "comment.updated",
    "logCreated":           "comment.created", # 구버전 호환
    "logUpdated":           "comment.updated",
}


class DoorayEvent(BaseModel):
    """정규화된 Dooray 이벤트 데이터."""
    event_type: Literal["task.created", "task.updated", "comment.created", "comment.updated"]
    task_id: str
    project_id: str
    title: Optional[str] = None
    body: str = ""          # webhook payload에 없으므로 API로 별도 조회 후 채움
    author: Optional[str] = None
    current_state: Optional[State] = None  # API 조회로 채우는 현재 워크플로우 상태
    extra_instruction: str = ""            # 재트리거 시 테스터 최신 댓글
    raw: dict


def parse_dooray_webhook(payload: dict) -> Optional[DoorayEvent]:
    """
    실제 Dooray Webhook v2 페이로드를 DoorayEvent로 변환.
    지원하지 않는 이벤트는 None 반환.

    실제 Dooray 페이로드 구조:
    {
      "webhookType": "postCreated",   ← 이벤트 타입
      "hookEventType": "postCreated", ← 동일 (중복 필드)
      "post": { "id": "...", "subject": "..." },  ← 태스크 정보 (body 없음)
      "project": { "id": "..." },
      "source": { "member": { "name": "..." } }   ← 작성자
    }
    """
    raw_type = (
        payload.get("webhookType")
        or payload.get("hookEventType")
        or payload.get("event")
        or payload.get("type")
    )
    event_type = _EVENT_MAP.get(raw_type)
    if event_type is None:
        return None

    post    = payload.get("post") or {}
    log     = payload.get("log") or {}
    project = payload.get("project") or {}
    source  = payload.get("source") or {}
    author  = (source.get("member") or {}).get("name")

    if event_type.startswith("task."):
        task_id = post.get("id", "")
        title   = post.get("subject")
    else:  # comment.*
        # 댓글 이벤트: 상위 post의 id를 task_id로 사용
        task_id = (log.get("post") or {}).get("id", "") or post.get("id", "")
        title   = None

    return DoorayEvent(
        event_type=event_type,
        task_id=task_id,
        project_id=project.get("id", ""),
        title=title,
        body="",    # router에서 API 조회 후 채움
        author=author,
        raw=payload,
    )
