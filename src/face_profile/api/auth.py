"""Bearer-token authentication for the M12 API.

Non-loopback deployment without a configured token is already rejected
at configuration-load time (``config.APIConfig``'s fail-closed
validator); this module enforces the token at request time whenever one
is configured, including on loopback if the operator opted in.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

_UNAUTHORIZED_DETAIL = {"error_code": "unauthorized", "message": "a valid bearer token is required"}


def require_auth(request: Request) -> None:
    """FastAPI dependency enforcing the configured bearer token, if any."""

    token = request.app.state.config.api.auth_token
    if token is None:
        return
    authorization = request.headers.get("authorization")
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_UNAUTHORIZED_DETAIL)
    provided = authorization.removeprefix("Bearer ")
    if not hmac.compare_digest(provided, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_UNAUTHORIZED_DETAIL)


def check_websocket_auth(token: str | None, *, configured_token: str | None) -> bool:
    """Return True when a WebSocket handshake's token (if any is required) matches."""

    if configured_token is None:
        return True
    if token is None:
        return False
    return hmac.compare_digest(token, configured_token)
