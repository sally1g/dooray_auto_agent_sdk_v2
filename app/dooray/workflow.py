"""Dooray 워크플로우 상태 ↔ 시나리오 논리 상태 매핑.

7단계 상태 체계:
  1. 할일      TODO            registered  게시글 최초 등록
  2. 처리중    IN_PROGRESS     working     Claude 작업 시작
  3. 구현      IMPLEMENTED     working     Claude push 성공
  3-X. 처리실패 FAILED          working     Claude 작업 실패 / skipped
  4-A. 보완요청 REWORK          working     테스터 추가 수정 요청
  4-B. 운영배포대기 READY_TO_DEPLOY closed    테스터 검수 통과
  5. 완료      DONE            closed      운영 반영 완료 → 브랜치 삭제

상태 ID는 프로젝트마다 다르고 변경될 수 있으므로 하드코딩하지 않는다.
DoorayClient가 /workflows API로 조회한 한글 이름을 아래 매핑으로 해석한다.
"""
from enum import Enum


class State(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    IMPLEMENTED = "IMPLEMENTED"
    FAILED = "FAILED"
    REWORK = "REWORK"
    READY_TO_DEPLOY = "READY_TO_DEPLOY"
    DONE = "DONE"


# 시나리오 논리 상태 → Dooray 워크플로우 한글 이름 (project 설정과 일치해야 함)
STATE_TO_NAME = {
    State.TODO: "할 일",
    State.IN_PROGRESS: "처리중",
    State.IMPLEMENTED: "구현",
    State.FAILED: "처리실패",
    State.REWORK: "보완요청",
    State.READY_TO_DEPLOY: "운영배포대기",
    State.DONE: "완료",
}

# 한글 라벨 (댓글/로그 출력용)
STATE_LABEL = {
    State.TODO: "할일",
    State.IN_PROGRESS: "처리중",
    State.IMPLEMENTED: "구현",
    State.FAILED: "처리실패",
    State.REWORK: "보완요청",
    State.READY_TO_DEPLOY: "운영배포대기",
    State.DONE: "완료",
}


def normalize(name: str) -> str:
    """워크플로우 이름 비교용 정규화 — 공백 차이('할 일' vs '할일')를 흡수."""
    return (name or "").replace(" ", "").strip()


# 정규화된 이름 → State (역방향 조회용)
NAME_TO_STATE = {normalize(name): state for state, name in STATE_TO_NAME.items()}
