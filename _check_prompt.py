from app.webhooks.parser import DoorayEvent
from app.agent.prompt_builder import build_system_prompt, build_user_message

# system 프롬프트 — 태그 정책이 들어갔는지
sysp = build_system_prompt()
print("=== SYSTEM ===")
print("len:", len(sysp))
print("DEV_ prefix in system:", "DEV_" in sysp)
print("'태그 push' guidance in system:", "태그 push" in sysp)

# user 메시지 — 4단계가 새 절차로 교체됐는지
fake = DoorayEvent(
    event_type="taskCreated",
    task_id="task-abc123",
    title="랜딩 hero 복사",
    body="hero 문구를 바꿔주세요.",
    author="홍길동",
    extra_instruction=None,
)
um = build_user_message(fake)
print("\n=== USER ===")
print("len:", len(um))
print("has DEV_ tag rule:", "DEV_" in um and "snake_case" in um)
print("has YYYYMMDDHHMMSS:", "YYYYMMDDHHMMSS" in um)
print("has 'git push origin {beta}' merge step (legacy):",
      "git push origin develop\n```" in um)  # 새 절차에는 코드블록 안에 push develop 있긴 함
print("has 'git tag -a':", "git tag -a" in um)
print("has 'git push origin \"$TAG\"':", 'git push origin "$TAG"' in um)
print("has snake_case rule lines:", "user_login_bugfix" in um)

# 새 4단계 발췌
import re
m = re.search(r"### 4단계.*?### 5단계", um, re.DOTALL)
if m:
    print("\n--- 4단계 발췌 (앞 1500자) ---")
    print(m.group()[:1500])
