import os
import subprocess
from fastapi import FastAPI
from app.webhooks.router import router as webhook_router
from app.config import settings
from app.utils.logger import logger
from app.utils.git_helper import configure_git_credentials, build_clone_url

app = FastAPI(title="Dooray Claude Agent", version="0.1.0")
app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup():
    logger.info(f"Server starting on {settings.server_host}:{settings.server_port}")
    _ensure_target_repo()
    configure_git_credentials()
    _ensure_node_modules()


def _ensure_target_repo():
    """target_repo가 비어 있으면 .env의 TARGET_REPO_URL로 clone한다."""
    repo_path = settings.target_repo_path
    git_dir = os.path.join(repo_path, ".git")

    if os.path.isdir(git_dir):
        logger.info(f"[GIT] target_repo 이미 존재 — clone 생략 ({repo_path})")
        return

    logger.info(f"[GIT] target_repo 없음 — clone 시작: {settings.target_repo_url}")
    os.makedirs(repo_path, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", build_clone_url(), "."],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f"[GIT] clone 실패:\n{result.stderr}")
        raise RuntimeError(f"target_repo clone 실패: {result.stderr}")
    logger.info(f"[GIT] clone 완료 → {repo_path}")


def _ensure_node_modules():
    """node_modules가 없으면 pnpm install을 실행한다. 실패해도 서버는 뜬다."""
    repo_path = settings.target_repo_path
    nm_path = os.path.join(repo_path, "node_modules")
    if os.path.isdir(nm_path):
        logger.info("[NODE] node_modules 이미 존재 — pnpm install 생략")
        return
    logger.info("[NODE] node_modules 없음 — pnpm install 시작 (시간이 걸릴 수 있음)")
    result = subprocess.run(
        ["pnpm", "install", "--frozen-lockfile"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        logger.warning(f"[NODE] pnpm install 실패 (에이전트 실행 중 tsc 오류 가능):\n{result.stderr[:500]}")
    else:
        logger.info("[NODE] pnpm install 완료")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False
    )
