"""에이전트 프롬프트 빌더 (SDK tool use 버전).

v1과 달리 프롬프트를 두 부분으로 분리한다:
  - build_system_prompt(): 모든 요청에 동일한 정적 지침 → prompt caching 대상
  - build_user_message(): 요청마다 달라지는 태스크 내용 + git 절차

CLI 버전의 "stdout 마지막 줄 JSON" 규약은 제거됐다. 결과는 report_result
도구 호출로 보고한다.

또한 target_repo에 `CLAUDE.md` / `.claude/` 가 있으면 그 내용을 system 프롬프트에
주입하여 프로젝트별 규칙을 우선 적용한다 (Claude Code CLI가 자동으로 읽어주던
프로젝트 컨텍스트를 SDK 방식에서도 동일하게 살리기 위함).
"""
from pathlib import Path
from typing import Iterable

from app.webhooks.parser import DoorayEvent
from app.config import settings
from app.utils.logger import logger


SYSTEM_INSTRUCTION = """당신은 Next.js 프로젝트의 자동화 개발 에이전트다.
비개발자가 작성한 화면 기반 수정 요청을 분석하여 소스코드를 수정하고, 테스트 후 지정 브랜치로 배포한다.

사용 가능한 도구:
- bash: target_repo에서 쉘 명령 실행 (git, npm/pnpm, npx tsc 등)
- read_file / write_file / edit_file: 파일 읽기/쓰기/부분수정
- glob: 파일 경로 패턴 검색
- grep: 파일 내용 검색
- report_result: 작업 종료 + 최종 결과 보고 (반드시 마지막에 1회 호출)

작업 원칙:
1. 요청자는 파일 경로를 모른다. URL·메뉴 경로·화면에 보이는 텍스트만 제공한다.
2. 아래 파일 탐색 절차를 반드시 수행하여 수정 대상 파일을 직접 찾아낸다.
3. 파일을 찾지 못하거나 수정 범위가 불명확하면 추측하지 말고 report_result(status=skipped)로 종료한다.
4. 수정 후 반드시 빌드/타입체크를 수행한다.
5. 모든 검증을 통과해야 push 한다.
6. 작업이 끝나면 성공/실패/건너뜀 무관하게 반드시 report_result를 호출한다.

절대 금지 사항 (위반 시 즉시 작업 중단):
- main 브랜치에 직접 push 또는 merge 절대 금지
- --force push 절대 금지
- .env, .env.local 파일 커밋 금지

베타 배포 정책 (2026~):
- 베타 배포 트리거는 **`DEV_` prefix 태그 push** 다.
  `develop`/베타 브랜치에 commit push 만으로는 배포가 시작되지 않는다.
- 태그 규칙: `DEV_<snake_case_title>_YYYYMMDDHHMMSS`
- snake_case_title 은 누적된 commit 메시지를 분석해 AI가 산출한다 (자세한 절차는
  user 메시지의 4단계 참조).
"""


URL_TO_FILE_GUIDE = """
## Next.js App Router — URL → 파일 경로 추론 규칙

URL 경로를 아래 규칙으로 변환하여 후보 파일 목록을 생성한다.

| URL 예시 | 우선 탐색 파일 |
|----------|--------------|
| / | app/page.tsx, app/layout.tsx |
| /jobs | app/jobs/page.tsx, app/jobs/layout.tsx |
| /jobs/list | app/jobs/list/page.tsx |
| /jobs/123 (동적) | app/jobs/[id]/page.tsx, app/jobs/[slug]/page.tsx |
| GNB·푸터 등 공통 영역 | app/layout.tsx, components/Header/*, components/Footer/* |
| 모달·팝업 | components/ 하위 grep 탐색 |

⚠️ page.tsx가 직접 렌더링하지 않고 하위 컴포넌트에 위임하는 경우가 많으므로
   텍스트 grep 탐색을 반드시 병행한다.
"""


