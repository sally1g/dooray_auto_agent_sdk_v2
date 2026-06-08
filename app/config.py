from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic (SDK 방식 — API Key 필수. OAuth는 SDK에서 동작하지 않음)
    anthropic_api_key: str = ""

    # Dooray
    dooray_api_token: str
    dooray_tenant_domain: str   # 웹 UI 도메인 (예: incruit.dooray.com)
    dooray_project_id: str
    dooray_api_base: str = "https://api.dooray.com"   # API 호스트 (테넌트 무관 고정)

    # Target Repository
    target_repo_path: str = "./target_repo"
    target_repo_url: str
    target_beta_branch: str = "beta"

    # 워크플로우 상태 전이 (할일→처리중→구현/처리실패 등) 자동 관리 여부
    manage_workflow_state: bool = True

    # GitHub (HTTPS push용; SSH 방식이면 빈 문자열)
    github_token: str = ""
    github_user: str = ""

    # Anthropic SDK / 에이전트 루프
    claude_model: str = "claude-sonnet-4-5"
    claude_max_tokens: int = 8192   # 응답 1회당 최대 출력 토큰
    claude_max_turns: int = 50      # 에이전트 루프 최대 반복 횟수 (도구 호출 사이클)
    claude_timeout_sec: int = 600   # 단일 에이전트 실행 전체 최대 시간(초)
    bash_timeout_sec: int = 300     # bash 도구 단일 명령 기본 타임아웃(초)

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    log_level: str = "INFO"

    # Webhook Security
    webhook_secret: str = ""   # ?key=<secret> 쿼리 파라미터 검증 (빈 문자열이면 스킵)


settings = Settings()
