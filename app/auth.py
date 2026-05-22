import hmac
import typing as t  # noqa: F401

from fastapi import HTTPException, Request, status


class RequireBearer:
    """FastAPI dependency: enforce API key auth when configured.

    Accepts either `Authorization: Bearer <api_key>` or a raw
    `Authorization: <api_key>` header. Legacy AVClient sends the raw form;
    new clients should prefer the Bearer form. Empty `api_key` disables
    auth entirely.

    Pre: `api_key` is a string.
    Post: raises 401 on mismatch; returns None on success or when disabled.
    """

    def __init__(self, api_key: str) -> None:
        assert isinstance(api_key, str)
        self.api_key = api_key

    async def __call__(self, request: Request) -> None:
        assert request is not None
        if not self.api_key:
            return
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else header
        if not token or not hmac.compare_digest(token.strip(), self.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
            )
