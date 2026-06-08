# Claude Agent Rules — Next.js 자동 수정 규칙

## 역할
Dooray 이벤트를 받아 Next.js 코드를 수정/테스트/배포한다.

## 디렉토리 규칙
- App Router: `app/` 하위 라우트 파일 수정
- 컴포넌트: `components/`
- API: `app/api/[route]/route.ts`
- 유틸: `lib/`
- 타입: `types/`

## 작업 절차
1. 변경 전 항상 `git pull`
2. 코드 스타일: prettier + eslint 적용
3. TypeScript strict 준수, `any` 금지
4. 변경 후 검증 순서:
   - `npx tsc --noEmit`
   - `npm run lint`
   - `npm test -- --watchAll=false` (test 스크립트 존재 시)

## 브랜치 전략
- 작업 브랜치: `auto/dooray-<task_id>`
- 머지 대상: `beta` 브랜치
- `main` 직접 push 금지

## 절대 금지
- `--force` push 금지
- `.env`, `.env.local` 커밋 금지
- `node_modules/`, `.next/` 커밋 금지
- 기존 테스트 파일 삭제 금지
- DB 마이그레이션 자동 실행 금지

## 결과 출력
작업 종료 시 `report_result` 도구를 1회 호출하여 구조화된 결과를 보고한다:
{"status":"success","modified_files":["app/page.tsx"],"commit_hash":"abc1234","test_result":"pass","summary":"..."}
(v2: stdout JSON 파싱 대신 SDK tool use로 결과 수신)
