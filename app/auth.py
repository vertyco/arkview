import typing as t  # noqa: F401

from fastapi import HTTPException, Request, status


class RequireBearer:
    """FastAPI dependency: enforce `Authorization: Bearer <api_key>` when configured.

    Pre: `api_key` is a string (empty disables auth).
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
        expected = f"Bearer {self.api_key}"
        if header != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token",
            )