DISCOVERY_STEPS = """
## 파일 탐색 절차 (A → B → C 순서로 반드시 수행)

### [A] URL → 라우트 파일 후보 추출
1. 요청의 "어느 화면인가요?" 항목에서 URL 경로 추출
2. 위 추론 규칙에 따라 후보 파일 목록 생성
3. glob 도구로 파일 존재 확인 (예: glob "app/**/page.tsx")

### [B] 보이는 텍스트로 실제 파일 검색 (핵심 단계)
"지금 어떻게 보이나요?" 항목의 텍스트를 grep으로 검색한다.
- grep(pattern="보이는텍스트", glob="*.tsx")
- 결과가 여러 개면 URL 경로·메뉴 경로와 가장 관련 있는 파일 선택
- 한글 텍스트가 i18n/상수 파일로 분리된 경우 *.json, *.ts 도 검색

### [C] 컴포넌트 트리 추적 (A·B로 파일을 특정 못한 경우)
1. [A]에서 찾은 page.tsx를 read_file로 읽어 import된 컴포넌트 파악
2. 각 컴포넌트 파일을 열어 "화면 어디에 있나요?" 설명과 일치하는 요소 탐색
3. layout.tsx 확인 — GNB·사이드바·푸터 등 공통 요소는 여기에 있음
4. 탐색 깊이는 최대 3단계(page → component → sub-component)까지

### [D] 수정 대상 확정 후 최소 변경
- 찾은 파일과 라인을 특정한 뒤 edit_file로 수정
- 요청된 부분에만 변경 — 주변 코드·스타일 건드리지 않음
- 파일을 끝내 특정하지 못하면 수정하지 않고 report_result(status=skipped)
"""


# ── target_repo 프로젝트 컨텍스트 로딩 ──────────────────────────────
# Claude Code CLI는 .claude/ + CLAUDE.md를 자동 로드한다. SDK 방식에는 그 기능이
# 없으므로 직접 읽어 system 프롬프트에 주입한다.

# 한 파일당 최대 길이(문자). 너무 큰 문서는 잘라서 토큰 낭비 방지.
_MAX_FILE_CHARS = 30_000
# 프로젝트 컨텍스트 전체 합산 상한. 시스템 프롬프트가 비대해지면 캐시 적중률·
# 응답 속도가 모두 떨어지므로 보수적으로 잡는다.
_MAX_TOTAL_CHARS = 120_000

# 우선순위가 명시된 단일 파일 후보. 존재하는 것만 순서대로 포함.
_PRIORITY_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    ".claude/CLAUDE.md",
    ".claude/settings.json",
)

# 패턴 매칭으로 가져올 디렉토리들. (디렉토리, 파일 확장자 화이트리스트)
_DIR_GLOBS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (".claude/rules", (".md",)),
    (".claude/agents", (".md",)),
    (".claude/skills", (".md",)),
)


def _repo_root() -> Path:
    return Path(settings.target_repo_path).resolve()


