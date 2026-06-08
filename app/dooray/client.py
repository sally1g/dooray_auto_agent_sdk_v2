import httpx
from typing import Optional
from app.config import settings
from app.utils.logger import logger
from app.dooray.workflow import State, STATE_TO_NAME, STATE_LABEL, NAME_TO_STATE, normalize


class DoorayClient:
    def __init__(self):
        self.base_url = f"{settings.dooray_api_base}/project/v1"
        self.headers = {
            "Authorization": f"dooray-api {settings.dooray_api_token}",
            "Content-Type": "application/json",
        }

    # ── 게시글 조회 ─────────────────────────────────────────────
    async def fetch_post(self, project_id: str, post_id: str) -> dict:
        """게시글 본문 + 현재 워크플로우 상태를 조회한다.

        반환: {"body": str, "workflow_id": str, "state": Optional[State]}
        """
        url = f"{self.base_url}/projects/{project_id}/posts/{post_id}"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(url, headers=self.headers)
                r.raise_for_status()
                data = r.json()
                result = data.get("result") or data
                body = (result.get("body") or {}).get("content", "")
                wf = result.get("workflow") or {}
                wf_id = wf.get("id", "")
                wf_name = wf.get("name", "")
                state = NAME_TO_STATE.get(normalize(wf_name))
                logger.info(
                    f"[DOORAY] post={post_id} body_len={len(body)} "
                    f"workflow='{wf_name}'({wf_id}) state={state}"
                )
                return {"body": body, "workflow_id": wf_id, "state": state}
            except httpx.HTTPError as e:
                logger.error(f"[DOORAY] Failed to fetch post: {e}")
                return {"body": "", "workflow_id": "", "state": None}

    async def fetch_post_body(self, project_id: str, post_id: str) -> str:
        """본문만 필요할 때의 호환 래퍼."""
        return (await self.fetch_post(project_id, post_id))["body"]

    async def fetch_latest_comment(self, project_id: str, post_id: str) -> str:
        """게시글의 최신 댓글(log) 본문을 반환한다. 없으면 빈 문자열.

        보완요청/할일 재트리거 시 테스터의 추가 지시를 읽기 위해 사용.
        에이전트가 단 결과 댓글(자동 댓글)은 사람 지시가 아니므로 제외한다.
        """
        url = f"{self.base_url}/projects/{project_id}/posts/{post_id}/logs"
        params = {"page": 0, "size": 20, "order": "-createdAt"}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(url, headers=self.headers, params=params)
                r.raise_for_status()
                logs = r.json().get("result") or []
                for log in logs:  # 최신순
                    content = (log.get("body") or {}).get("content", "") or ""
                    if not content.strip():
                        continue
                    # 자동 결과 댓글(아이콘 헤더로 시작)은 건너뛴다
                    if content.lstrip().startswith(("## ✅", "## ❌", "## ⏭️", "## 🔁")):
                        continue
                    logger.info(f"[DOORAY] latest human comment len={len(content)} on post={post_id}")
                    return content
                return ""
            except httpx.HTTPError as e:
                logger.error(f"[DOORAY] Failed to fetch logs: {e}")
                return ""

    # ── 워크플로우 상태 전이 ────────────────────────────────────
    _workflow_name_to_id: Optional[dict] = None  # 클래스 캐시 {정규화이름: id}

    async def _ensure_workflows(self) -> dict:
        if DoorayClient._workflow_name_to_id is not None:
            return DoorayClient._workflow_name_to_id
        url = f"{self.base_url}/projects/{settings.dooray_project_id}/workflows"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=self.headers)
            r.raise_for_status()
            mapping = {}
            for w in r.json().get("result", []):
                mapping[normalize(w.get("name", ""))] = w.get("id")
            DoorayClient._workflow_name_to_id = mapping
            logger.info(f"[DOORAY] workflows loaded: {list(mapping.keys())}")
            return mapping

    async def set_state(self, project_id: str, post_id: str, state: State) -> bool:
        """게시글 워크플로우 상태를 전이한다. 비활성화(toggle off) 시 no-op."""
        if not settings.manage_workflow_state:
            return False
        try:
            mapping = await self._ensure_workflows()
        except httpx.HTTPError as e:
            logger.error(f"[DOORAY] workflow 목록 조회 실패: {e}")
            return False

        wf_id = mapping.get(normalize(STATE_TO_NAME[state]))
        if not wf_id:
            logger.error(f"[DOORAY] '{STATE_TO_NAME[state]}' 워크플로우 ID를 찾지 못함 — 전이 생략")
            return False

        url = f"{self.base_url}/projects/{project_id}/posts/{post_id}/set-workflow"
        payload = {"workflowId": wf_id}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.post(url, headers=self.headers, json=payload)
                r.raise_for_status()
                logger.info(f"[DOORAY] post={post_id} → {STATE_LABEL[state]}({wf_id}) 전이 완료")
                return True
            except httpx.HTTPError as e:
                body = getattr(e, "response", None)
                logger.error(
                    f"[DOORAY] 상태 전이 실패 → {STATE_LABEL[state]}: "
                    f"{getattr(body, 'status_code', '?')} {getattr(body, 'text', str(e))[:300]}"
                )
                return False

    async def post_comment(self, task_id: str, content: str) -> bool:
        url = f"{self.base_url}/projects/{settings.dooray_project_id}/posts/{task_id}/logs"
        payload = {"body": {"mimeType": "text/x-markdown", "content": content}}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.post(url, headers=self.headers, json=payload)
                logger.debug(f"[DOORAY] POST {url} → {r.status_code} {r.text[:200]}")
                r.raise_for_status()
                logger.info(f"[DOORAY] Comment posted on task={task_id}")
                return True
            except httpx.HTTPError as e:
                logger.error(f"[DOORAY] Comment post failed: {r.status_code} {r.text[:300]}")
                return False


def format_result_comment(agent_result, event) -> str:
    if agent_result.status == "success":
        icon = "✅"
        title = "자동 처리 완료 → 상태: 구현"
    elif agent_result.status == "skipped":
        icon = "❌"
        title = "처리 건너뜀 → 상태: 처리실패 (수동 개입 필요)"
    else:
        icon = "❌"
        title = "자동 처리 실패 → 상태: 처리실패"

    files = "\n".join(f"- `{f}`" for f in agent_result.modified_files) or "- (없음)"
    return f"""## {icon} {title}

**이벤트:** `{event.event_type}`
**태스크 ID:** `{event.task_id}`
**테스트 결과:** {agent_result.test_result or "N/A"}
**커밋:** `{agent_result.commit_hash or "N/A"}`
**브랜치:** `auto/dooray-{event.task_id}` → `{settings.target_beta_branch}`

### 수정 파일
{files}

### 요약
{agent_result.summary}

{f"### 오류{chr(10)}```{chr(10)}{agent_result.error}{chr(10)}```" if agent_result.error else ""}
"""
