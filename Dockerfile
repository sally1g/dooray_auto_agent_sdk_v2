FROM python:3.12-slim

# git + ripgrep(grep 도구) + Node.js(target_repo 빌드/테스트용)
# v2는 Claude Code CLI를 쓰지 않으므로 CLI 설치 불필요. Node는 에이전트가
# bash로 npx tsc / npm run lint / npm test 를 돌리기 위해 필요하다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    ripgrep \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pnpm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /app/logs /app/target_repo
RUN git config --global --add safe.directory /app/target_repo

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