def _read_clipped(path: Path) -> str:
    """파일을 읽되 길이 제한을 적용. 실패 시 빈 문자열."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[PROMPT] {path} 읽기 실패: {e}")
        return ""
    if len(text) > _MAX_FILE_CHARS:
        text = text[:_MAX_FILE_CHARS] + "\n\n... (이 파일이 너무 커서 일부만 표시됨) ..."
    return text


def _iter_dir_files(root: Path, rel_dir: str, exts: tuple[str, ...]) -> Iterable[Path]:
    base = root / rel_dir
    if not base.is_dir():
        return []
    files = [p for p in sorted(base.rglob("*")) if p.is_file() and p.suffix.lower() in exts]
    return files


def _load_target_project_context() -> str:
    """target_repo 의 CLAUDE.md + .claude/ 문서를 읽어 system 주입용 문자열로 변환.

    - 매 요청마다 다시 읽는다 (git pull 후 최신 규칙이 즉시 반영되도록).
    - 파일 부재 시 조용히 빈 문자열 반환.
    - 전체 합산 한도(_MAX_TOTAL_CHARS)에 도달하면 그 시점에서 멈춘다.
    """
    root = _repo_root()
    if not root.is_dir():
        return ""

    sections: list[str] = []
    total = 0

    def _push(rel_path: str, body: str) -> bool:
        """남은 한도 안에서 섹션 추가. 한도 초과면 False 반환(중단 신호)."""
        nonlocal total
        body = body.strip()
        if not body:
            return True
        header = f"### `{rel_path}`"
        chunk = f"{header}\n\n{body}\n"
        if total + len(chunk) > _MAX_TOTAL_CHARS:
            sections.append(
                f"\n... (target_repo 프로젝트 문서가 한도({_MAX_TOTAL_CHARS} chars)를 "
                f"초과해 일부만 포함됨) ...\n"
            )
            return False
        sections.append(chunk)
        total += len(chunk)
        return True

    # 1) 우선순위 단일 파일
    for rel in _PRIORITY_FILES:
        f = root / rel
        if not f.is_file():
            continue
        body = _read_clipped(f)
        if not _push(rel, body):
            return _wrap(sections)

    # 2) 디렉토리 단위 — rules/agents/skills
    for rel_dir, exts in _DIR_GLOBS:
        for f in _iter_dir_files(root, rel_dir, exts):
            rel = f.relative_to(root).as_posix()
            body = _read_clipped(f)
            if not _push(rel, body):
                return _wrap(sections)

    return _wrap(sections)


def _wrap(sections: list[str]) -> str:
    """모은 섹션들을 system 프롬프트용 블록으로 감싼다."""
    if not sections:
        return ""
    body = "\n".join(sections)
    return (
        "\n## 프로젝트 고유 규칙 (target_repo `.claude/` + `CLAUDE.md`)\n"
        "아래 문서들은 이 저장소가 제공하는 프로젝트별 규약·가드레일·아키텍처 메모다.\n"
        "**위 일반 지침과 충돌할 경우 이 문서들이 우선한다.** 코드 수정·검증·커밋·\n"
        "푸시 전 반드시 이 규칙을 준수했는지 점검하라.\n"
        "(주의: `.claude/settings.json` 의 hooks/plugins 항목은 Claude Code CLI 전용\n"
        "설정이라 본 SDK 에이전트가 직접 실행하지는 않는다. 단, 그 의도(예: force push\n"
        "차단, type-check 자동 실행)는 작업 시 동등하게 준수한다.)\n\n"
        f"{body}\n"
    )


def build_system_prompt() -> str:
    """모든 요청에 동일한 정적 지침 + target_repo 프로젝트 컨텍스트.

    프로젝트 컨텍스트는 매 요청마다 다시 읽는다. 내용이 바뀌지 않은 동안에는
    동일한 system 프롬프트가 되어 prompt caching이 그대로 적중하고, 규칙이
    수정되면 그 다음 요청부터 즉시 반영된다.
    """
    base = f"{SYSTEM_INSTRUCTION}\n{URL_TO_FILE_GUIDE}\n{DISCOVERY_STEPS}"
    project_ctx = _load_target_project_context()
    if project_ctx:
        return f"{base}\n{project_ctx}"
    return base


def build_user_message(event: DoorayEvent) -> str:
    """요청별 동적 내용 — 태스크 본문 + git 배포 절차."""
    beta_branch = settings.target_beta_branch
    task_id = event.task_id

    rework_section = ""
    extra = (getattr(event, "extra_instruction", "") or "").strip()
    if extra:
        rework_section = f"""
## ⚠️ 재작업 추가 지시 (테스터 최신 코멘트)
이 작업은 보완요청/재시도로 재트리거되었다. 아래 코멘트의 추가 요구사항을
**기존 요청 내용보다 우선하여** 반영하라. 기존 작업 브랜치(auto/dooray-{task_id})를
재사용하여 이어서 수정한다.

{extra}
"""

    return f"""## 수신된 Dooray 수정 요청
- 이벤트 타입: {event.event_type}
- 태스크 ID: {task_id}
- 제목: {event.title or "(댓글 이벤트)"}
- 작성자: {event.author or "Unknown"}

## 요청 내용 (비개발자 작성)
{event.body}
{rework_section}

## 수행 절차

### 1단계 — 프로젝트 구조 파악
bash로 `ls -la`, `cat package.json`, glob로 주요 파일 목록 확인.

### 2단계 — 파일 탐색
시스템 지침의 파일 탐색 절차(A → B → C)를 수행한다.

### 3단계 — 검증 (있는 스크립트만 실행, 없으면 생략)
먼저 root package.json의 scripts를 확인하여 실제로 존재하는 명령만 실행한다.
npx 사용 금지 — 항상 pnpm을 사용한다 (monorepo 환경).

