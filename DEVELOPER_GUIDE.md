# Dooray Claude Agent v2 개발자 가이드

## 목차
1. [프로젝트 개요](#프로젝트-개요)
2. [아키텍처](#아키텍처)
3. [프로세스 흐름](#프로세스-흐름)
4. [주요 컴포넌트](#주요-컴포넌트)
5. [환경 설정](#환경-설정)
6. [개발 가이드](#개발-가이드)
7. [배포 및 운영](#배포-및-운영)
8. [트러블슈팅](#트러블슈팅)

---

## 프로젝트 개요

### 목적
Dooray 프로젝트 관리 도구의 웹훅을 수신하여, 비개발자가 작성한 화면 수정 요청을 분석하고 Next.js 코드를 자동으로 수정/테스트/배포하는 AI 에이전트 시스템입니다.

### v1 대비 주요 변경사항
| 구분 | v1 | v2 (현재) |
|------|----|----|
| 에이전트 실행 방식 | Claude Code CLI subprocess | Anthropic SDK tool use 루프 |
| 도구 구현 | CLI 내장 | 직접 구현 (`app/agent/tools.py`) |
| 결과 수집 | stdout JSON 정규식 파싱 | `report_result` 도구 호출 (구조화) |
| 인증 | API Key 또는 OAuth | API Key 전용 |
| 안정성 | subprocess 파싱 불안정성 | SDK 직접 제어로 안정성 향상 |

### 핵심 기술 스택
- **언어**: Python 3.10+
- **웹 프레임워크**: FastAPI
- **AI SDK**: Anthropic SDK (Claude)
- **API 클라이언트**: httpx
- **로깅**: loguru
- **테스트**: pytest, pytest-asyncio

---

## 아키텍처

### 시스템 구성도

```
┌─────────────────┐
│   Dooray 웹훅   │
│  (POST 요청)    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   FastAPI 서버 (app/main.py)            │
│   - 웹훅 라우터                          │
│   - 헬스체크 엔드포인트                  │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   웹훅 처리 계층 (app/webhooks/)         │
│   - security.py: 요청 검증              │
│   - parser.py: 이벤트 파싱              │
│   - router.py: 상태머신 & 액션 결정    │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   Dooray API 계층 (app/dooray/)         │
│   - client.py: API 통신                 │
│   - workflow.py: 상태 매핑              │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   에이전트 실행 계층 (app/agent/)        │
│   - runner.py: SDK 루프 제어            │
│   - prompt_builder.py: 프롬프트 생성    │
│   - tools.py: 도구 정의 & 실행          │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   유틸리티 계층 (app/utils/)             │
│   - git_helper.py: Git 작업             │
│   - logger.py: 로깅 설정                │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   target_repo (Next.js 프로젝트)         │
│   - 코드 수정 작업 공간                  │
└─────────────────────────────────────────┘
```

### 디렉토리 구조

```
dooray_claude_agent_v2/
├── app/
│   ├── agent/              # AI 에이전트 코어
│   │   ├── runner.py       # SDK 루프 실행기
│   │   ├── tools.py        # 도구 정의 및 실행
│   │   └── prompt_builder.py  # 프롬프트 생성
│   ├── dooray/             # Dooray API 연동
│   │   ├── client.py       # API 클라이언트
│   │   └── workflow.py     # 워크플로우 상태 관리
│   ├── webhooks/           # 웹훅 처리
│   │   ├── router.py       # 라우팅 & 상태머신
│   │   ├── parser.py       # 이벤트 파싱
│   │   └── security.py     # 인증/보안
│   ├── utils/              # 공통 유틸리티
│   │   ├── git_helper.py   # Git 작업 헬퍼
│   │   └── logger.py       # 로깅 설정
│   ├── config.py           # 환경 변수 관리
│   └── main.py             # FastAPI 앱 진입점
├── tests/                  # 테스트 코드
│   └── fixtures/           # 테스트 데이터
├── logs/                   # 로그 파일 디렉토리
├── target_repo/            # 작업 대상 Next.js 프로젝트
├── .env                    # 환경 변수 (비공개)
├── .env.example            # 환경 변수 예시
├── requirements.txt        # Python 의존성
├── Dockerfile              # 도커 이미지 정의
├── docker-compose.yml      # 도커 컴포즈 설정
├── README.md               # 프로젝트 소개
├── CLAUDE.md               # Claude 에이전트 규칙
└── DEVELOPER_GUIDE.md      # 이 문서
```

---

## 프로세스 흐름

### 1. 전체 워크플로우

```
[Dooray 웹훅 수신]
     ↓
[보안 검증] (webhook_secret)
     ↓
[이벤트 파싱] (DoorayEvent 생성)
     ↓
[게시글 본문 & 상태 조회] (Dooray API)
     ↓
[상태머신 액션 결정]
     ├── IGNORE: 종료
     ├── DELETE_BRANCH: 브랜치 삭제 (완료 상태)
     └── RUN/RUN_WITH_COMMENT: 에이전트 실행
          ↓
     [상태 전이: 할일 → 처리중]
          ↓
     [Claude 에이전트 실행]
          ├── 파일 탐색 (glob, grep, read_file)
          ├── 코드 수정 (write_file, edit_file)
          ├── 검증 (bash: tsc, lint)
          ├── Git 작업 (bash: commit, push, merge)
          └── report_result 호출
          ↓
     [상태 전이: 처리중 → 구현/처리실패]
          ↓
     [결과 댓글 작성]
```

### 2. 상태머신 (워크플로우)

시스템은 7단계 상태 체계를 사용합니다:

| 상태 코드 | 한글 이름 | Dooray 워크플로우 | 설명 |
|-----------|----------|------------------|------|
| `TODO` | 할일 | "할 일" | 게시글 최초 등록 |
| `IN_PROGRESS` | 처리중 | "처리중" | Claude 작업 시작 |
| `IMPLEMENTED` | 구현 | "구현" | Claude push 성공 |
| `FAILED` | 처리실패 | "처리실패" | Claude 작업 실패/건너뜀 |
| `REWORK` | 보완요청 | "보완요청" | 테스터 추가 수정 요청 |
| `READY_TO_DEPLOY` | 운영배포대기 | "운영배포대기" | 테스터 검수 통과 |
| `DONE` | 완료 | "완료" | 운영 배포 완료 |

### 3. 이벤트 타입별 액션 결정

**`_decide_action()` 함수 로직** (`app/webhooks/router.py`):

#### A. `task.created` (게시글 생성)
- 현재 상태가 `TODO`면 → `RUN` (즉시 실행)
- 그 외 → `IGNORE`

#### B. `task.updated` (게시글 업데이트/상태 변경)
- 현재 상태가 `REWORK` 또는 `TODO`면 → `RUN_WITH_COMMENT` (댓글 필요)
- 현재 상태가 `DONE`이면 → `DELETE_BRANCH` (브랜치 정리)
- 그 외 → `IGNORE`

#### C. `comment.created` (댓글 추가)
- 현재 상태가 `REWORK` 또는 `TODO`면 → `RUN_WITH_COMMENT`
- 그 외 → `IGNORE`

#### 주요 규칙
- **`RUN_WITH_COMMENT`**: 최신 사람 댓글이 있어야만 실행 (없으면 `IGNORE`)
- **봇 자동 댓글 제외**: `## ✅`, `## ❌`, `## ⏭️`, `## 🔁`로 시작하는 댓글은 무시
- **무한 루프 방지**: 봇이 거는 상태 전이(`처리중`, `구현`, `처리실패`)는 어느 조건에도 걸리지 않음

---

## 주요 컴포넌트

### 1. 웹훅 처리 (`app/webhooks/`)

#### `router.py`
**핵심 엔드포인트**: `POST /webhook/dooray?key=<secret>`

**주요 함수**:
- `receive_dooray_webhook()`: 웹훅 수신 및 액션 결정
- `_decide_action()`: 상태머신 로직
- `_process_event()`: 백그라운드 작업 (에이전트 실행 + 상태 전이 + 댓글)
- `_handle_done()`: 완료 처리 (브랜치 삭제)

**특징**:
- BackgroundTasks를 사용하여 비동기 처리 (웹훅 응답은 즉시 반환)
- 에이전트 실행 중 예외 발생 시 자동으로 `FAILED` 상태로 전환

#### `parser.py`
Dooray 웹훅 페이로드를 `DoorayEvent` 모델로 파싱합니다.

**지원 이벤트 타입**:
- `postCreated` → `task.created`
- `postUpdated` → `task.updated`
- `postWorkflowChanged` → `task.updated`
- `postCommentCreated` → `comment.created`
- `postCommentUpdated` → `comment.updated`

**DoorayEvent 필드**:
```python
event_type: str          # 정규화된 이벤트 타입
task_id: str             # Dooray 게시글 ID
project_id: str          # Dooray 프로젝트 ID
title: Optional[str]     # 게시글 제목
body: str                # 게시글 본문 (API 조회 후 채움)
author: Optional[str]    # 작성자
current_state: State     # 현재 워크플로우 상태 (API 조회 후 채움)
extra_instruction: str   # 재트리거 시 최신 댓글
```

#### `security.py`
웹훅 요청 검증:
- `?key=` 쿼리 파라미터를 `WEBHOOK_SECRET`과 비교
- 일치하지 않으면 `401 Unauthorized` 반환
- `WEBHOOK_SECRET`이 빈 값이면 검증 스킵 (개발 환경용)

### 2. Dooray API 연동 (`app/dooray/`)

#### `client.py` - DoorayClient 클래스

**주요 메서드**:

```python
# 게시글 조회
async def fetch_post(project_id: str, post_id: str) -> dict
  # 반환: {"body": str, "workflow_id": str, "state": Optional[State]}

# 최신 댓글 조회 (사람이 쓴 댓글만)
async def fetch_latest_comment(project_id: str, post_id: str) -> str

# 워크플로우 상태 전이
async def set_state(project_id: str, post_id: str, state: State) -> bool

# 댓글 작성
async def post_comment(task_id: str, content: str) -> bool
```

**특징**:
- 워크플로우 ID는 한글 이름으로 조회하여 자동 매핑 (프로젝트별로 다름)
- `_workflow_name_to_id` 클래스 캐시로 중복 조회 방지
- `MANAGE_WORKFLOW_STATE=false`일 때 상태 전이 비활성화

**결과 댓글 포맷** (`format_result_comment()`):
```markdown
## ✅/❌ 자동 처리 완료/실패 → 상태: 구현/처리실패

**이벤트:** `task.created`
**태스크 ID:** `123456`
**테스트 결과:** pass/fail/N/A
**커밋:** `abc1234`
**브랜치:** `auto/dooray-123456` → `beta`

### 수정 파일
- `app/page.tsx`

### 요약
[에이전트가 작성한 요약]

### 오류 (실패 시)
```
[오류 내용]
```
```

#### `workflow.py`
워크플로우 상태 정의 및 매핑:
- `State` Enum: 7가지 상태 코드
- `STATE_TO_NAME`: 내부 상태 → Dooray 한글 이름
- `NAME_TO_STATE`: 한글 이름 → 내부 상태 (역방향)
- `normalize()`: 공백 차이 흡수 ("할 일" vs "할일")

### 3. 에이전트 실행 (`app/agent/`)

#### `runner.py` - 핵심 에이전트 루프

**주요 함수**:

```python
async def run_claude_agent(event: DoorayEvent) -> AgentResult
```

**실행 흐름**:
1. **Lock 획득**: `_agent_lock`으로 직렬화 (단일 `target_repo` 보호)
2. **타임아웃 적용**: `CLAUDE_TIMEOUT_SEC` 내에 완료되어야 함
3. **프롬프트 생성**: 
   - System: 정적 지침 (캐싱 대상)
   - Tools: 도구 정의 (캐싱 대상)
   - User: 태스크별 동적 내용
4. **SDK 루프 실행**: 최대 `CLAUDE_MAX_TURNS`까지 반복
   ```python
   for turn in range(1, max_turns + 1):
       response = await client.messages.create(...)
       if stop_reason == "tool_use":
           # 도구 실행 → 결과 append → 다음 턴
           if tool.name == "report_result":
               return AgentResult(...)  # 종료
       else:
           # 텍스트만 출력하고 종료 (비정상)
   ```
5. **결과 반환**: `AgentResult` 객체

**AgentResult 필드**:
```python
status: str              # success / failed / skipped
modified_files: list     # 수정된 파일 목록
commit_hash: str         # Git 커밋 해시
test_result: str         # pass / fail / N/A
summary: str             # 작업 요약 (한국어)
error: str               # 오류 메시지 (실패 시)
raw_output: str          # 전체 대화 기록
```

**캐싱 전략**:
- System 프롬프트: `cache_control: ephemeral`
- 도구 정의: 마지막 도구에 `cache_control: ephemeral`
- 정적 컨텍스트 재사용으로 토큰 비용 절감

#### `tools.py` - 에이전트 도구 정의 및 실행

**도구 목록**:

| 도구 이름 | 설명 | 주요 파라미터 |
|----------|------|--------------|
| `bash` | 쉘 명령 실행 (git, npm, tsc 등) | `command`, `timeout_sec` |
| `read_file` | 파일 내용 읽기 (라인 번호 포함) | `path` |
| `write_file` | 파일 생성/덮어쓰기 | `path`, `content` |
| `edit_file` | 부분 문자열 치환 | `path`, `old_string`, `new_string` |
| `glob` | 파일 패턴 검색 | `pattern` |
| `grep` | 파일 내용 검색 (ripgrep) | `pattern`, `path`, `glob` |
| `report_result` | 작업 종료 & 결과 보고 | `status`, `summary`, ... |

**안전 장치**:

```python
def _safe_path(rel_path: str) -> str
```
- 모든 파일 작업은 `target_repo` 내부로 제한
- 경로 탈출 시도(`../`, 절대 경로) 차단
- 위반 시 `ValueError` 발생

**bash 도구 특징**:
- 작업 디렉토리: `target_repo`
- 환경 변수: `GITHUB_TOKEN`, `GIT_TERMINAL_PROMPT=0`
- stdout/stderr 병합하여 순서 보존
- 출력이 30,000자 초과 시 앞뒤만 표시 (중간 생략)
- 타임아웃 기본값: `BASH_TIMEOUT_SEC` (300초)

**edit_file 도구 규칙**:
- `old_string`은 파일 내에서 **유일**해야 함 (1회만 출현)
- 정확히 일치하지 않거나 중복되면 오류 반환
- 에이전트가 보고 재시도하도록 유도

**grep 도구**:
- ripgrep(`rg`) 우선 사용, 없으면 시스템 `grep`으로 폴백
- `node_modules`, `.git`, `.next` 자동 제외
- 결과가 20,000자 초과 시 일부만 표시

#### `prompt_builder.py` - 프롬프트 생성

**두 가지 프롬프트**:

1. **System Prompt** (`build_system_prompt()`):
   - 에이전트 역할 및 규칙
   - Next.js App Router URL → 파일 경로 매핑 규칙
   - 파일 탐색 절차 (A → B → C 순서)
   - 금지 사항 (main 직접 push, --force 등)
   - **정적 내용으로 prompt caching 대상**

2. **User Message** (`build_user_message()`):
   - 태스크 ID, 제목, 작성자
   - 요청 본문 (비개발자 작성)
   - 재작업 시 테스터 추가 지시
   - Git 작업 절차 (브랜치 전략 포함)
   - **동적 내용 (요청마다 다름)**

**파일 탐색 절차** (System Prompt 포함):

```
[A] URL → 라우트 파일 후보 추출
  ├─ URL 경로 분석 (예: /jobs → app/jobs/page.tsx)
  └─ glob으로 파일 존재 확인

[B] 보이는 텍스트로 실제 파일 검색 (핵심)
  ├─ grep(pattern="보이는텍스트", glob="*.tsx")
  └─ 한글 텍스트가 i18n/상수 파일에 있으면 *.json, *.ts도 검색

[C] 컴포넌트 트리 추적 (A·B 실패 시)
  ├─ page.tsx의 import된 컴포넌트 파악
  ├─ layout.tsx 확인 (GNB, 사이드바, 푸터)
  └─ 최대 3단계 깊이까지 탐색

[D] 수정 대상 확정 후 최소 변경
  ├─ 찾은 파일만 수정
  └─ 못 찾으면 report_result(status=skipped)
```

**Git 작업 절차** (User Message 포함):
- 기준 브랜치: `beta` (main 아님!)
- 작업 브랜치: `auto/dooray-<task_id>`
- 재작업 시 기존 브랜치 재사용
- beta로 rebase → merge --no-ff → push

### 4. 유틸리티 (`app/utils/`)

#### `git_helper.py`

**주요 함수**:

```python
def build_clone_url() -> str
```
- `GITHUB_TOKEN` 포함 HTTPS URL 생성
- SSH 방식이면 원본 URL 반환

```python
def configure_git_credentials() -> None
```
- `git remote set-url`로 인증 URL 적용
- 서버 시작 시 1회 실행 (`app/main.py`)

```python
def delete_task_branch(task_id: str) -> bool
```
- 완료(DONE) 전이 시 작업 브랜치 삭제
- 안전 체크:
  - task_id 형식 검증 (영숫자/_/- 만 허용)
  - `auto/dooray-` 프리픽스 필수
  - 보호 브랜치(`main`, `master`, `develop` 등) 삭제 금지
  - 현재 체크아웃 중이면 beta로 이동 후 삭제
- 원격/로컬 브랜치 모두 삭제 (`-D` 강제 삭제)

**보호 브랜치 목록**:
```python
_PROTECTED_BRANCHES = {"main", "master", "develop", "dev", "beta"}
```

#### `logger.py`

**로깅 설정** (loguru):
- 콘솔 출력: 컬러 포맷, 레벨별 구분
- 파일 출력: `logs/agent_{YYYY-MM-DD}.log`
  - 자정(00:00) 로테이션
  - 14일 보관
  - UTF-8 인코딩
- 로그 레벨: `LOG_LEVEL` 환경 변수 (기본: INFO)

**로그 포맷**:
```
2026-06-04 10:30:45 | INFO     | app.webhooks.router:receive_dooray_webhook | [WEBHOOK] event=task.created ...
```

### 5. 설정 관리 (`app/config.py`)

**Settings 클래스** (pydantic-settings):

모든 환경 변수는 `.env` 파일에서 자동 로드됩니다.

**주요 설정 그룹**:

#### Anthropic (필수)
```python
anthropic_api_key: str = ""  # SDK 인증 키 (필수)
```

#### Dooray
```python
dooray_api_token: str         # API 토큰
dooray_tenant_domain: str     # 웹 도메인 (예: incruit.dooray.com)
dooray_project_id: str        # 프로젝트 ID
dooray_api_base: str = "https://api.dooray.com"  # API 호스트 (고정)
```

#### Target Repository
```python
target_repo_path: str = "./target_repo"
target_repo_url: str          # Git 저장소 URL
target_beta_branch: str = "beta"  # 머지 대상 브랜치
```

#### GitHub (HTTPS push용)
```python
github_token: str = ""        # Personal Access Token (SSH 방식이면 비워둠)
github_user: str = ""         # GitHub 사용자명
```

#### 에이전트 루프
```python
claude_model: str = "claude-sonnet-4-5"
claude_max_tokens: int = 8192      # 응답 1회당 최대 토큰
claude_max_turns: int = 50         # 루프 최대 반복 횟수
claude_timeout_sec: int = 600      # 전체 타임아웃 (초)
bash_timeout_sec: int = 300        # bash 명령 타임아웃 (초)
```

#### 워크플로우
```python
manage_workflow_state: bool = True  # 상태 전이 자동 관리
```

#### 서버
```python
server_host: str = "0.0.0.0"
server_port: int = 8000
log_level: str = "INFO"
```

#### 보안
```python
webhook_secret: str = ""      # 웹훅 ?key= 검증값 (빈 값이면 스킵)
```

---

## 환경 설정

### 1. 사전 준비

#### 필수 요구사항
- Python 3.10 이상
- Git
- ripgrep (`rg`) - 설치 권장 (없으면 시스템 grep 사용)
- Node.js 및 pnpm (target_repo 빌드용)

#### Anthropic API Key 발급
1. [console.anthropic.com](https://console.anthropic.com) 접속
2. API Keys 메뉴에서 새 키 발급
3. `.env` 파일의 `ANTHROPIC_API_KEY`에 입력

⚠️ **주의**: v2는 SDK를 직접 사용하므로 `claude login` OAuth는 사용 불가

#### Dooray 설정
1. Dooray 관리자 페이지에서 API 토큰 발급
2. 프로젝트 ID 확인 (URL에서 확인 가능)
3. 워크플로우 이름이 다음과 일치하는지 확인:
   - "할 일", "처리중", "구현", "처리실패", "보완요청", "운영배포대기", "완료"
   - 공백 차이는 자동 흡수됨 ("할 일" = "할일")

#### GitHub 설정 (HTTPS 방식)
1. [GitHub Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens) 접속
2. Classic token 생성
3. 권한: `repo` (전체 저장소 액세스)
4. `.env`에 `GITHUB_TOKEN`, `GITHUB_USER` 입력

**SSH 방식 사용 시**:
- `GITHUB_TOKEN`, `GITHUB_USER`를 비워두고 SSH 키로 인증
- 서버 실행 환경에 SSH 키가 설정되어 있어야 함

### 2. 환경 변수 설정

`.env.example`을 복사하여 `.env` 생성:

```bash
cp .env.example .env
```

최소 필수 항목:
```env
ANTHROPIC_API_KEY=sk-ant-xxxx
DOORAY_API_TOKEN=xxxx
DOORAY_TENANT_DOMAIN=your-org.dooray.com
DOORAY_PROJECT_ID=1234567890
TARGET_REPO_URL=https://github.com/your-org/your-nextjs-app.git
```

### 3. 실행 방법

#### Docker (권장)

```bash
# 이미지 빌드 및 컨테이너 실행
docker compose up -d --build

# 로그 확인
docker compose logs -f

# 컨테이너 중지
docker compose down
```

**docker-compose.yml 설정**:
- target_repo 볼륨 마운트 (데이터 보존)
- 로그 디렉토리 마운트
- 포트 매핑: 8000:8000

#### 로컬 실행

```bash
# 1. 가상환경 생성
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 서버 실행
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 또는
python app/main.py
```

#### 개발 모드 (자동 리로드)

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. Dooray 웹훅 등록

1. Dooray 프로젝트 설정 → 웹훅 메뉴
2. 새 웹훅 추가:
   - URL: `https://your-server.com/webhook/dooray?key=<WEBHOOK_SECRET>`
   - 이벤트 선택:
     - ✅ 게시글 생성
     - ✅ 게시글 업데이트
     - ✅ 워크플로우 변경
     - ✅ 댓글 생성
3. 저장

**로컬 테스트**:
- ngrok 등으로 터널링: `ngrok http 8000`
- 터널 URL을 웹훅에 등록

---

## 개발 가이드

### 1. 코드 수정 가이드

#### 새 도구 추가하기

1. **`app/agent/tools.py`에 스키마 추가**:

```python
TOOL_SCHEMAS = [
    # ... 기존 도구들
    {
        "name": "my_new_tool",
        "description": "도구 설명 (에이전트가 읽음)",
        "input_schema": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "파라미터 설명"},
            },
            "required": ["param1"],
        },
    },
]
```

2. **`execute_tool()` 함수에 실행 로직 추가**:

```python
async def execute_tool(name: str, tool_input: dict) -> str:
    try:
        # ... 기존 도구들
        if name == "my_new_tool":
            return await _tool_my_new(tool_input)
        return f"ERROR: 알 수 없는 도구 '{name}'"
    except Exception as e:
        return f"ERROR: {e}"

async def _tool_my_new(inp: dict) -> str:
    # 도구 구현
    result = do_something(inp["param1"])
    return f"성공: {result}"
```

3. **에이전트가 자동으로 사용 가능**

#### 새 워크플로우 상태 추가하기

1. **`app/dooray/workflow.py` 수정**:

```python
class State(str, Enum):
    # ... 기존 상태들
    MY_NEW_STATE = "MY_NEW_STATE"

STATE_TO_NAME = {
    # ... 기존 매핑들
    State.MY_NEW_STATE: "새상태",  # Dooray 워크플로우 한글 이름
}
```

2. **`app/webhooks/router.py`의 `_decide_action()` 로직 수정**:

```python
def _decide_action(event_type: str, state) -> str:
    # ... 기존 로직
    if state == State.MY_NEW_STATE:
        return _RUN  # 또는 다른 액션
    # ...
```

### 2. 테스트

#### 유닛 테스트 작성

```bash
# 테스트 실행
pytest tests/

# 특정 테스트 파일 실행
pytest tests/test_parser.py -v

# 커버리지 확인
pytest --cov=app tests/
```

#### 웹훅 페이로드 테스트

`tests/fixtures/sample_webhook.json` 형식으로 샘플 데이터 작성:

```json
{
  "webhookType": "postCreated",
  "post": {
    "id": "123456",
    "subject": "테스트 태스크"
  },
  "project": {
    "id": "1234567890"
  },
  "source": {
    "member": {
      "name": "홍길동"
    }
  }
}
```

curl로 직접 테스트:

```bash
curl -X POST "http://localhost:8000/webhook/dooray?key=test" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_webhook.json
```

### 3. 디버깅

#### 로그 레벨 조정

`.env`:
```env
LOG_LEVEL=DEBUG
```

#### 주요 로그 접두사

- `[WEBHOOK]`: 웹훅 수신 및 액션 결정
- `[AGENT]`: 에이전트 실행 흐름
- `[AGENT:1234abcd]`: 특정 태스크 ID의 에이전트 로그
- `[TOOL]`: 도구 실행
- `[DOORAY]`: Dooray API 호출
- `[GIT]`: Git 작업
- `[NODE]`: Node.js 관련 작업
- `[BG]`: 백그라운드 작업

#### 에이전트 대화 기록 확인

로그 파일 또는 Dooray 댓글의 `raw_output` 필드에서 전체 대화 내용 확인 가능

### 4. 프롬프트 수정

에이전트 동작을 조정하려면 다음 파일을 수정하세요:

#### System Prompt (정적)
**파일**: `app/agent/prompt_builder.py` → `SYSTEM_INSTRUCTION`

- 에이전트 역할 정의
- 도구 사용법
- 작업 원칙
- 금지 사항

**수정 시 주의**:
- Prompt caching 대상이므로 변경 시 캐시 무효화
- 정적 내용만 포함 (태스크별 동적 내용은 User Message로)

#### URL → 파일 경로 매핑
**파일**: `app/agent/prompt_builder.py` → `URL_TO_FILE_GUIDE`

- Next.js App Router 경로 규칙
- 동적 라우트 패턴
- 공통 컴포넌트 위치

#### 파일 탐색 절차
**파일**: `app/agent/prompt_builder.py` → `DISCOVERY_STEPS`

- 3단계 탐색 전략 (A → B → C)
- 각 단계별 상세 지침

#### User Message (동적)
**파일**: `app/agent/prompt_builder.py` → `build_user_message()`

- 태스크 정보 포맷
- Git 작업 절차
- 브랜치 전략
- 재작업 지시 포맷

### 5. 보안 고려사항

#### 경로 탈출 방지
- `_safe_path()` 함수로 모든 파일 접근 검증
- `target_repo` 밖 접근 시 `ValueError` 발생

#### 브랜치 보호
- `_PROTECTED_BRANCHES`에 보호 브랜치 목록 정의
- main/master/develop 등 직접 push 차단

#### 웹훅 인증
- `WEBHOOK_SECRET`으로 요청 검증
- 개발 환경에서는 비워두고 스킵 가능

#### 환경 변수 보안
- `.env` 파일은 `.gitignore`에 포함 (커밋 금지)
- API 키, 토큰 등 민감 정보 포함

---

## 배포 및 운영

### 1. 프로덕션 배포

#### 환경 변수 점검

배포 전 필수 항목 확인:
```bash
# 필수
✅ ANTHROPIC_API_KEY
✅ DOORAY_API_TOKEN
✅ DOORAY_PROJECT_ID
✅ TARGET_REPO_URL
✅ GITHUB_TOKEN (HTTPS 방식 시)
✅ WEBHOOK_SECRET (보안 필수)

# 선택적 조정
⚙️ CLAUDE_TIMEOUT_SEC (대규모 작업 시 증가)
⚙️ CLAUDE_MAX_TURNS (복잡한 작업 시 증가)
⚙️ LOG_LEVEL (프로덕션은 INFO 권장)
```

#### Docker 배포

**docker-compose.yml** (프로덕션용):

```yaml
version: '3.8'
services:
  agent:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./target_repo:/app/target_repo
      - ./logs:/app/logs
    env_file:
      - .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

배포 명령:

```bash
# 이미지 빌드 및 실행
docker compose up -d --build

# 헬스체크 확인
curl http://localhost:8000/health

# 로그 모니터링
docker compose logs -f --tail=100
```

#### 리버스 프록시 (nginx)

```nginx
server {
    listen 443 ssl;
    server_name agent.your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 에이전트 실행 시간이 길 수 있으므로 타임아웃 증가
        proxy_read_timeout 900s;
        proxy_connect_timeout 60s;
    }
}
```

### 2. 모니터링

#### 헬스체크 엔드포인트

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

컨테이너 자동 재시작에 활용 가능

#### 로그 수집

**로그 위치**:
- 파일: `logs/agent_YYYY-MM-DD.log`
- 컨테이너: `docker compose logs -f`

**로그 집계 (예: ELK Stack)**:

```yaml
# docker-compose.yml에 추가
  filebeat:
    image: docker.elastic.co/beats/filebeat:8.0.0
    volumes:
      - ./logs:/logs:ro
      - ./filebeat.yml:/usr/share/filebeat/filebeat.yml:ro
```

#### 주요 모니터링 지표

- **요청 처리율**: 웹훅 수신 빈도
- **에이전트 성공률**: `status=success` 비율
- **평균 실행 시간**: 태스크당 처리 시간
- **타임아웃 발생**: `CLAUDE_TIMEOUT_SEC` 초과 횟수
- **API 오류율**: Dooray/Anthropic API 실패율

### 3. 성능 최적화

#### Prompt Caching 최대화

- System Prompt는 변경하지 않기 (캐시 유지)
- 도구 스키마 수정 최소화
- 캐시 히트율 모니터링 (Anthropic Console)

#### 동시 실행 제한

현재 `_agent_lock`으로 직렬화됨:
- 단일 `target_repo` 보호
- 동시 실행 시 Git 충돌 방지

**확장 방안**:
- 태스크별 별도 작업 디렉토리 생성
- Lock을 태스크 ID 기반으로 세분화

#### 타임아웃 조정

```env
# 간단한 작업
CLAUDE_TIMEOUT_SEC=300
CLAUDE_MAX_TURNS=30

# 복잡한 작업
CLAUDE_TIMEOUT_SEC=900
CLAUDE_MAX_TURNS=60
```

### 4. 백업 및 복구

#### target_repo 백업

```bash
# 정기 백업 스크립트
#!/bin/bash
DATE=$(date +%Y%m%d)
tar -czf target_repo_backup_$DATE.tar.gz target_repo/
# 원격 저장소로 복사
aws s3 cp target_repo_backup_$DATE.tar.gz s3://backups/
```

#### 로그 백업

```bash
# 14일 이상 된 로그 아카이브
find logs/ -name "*.log" -mtime +14 -exec gzip {} \;
find logs/ -name "*.log.gz" -mtime +90 -delete
```

#### 복구 절차

1. 서비스 중지: `docker compose down`
2. target_repo 복원: `tar -xzf backup.tar.gz`
3. Git 상태 확인: `cd target_repo && git status`
4. 서비스 재시작: `docker compose up -d`

---

## 트러블슈팅

### 1. 일반적인 문제

#### 문제: 웹훅을 받지 못함

**증상**: Dooray 이벤트 발생해도 에이전트가 동작하지 않음

**해결 방법**:
1. 헬스체크 확인: `curl http://your-server:8000/health`
2. 웹훅 URL 확인: `?key=` 파라미터 포함 여부
3. 방화벽/포트 포워딩 확인
4. Dooray 웹훅 설정에서 "최근 전송 로그" 확인
5. 로그 확인: `docker compose logs | grep WEBHOOK`

#### 문제: "Unauthorized: invalid key"

**증상**: 웹훅 요청이 401 오류 반환

**해결 방법**:
1. `.env`의 `WEBHOOK_SECRET` 확인
2. Dooray 웹훅 URL의 `?key=` 값과 일치하는지 확인
3. 개발 환경에서는 `WEBHOOK_SECRET=`로 비워두기

#### 문제: target_repo clone 실패

**증상**: 서버 시작 시 "clone 실패" 오류

**해결 방법**:
1. `TARGET_REPO_URL` 형식 확인 (HTTPS 또는 SSH)
2. HTTPS 방식:
   - `GITHUB_TOKEN`, `GITHUB_USER` 설정 확인
   - 토큰 권한 확인 (repo 필요)
3. SSH 방식:
   - 서버에 SSH 키 설정
   - `ssh -T git@github.com`으로 연결 테스트

### 2. 에이전트 실행 문제

#### 문제: 에이전트가 타임아웃

**증상**: `CLAUDE_TIMEOUT_SEC` 초과 후 "timeout" 오류

**해결 방법**:
1. 타임아웃 증가: `.env`에서 `CLAUDE_TIMEOUT_SEC=900`
2. 요청 내용 간소화 (너무 복잡한 작업은 분할)
3. 로그에서 어느 단계에서 시간이 오래 걸리는지 확인
4. bash 명령 타임아웃도 확인: `BASH_TIMEOUT_SEC`

#### 문제: "max_turns 도달 — 작업 미완료"

**증상**: 에이전트가 결과를 보고하지 못하고 종료

**해결 방법**:
1. `CLAUDE_MAX_TURNS` 증가 (기본 50 → 80)
2. 프롬프트가 명확한지 확인 (파일 찾기 실패 반복 등)
3. 로그에서 루프 패턴 확인 (같은 작업 반복 시)
4. System Prompt 개선 (더 명확한 지침)

#### 문제: "report_result 없이 종료"

**증상**: 에이전트가 텍스트만 출력하고 도구를 호출하지 않음

**원인**: 
- 프롬프트가 너무 모호함
- 에이전트가 작업 불가능하다고 판단

**해결 방법**:
1. 로그의 어시스턴트 텍스트 확인 (무엇을 시도했는지)
2. 요청 본문 개선 (더 구체적으로)
3. System Prompt의 report_result 설명 강화

#### 문제: Git push 실패

**증상**: 에이전트가 코드 수정까지는 했으나 push 실패

**해결 방법**:
1. Git 인증 확인:
   ```bash
   docker compose exec agent bash
   cd target_repo
   git push  # 수동으로 테스트
   ```
2. rebase 충돌:
   - 로그에서 "REBASE_CONFLICT" 검색
   - beta 브랜치와 작업 브랜치 간 충돌 해결 필요
3. 브랜치 보호 규칙 확인 (GitHub/GitLab 설정)

### 3. Dooray 연동 문제

#### 문제: 워크플로우 상태 전이 실패

**증상**: 에이전트가 동작했지만 상태가 변경되지 않음

**해결 방법**:
1. `MANAGE_WORKFLOW_STATE=true` 확인
2. 로그에서 "[DOORAY] 상태 전이 실패" 검색
3. 워크플로우 이름 매핑 확인:
   ```python
   # app/dooray/workflow.py
   STATE_TO_NAME = {
       State.TODO: "할 일",  # Dooray와 정확히 일치해야 함
       # ...
   }
   ```
4. Dooray API 토큰 권한 확인 (워크플로우 변경 권한 필요)

#### 문제: 댓글 작성 실패

**증상**: 결과 댓글이 Dooray에 나타나지 않음

**해결 방법**:
1. API 토큰 확인: `DOORAY_API_TOKEN`
2. 프로젝트 ID 확인: `DOORAY_PROJECT_ID`
3. 로그에서 "[DOORAY] Comment post failed" 검색
4. 수동 테스트:
   ```bash
   curl -X POST \
     "https://api.dooray.com/project/v1/projects/{PROJECT_ID}/posts/{TASK_ID}/logs" \
     -H "Authorization: dooray-api {TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"body":{"mimeType":"text/x-markdown","content":"테스트"}}'
   ```

#### 문제: 최신 댓글을 읽지 못함

**증상**: 재트리거 시 테스터 댓글이 반영되지 않음

**해결 방법**:
1. 댓글이 봇 댓글 형식인지 확인 (## ✅/❌로 시작하면 제외됨)
2. 로그에서 "[DOORAY] latest human comment" 검색
3. `fetch_latest_comment()` 로직 확인

### 4. 성능 문제

#### 문제: 에이전트 응답이 느림

**원인 분석**:
1. 로그에서 각 도구 실행 시간 확인
2. bash 명령이 오래 걸리는지 확인 (빌드, 테스트 등)
3. Anthropic API 응답 시간 확인

**해결 방법**:
- bash 타임아웃 조정
- node_modules 캐싱 확인 (pnpm install 스킵)
- Prompt caching 활성화 확인 (캐시 히트율)

#### 문제: 동시 요청 처리 불가

**현재 제약**: `_agent_lock`으로 직렬화됨

**확장 방안**:
1. 태스크별 별도 작업 디렉토리
2. 멀티 워커 배포 (각각 별도 target_repo)
3. 작업 큐 도입 (Celery 등)

### 5. 디버깅 팁

#### 로그 레벨별 정보

**INFO** (기본):
- 웹훅 수신/액션 결정
- 에이전트 시작/종료
- 도구 호출 요약
- 상태 전이

**DEBUG**:
- API 요청/응답 전체
- 도구 입력/출력 상세
- Git 명령 상세

#### 주요 로그 패턴

에이전트 실행 추적:
```
[AGENT] Starting SDK agent for task=123456
[AGENT:12345678] 🔧 bash(git pull)
[AGENT:12345678] 🔧 read_file(app/page.tsx)
[AGENT:12345678] 🔧 edit_file(app/page.tsx)
[AGENT:12345678] ✅ report_result 수신 turn=12
```

오류 추적:
```
[TOOL] edit_file 실행 오류: 파일 없음
[DOORAY] 상태 전이 실패 → 구현: 404 ...
[AGENT] Anthropic API 오류: rate_limit_error
```

#### 수동 재현 방법

1. 이벤트 페이로드 저장:
```bash
# 로그에서 추출 또는 Dooray 웹훅 로그에서 복사
cat > test_event.json
```

2. curl로 직접 호출:
```bash
curl -X POST "http://localhost:8000/webhook/dooray?key=test" \
  -H "Content-Type: application/json" \
  -d @test_event.json
```

3. 로그 실시간 확인:
```bash
tail -f logs/agent_$(date +%Y-%m-%d).log
```

---

## 부록

### A. API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 헬스체크 |
| POST | `/webhook/dooray` | Dooray 웹훅 수신 |

### B. 환경 변수 전체 목록

[config.py 참조](#5-설정-관리-appconfigpy)

### C. 워크플로우 상태 다이어그램

```
                    ┌─────────┐
                    │  할 일   │ (TODO)
                    └────┬────┘
                         │ task.created
                         │ 또는 comment.created
                         ▼
                    ┌─────────┐
                    │  처리중  │ (IN_PROGRESS)
                    └────┬────┘
                         │ 에이전트 실행
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
       ┌─────────┐  ┌─────────┐  ┌──────────┐
       │   구현   │  │ 처리실패 │  │  건너뜀   │
       └────┬────┘  └────┬────┘  └────┬─────┘
            │            │             │
            │            │             │ (수동 처리)
            │            └─────┬───────┘
            │                  │ 수정 요청
            │                  ▼
            │            ┌──────────┐
            │            │ 보완요청  │ (REWORK)
            │            └────┬─────┘
            │                 │ comment.created
            │                 │ (재트리거)
            │                 ▼
            │            (처리중으로 돌아감)
            │
            │ 테스터 검수
            ▼
       ┌──────────────┐
       │ 운영배포대기  │ (READY_TO_DEPLOY)
       └──────┬───────┘
              │ 운영 배포
              ▼
       ┌──────────────┐
       │     완료      │ (DONE)
       └──────┬───────┘
              │
              ▼
        (브랜치 삭제)
```

### D. 참고 문서

- **Anthropic API**: [docs.anthropic.com](https://docs.anthropic.com)
- **Dooray API**: Dooray 관리자 페이지 → API 문서
- **FastAPI**: [fastapi.tiangolo.com](https://fastapi.tiangolo.com)
- **Next.js App Router**: [nextjs.org/docs/app](https://nextjs.org/docs/app)

---

## 라이선스 및 기여

### 기여 방법
1. 이슈 등록 또는 기능 제안
2. Fork 후 브랜치 생성: `git checkout -b feature/my-feature`
3. 변경 사항 커밋: `git commit -m "Add my feature"`
4. Push: `git push origin feature/my-feature`
5. Pull Request 생성

### 문의
프로젝트 관련 문의사항은 이슈 또는 내부 채널로 연락 주세요.

---

**문서 버전**: 1.0.0  
**최종 업데이트**: 2026-06-04
