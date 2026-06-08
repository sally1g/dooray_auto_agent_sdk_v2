import re
import subprocess
from app.config import settings
from app.utils.logger import logger

# 절대 삭제 금지 브랜치
_PROTECTED_BRANCHES = {"main", "master", "develop", "dev", "beta"}
# task_id 형식 검증 — 와일드카드/경로 주입 방지
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def build_clone_url() -> str:
    """GITHUB_TOKEN이 설정된 경우 인증 포함 HTTPS URL 반환, 아니면 원본 URL 반환."""
    url = settings.target_repo_url
    if settings.github_token and url.startswith("https://"):
        after_scheme = url[len("https://"):]
        return f"https://{settings.github_user}:{settings.github_token}@{after_scheme}"
    return url


def configure_git_credentials() -> None:
    """
    target_repo의 git 원격 URL을 GITHUB_TOKEN 포함 HTTPS URL로 교체한다.
    SSH 방식(github_token 미설정)이면 아무것도 하지 않는다.
    _ensure_target_repo() 이후에 호출해야 한다.
    """
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", settings.target_repo_path],
        check=False,
        capture_output=True,
    )

    if not settings.github_token:
        logger.info("[GIT] github_token 미설정 — SSH 키 방식으로 git 인증을 관리합니다.")
        return

    if not settings.target_repo_url.startswith("https://"):
        logger.warning("[GIT] TARGET_REPO_URL이 HTTPS가 아닙니다. git 자격증명 설정을 건너뜁니다.")
        return

    authed_url = build_clone_url()

    try:
        subprocess.run(
            ["git", "remote", "set-url", "origin", authed_url],
            cwd=settings.target_repo_path,
            check=True,
            capture_output=True,
        )
        logger.info("[GIT] git remote URL에 GITHUB_TOKEN 적용 완료.")
    except subprocess.CalledProcessError as e:
        logger.error(f"[GIT] git remote set-url 실패: {e.stderr.decode()}")


def _run_git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=settings.target_repo_path,
        capture_output=True,
        text=True,
    )


def delete_task_branch(task_id: str) -> bool:
    """완료(DONE) 상태 전이 시 작업 브랜치 auto/dooray-<task_id>를 안전하게 삭제한다.

    안전 체크:
      - task_id 형식 검증 (영숫자/_/- 만 허용 → 와일드카드 주입 방지)
      - 삭제 대상은 반드시 `auto/dooray-` 프리픽스 + 보호 브랜치 아님
      - 로컬이 해당 브랜치에 체크아웃돼 있으면 먼저 beta로 이동
      - 원격/로컬 삭제 실패는 경고만 (이미 없을 수 있음)
    """
    if not _TASK_ID_RE.match(task_id or ""):
        logger.error(f"[GIT] 비정상 task_id로 브랜치 삭제 거부: {task_id!r}")
        return False

    branch = f"auto/dooray-{task_id}"
    if not branch.startswith("auto/dooray-") or branch in _PROTECTED_BRANCHES:
        logger.error(f"[GIT] 보호 브랜치 삭제 거부: {branch}")
        return False

    # 현재 체크아웃된 브랜치가 삭제 대상이면 먼저 벗어난다
    cur = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    if cur.returncode == 0 and cur.stdout.strip() == branch:
        co = _run_git("checkout", settings.target_beta_branch)
        if co.returncode != 0:
            logger.warning(f"[GIT] {settings.target_beta_branch} 체크아웃 실패: {co.stderr.strip()}")

    ok = True
    # 원격 삭제 (없으면 무시)
    remote = _run_git("push", "origin", "--delete", branch)
    if remote.returncode == 0:
        logger.info(f"[GIT] 원격 브랜치 삭제: origin/{branch}")
    else:
        logger.warning(f"[GIT] 원격 브랜치 삭제 실패(이미 없음 가능): {remote.stderr.strip()}")

    # 로컬 삭제 (-D: 머지 여부 무관, DONE은 이미 운영 반영됨)
    local = _run_git("branch", "-D", branch)
    if local.returncode == 0:
        logger.info(f"[GIT] 로컬 브랜치 삭제: {branch}")
    else:
        logger.warning(f"[GIT] 로컬 브랜치 삭제 실패(이미 없음 가능): {local.stderr.strip()}")

    return ok
