import hmac
import logging

from fastapi import HTTPException, Request

from app.state import state

log = logging.getLogger("arkviewer.auth")


async def verify_api_key(request: Request) -> None:
    """FastAPI dependency for Bearer token authentication.

    Skipped entirely when no APIKey is configured.
    """
    if not state.config.api_key:
        return

    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else auth

    if not token or not hmac.compare_digest(token.strip(), state.config.api_key):
        log.warning("Rejected request from %s - invalid API key", request.client)
        raise HTTPException(status_code=401, detail="Invalid API key")