```
# scripts 확인
cat package.json | grep -A 30 '"scripts"'

# 타입체크 (type-check 또는 typecheck 스크립트가 있으면)
pnpm run type-check 2>/dev/null || pnpm run typecheck 2>/dev/null || echo "type-check 스크립트 없음 — 생략"

# 린트 (lint 스크립트가 있으면)
pnpm run lint 2>/dev/null || echo "lint 스크립트 없음 — 생략"
```

스크립트가 없거나 실패해도 push 단계로 진행한다. 단, 명백한 타입 오류가 있으면 report_result(status=failed).

### 4단계 — Git Push + 태그 기반 베타 배포 (검증 모두 통과 시)

⚠️ 기준 브랜치는 `main`이 아니라 **`{beta_branch}`** 다. main은 절대 건드리지 않는다.
⚠️ **deploy 정책(2026~)**: `{beta_branch}` 브랜치에 commit push 만으로는 베타 배포가
   트리거되지 않는다. `DEV_` prefix 태그를 push 해야 `deploy-tenant-app-beta`
   워크플로우가 실행되어 ECS `gangnam2026-beta` 로 반영된다.

#### 4-1) 작업 브랜치에 변경사항 commit
```
git checkout auto/dooray-{task_id} 2>/dev/null || git checkout -b auto/dooray-{task_id}
git add -A
git commit -m "dooray({task_id}): {event.event_type} 자동 반영"
```

#### 4-2) {beta_branch} 기준 rebase + push
```
# 작업 브랜치를 최신 {beta_branch} 기준으로 rebase
git fetch origin {beta_branch}
git rebase origin/{beta_branch} || {{ git rebase --abort; echo "REBASE_CONFLICT"; }}
git push -u origin auto/dooray-{task_id}

# {beta_branch} 로 병합 + push (이 시점에는 아직 배포 안 됨 — 태그만 트리거)
git checkout {beta_branch}
git pull --rebase origin {beta_branch}
git merge --no-ff auto/dooray-{task_id}
git push origin {beta_branch}
```

#### 4-3) 작업명 자동 생성 (영문 snake_case) — **AI가 직접 판단**
방금 {beta_branch}에 추가한 commit 메시지 목록을 읽어 핵심 작업명을 산출한다.
```
# {beta_branch} push 직후 origin reflog 또는 머지 직전 SHA 기준으로 신규 커밋만 추출
git log --pretty=format:"%s" -n 20
# (또는) git log {beta_branch}@{{1}}..{beta_branch} --pretty=format:"%s"
```

규칙:
- **영문 소문자 + underscore (snake_case)** — 예: `user_login_bugfix`, `payment_api_refactor`,
  `landing_hero_copy_fix`, `admin_company_filter_add`
- 50자 이내
- 한글 commit 메시지여도 핵심 의미를 영문으로 의역
- 누적 commit이 여러 개면 **가장 핵심적인 변경**을 대표하는 단일 명칭으로 요약
  (예: 버그수정 위주 → `*_bugfix`, 신규 기능 → `*_add`, 리팩토링 → `*_refactor`)
- 영숫자/언더스코어 외 문자 금지 — `[a-z0-9_]+` 만 허용

산출한 값을 환경변수 TITLE 로 둔다.
```
TITLE="<여기에 AI가 산출한 snake_case 작업명>"
```

#### 4-4) 태그 생성 + push (= 베타 배포 실제 트리거)
```
TAG="DEV_${{TITLE}}_$(date +%Y%m%d%H%M%S)"
echo "DEPLOY_TAG=$TAG"
git tag -a "$TAG" -m "auto deploy: {task_id} - $TITLE"
git push origin "$TAG"
```

태그 push 후 GitHub Actions 의 `deploy-tenant-app-beta` 가 실행된다.
이 시점에 에이전트 작업은 종료 — 배포 완료 여부는 별도 워크플로우가 처리한다.
생성한 TAG 값은 report_result 의 summary 에 반드시 포함시킨다 (예: "배포 태그: DEV_xxx_20260605091500").

### 5단계 — 완료 보고
작업 결과를 report_result 도구로 보고한다. status는
success(push 완료) / failed(오류) / skipped(파일 못찾음·범위불명확) 중 하나.
modified_files, commit_hash, test_result, summary, error를 채운다.
"""
