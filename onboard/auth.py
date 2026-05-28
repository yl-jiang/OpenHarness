"""Token-gate authentication for onboard.

Security model: a single shared secret (token) stored in ~/.onboard/secret.
Users present the token once via a gate page or URL parameter; a session cookie
is then set for subsequent requests.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint


_ONBOARD_ROOT = Path(os.environ.get("ONBOARD_WORKSPACE", "~/.onboard")).expanduser()
_SECRET_PATH = _ONBOARD_ROOT / "secret"

COOKIE_NAME = "onboard_session"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days

# Routes that bypass authentication
_PUBLIC_PATHS = frozenset({
    "/api/health",
    "/api/auth/verify",
    "/api/auth/status",
    "/_gate",
})


def get_token() -> str:
    """Read the current token, generating one if missing."""
    if _SECRET_PATH.exists():
        token = _SECRET_PATH.read_text(encoding="utf-8").strip()
        if token:
            return token
    return _generate_token()


def reset_token() -> str:
    """Force-generate a new token (invalidates all existing sessions)."""
    return _generate_token()


def _generate_token() -> str:
    """Generate a new random token and persist it."""
    _ONBOARD_ROOT.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    _SECRET_PATH.write_text(token + "\n", encoding="utf-8")
    _SECRET_PATH.chmod(0o600)
    return token


def _make_session_value(token: str) -> str:
    """Derive a session cookie value from the token (avoid storing raw token in cookie)."""
    return hashlib.sha256(f"onboard-session:{token}".encode()).hexdigest()[:48]


def verify_token(candidate: str) -> bool:
    """Constant-time comparison of candidate against stored token."""
    expected = get_token()
    return hmac.compare_digest(candidate.strip(), expected)


def verify_session_cookie(cookie_value: str) -> bool:
    """Check if a session cookie is valid for the current token."""
    expected = _make_session_value(get_token())
    return hmac.compare_digest(cookie_value, expected)


def set_session_cookie(response: Response) -> None:
    """Set the authenticated session cookie on a response."""
    value = _make_session_value(get_token())
    response.set_cookie(
        key=COOKIE_NAME,
        value=value,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


class TokenGateMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces token-gate authentication."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # Allow public paths
        if _is_public(path):
            return await call_next(request)

        # Check URL token parameter (one-time auth via link)
        url_token = request.query_params.get("token")
        if url_token and verify_token(url_token):
            # Redirect to same path without the token parameter, setting cookie
            clean_url = str(request.url).split("?")[0]
            response = RedirectResponse(url=clean_url, status_code=302)
            set_session_cookie(response)
            return response

        # Check session cookie
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie and verify_session_cookie(cookie):
            return await call_next(request)

        # Unauthenticated: return gate page or 401 for API calls
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )
        return HTMLResponse(content=_gate_html(), status_code=401)


def _is_public(path: str) -> bool:
    """Check if a path is in the public allowlist."""
    if path in _PUBLIC_PATHS:
        return True
    # Static assets for the gate page itself
    if path.startswith("/_gate"):
        return True
    return False


def _gate_html() -> str:
    """Inline HTML for the gate page."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Onboard - Access</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
      background: #0a0a0f;
      color: #e8e8ed;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .gate {
      width: 100%;
      max-width: 360px;
      padding: 2rem;
    }
    h1 {
      font-size: 1.5rem;
      margin-bottom: 0.5rem;
      font-weight: 600;
    }
    p {
      color: #888;
      font-size: 0.875rem;
      margin-bottom: 1.5rem;
      line-height: 1.5;
    }
    .input-group {
      display: flex;
      gap: 0.5rem;
    }
    input {
      flex: 1;
      padding: 0.625rem 0.875rem;
      border: 1px solid #333;
      border-radius: 6px;
      background: #1a1a2e;
      color: #e8e8ed;
      font-size: 0.9375rem;
      outline: none;
      transition: border-color 0.2s;
    }
    input:focus { border-color: #5b6ef5; }
    button {
      padding: 0.625rem 1.25rem;
      border: none;
      border-radius: 6px;
      background: #5b6ef5;
      color: #fff;
      font-size: 0.9375rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.2s;
    }
    button:hover { background: #4a5bd4; }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .error {
      color: #f55b5b;
      font-size: 0.8125rem;
      margin-top: 0.75rem;
      display: none;
    }
    .error.show { display: block; }
  </style>
</head>
<body>
  <div class="gate">
    <h1>🔒 Onboard</h1>
    <p>Enter access token to continue.<br/>
       Run <code>onboard token</code> in terminal to view your token.</p>
    <form id="gate-form">
      <div class="input-group">
        <input id="token-input" type="password" placeholder="Access token" autocomplete="off" autofocus />
        <button type="submit">Enter</button>
      </div>
      <p class="error" id="error-msg">Invalid token. Please try again.</p>
    </form>
  </div>
  <script>
    const form = document.getElementById('gate-form');
    const input = document.getElementById('token-input');
    const error = document.getElementById('error-msg');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      error.classList.remove('show');
      const token = input.value.trim();
      if (!token) return;
      try {
        const res = await fetch('/api/auth/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
        });
        if (res.ok) {
          window.location.href = '/';
        } else {
          error.classList.add('show');
          input.value = '';
          input.focus();
        }
      } catch {
        error.classList.add('show');
      }
    });
  </script>
</body>
</html>"""


def auth_routes() -> dict[str, Any]:
    """Return auth-related route handlers to be registered on the app."""
    from fastapi import APIRouter
    from pydantic import BaseModel

    router = APIRouter(prefix="/api/auth", tags=["auth"])

    class TokenRequest(BaseModel):
        token: str

    @router.post("/verify")
    def verify(body: TokenRequest) -> JSONResponse:
        if verify_token(body.token):
            response = JSONResponse(content={"ok": True})
            set_session_cookie(response)
            return response
        return JSONResponse(status_code=401, content={"ok": False, "detail": "Invalid token"})

    @router.get("/status")
    def status(request: Request) -> dict[str, bool]:
        cookie = request.cookies.get(COOKIE_NAME)
        authenticated = bool(cookie and verify_session_cookie(cookie))
        return {"authenticated": authenticated}

    @router.post("/logout")
    def logout() -> JSONResponse:
        response = JSONResponse(content={"ok": True})
        response.delete_cookie(key=COOKIE_NAME)
        return response

    return router
