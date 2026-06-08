from fastapi import Request, HTTPException
from app.config import settings


def verify_webhook_key(request: Request):
    if not settings.webhook_secret:
        return
    key = request.query_params.get("key", "")
    if key != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="Unauthorized: invalid key")
