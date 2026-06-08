# Dooray Claude Agent v2 (Anthropic SDK 버전)

Dooray 웹훅을 받아 Next.js 코드를 자동 수정/테스트/배포하는 에이전트.
**v1의 Claude Code CLI subprocess 방식을 Anthropic SDK tool use 루프로 전환한 버전.**

---

## v1 대비 변경점

| 구분 | v1 (`dooray_claude_agent`) | v2 (이 프로젝트) |
|------|----------------------------|------------------|
| 에이전트 실행 | `claude` CLI를 subprocess로 호출 | `anthropic` SDK tool use 루프 직접 구동 |
| 도구(Bash/Read/Edit…) | CLI 내장 | `app/agent/tools.py`에 직접 구현 |
| 결과 수집 | stdout stream-json **정규식 파싱** | `report_result` **도구 호출**로 구조화 수신 |
| 인증 | API Key 또는 OAuth(`claude login`) | **API Key 전용** (OAuth 불가) |
| 컨테이너 의존 | Node.js + Claude Code CLI | Node.js(빌드용) + ripgrep, CLI 불필요 |
| 동시성 | 단일 Lock 직렬화 | 동일 |

핵심 이득: stream-json 파싱·exit code 추측 같은 **subprocess 불안정성 제거**.
결과가 정규식이 아니라 도구 입력으로 들어오므로 파싱 실패가 원천적으로 없음.

---

## 아키텍처

```
Dooray 웹훅
   │  POST /webhook/dooray?key=<secret>
   ▼
app/webhooks/router.py        ← 이벤트 파싱 + 상태머신 판정
   │
   ▼
app/agent/runner.py           ← Anthropic SDK 루프
   │   messages.create → tool_use → execute_tool → 결과 append … 반복
   │   report_result 수신 시 종료
   ├── app/agent/prompt_builder.py   system(정적·캐싱) + user(태스크별)
   └── app/agent/tools.py            bash/read/write/edit/glob/grep/report_result
   ▼
AgentResult → Dooray 댓글 + 워크플로우 상태 전이
```

`dooray/`, `webhooks/parser.py`, `webhooks/security.py`, `utils/`는 v1과 동일.

---

## 실행

### 사전 준비
1. **Anthropic API Key 필수** — `console.anthropic.com`에서 발급 후 `.env`의 `ANTHROPIC_API_KEY`에 입력.
   (v2는 SDK를 직접 쓰므로 `claude login` OAuth로는 동작하지 않음)
2. `.env` 작성 — `.env.example` 참고.

### Docker
```bash
docker compose up -d --build
docker compose logs -f
```

### 로컬
```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 주요 설정 (`.env`)

| 변수 | 설명 | 기본 |
|------|------|------|
| `ANTHROPIC_API_KEY` | **필수.** SDK 인증 키 | — |
| `CLAUDE_MODEL` | 모델 ID | `claude-sonnet-4-5` |
| `CLAUDE_MAX_TOKENS` | 응답 1회 최대 출력 토큰 | `8192` |
| `CLAUDE_MAX_TURNS` | 에이전트 루프 최대 반복 | `50` |
| `CLAUDE_TIMEOUT_SEC` | 에이전트 전체 타임아웃(초) | `600` |
| `BASH_TIMEOUT_SEC` | bash 도구 단일 명령 타임아웃(초) | `300` |
| `WEBHOOK_SECRET` | `?key=` 쿼리 검증값 (빈 값이면 스킵) | — |
| `TARGET_BETA_BRANCH` | 머지 대상 브랜치 | `beta` |
| `MANAGE_WORKFLOW_STATE` | 워크플로우 상태 자동 전이 | `true` |

---

## 안전장치

- **경로 제한**: 모든 파일 도구는 `target_repo` 밖 접근 차단 (`_safe_path`)
- **이중 타임아웃**: bash 명령별 + 에이전트 루프 전체
- **브랜치 보호**: `main`/`develop` 등 보호 브랜치 직접 push·삭제 금지 (`git_helper.py`)
- **웹훅 인증**: `?key=` 쿼리 파라미터 검증

---

## 배포 정책 (2026~)

베타 배포는 **`DEV_` prefix 태그 push** 로만 트리거된다.
`develop`(=`TARGET_BETA_BRANCH`) 브랜치에 commit push 만으로는 배포가 시작되지 않으며,
`target_repo/.github/workflows/deploy.yml` 의 `deploy-tenant-app-beta` 잡이 태그 push 를
감지해 ECS `gangnam2026-beta` 로 반영한다.

에이전트 절차 (자세한 명령은 `app/agent/prompt_builder.py` 4단계 참조):
1. 자체 검증(type-check / lint) 통과
2. 작업 브랜치 → `{TARGET_BETA_BRANCH}` rebase 후 push (이 시점엔 미배포)
3. 신규 commit 메시지를 분석해 영문 snake_case 작업명을 산출
   (예: `landing_hero_copy_fix`, `payment_api_refactor`)
4. `DEV_<title>_YYYYMMDDHHMMSS` 태그 생성 → `git push origin <tag>` → 배포 트리거
5. `report_result` 의 summary 에 생성한 태그명 포함
