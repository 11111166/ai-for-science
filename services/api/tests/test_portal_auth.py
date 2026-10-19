import json

import httpx
import pytest

from app.portal_auth import PortalAuthClient, PortalAuthError


def response(status: int, body: dict, *, cookie: str = "") -> httpx.Response:
    headers = {"content-type": "application/json"}
    if cookie:
        headers["set-cookie"] = cookie
    return httpx.Response(status, content=json.dumps(body).encode(), headers=headers)


def test_login_returns_opaque_session_and_identity():
    calls = []

    def request_fn(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/login"):
            return response(200, {"ok": True}, cookie="portal_session=signed-token; Path=/; HttpOnly")
        return response(200, {"username": "rd_user", "display_name": "研发一组", "role": "sales"})

    token, identity = PortalAuthClient("http://127.0.0.1:8790", request_fn=request_fn).login("rd_user", "private-pass", "10.0.0.5")
    assert token == "signed-token"
    assert identity.username == "rd_user"
    assert identity.display_name == "研发一组"
    assert calls[0][2]["json"] == {"username": "rd_user", "password": "private-pass"}
    assert calls[1][2]["headers"]["Cookie"] == "portal_session=signed-token"
    assert calls[0][2]["headers"]["X-Forwarded-For"] == "10.0.0.5"


def test_login_preserves_portal_error_without_exposing_credentials():
    def request_fn(*_args, **_kwargs):
        return response(401, {"error": "账号或密码错误"})

    with pytest.raises(PortalAuthError, match="账号或密码错误") as error:
        PortalAuthClient(request_fn=request_fn).login("missing", "secret")
    assert error.value.status_code == 401


def test_register_requires_session_cookie():
    def request_fn(*_args, **_kwargs):
        return response(201, {"ok": True})

    with pytest.raises(PortalAuthError, match="没有返回有效会话"):
        PortalAuthClient(request_fn=request_fn).register("new_user", "新用户", "secret-123")
