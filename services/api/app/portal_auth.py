from dataclasses import dataclass
from http.cookies import SimpleCookie

import httpx

from .config import get_settings


class PortalAuthError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PortalIdentity:
    username: str
    display_name: str
    role: str


class PortalAuthClient:
    """Use the existing localhost account service without opening its SQLite file."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None, request_fn=None):
        settings = get_settings()
        self.base_url = (base_url or settings.portal_auth_url).rstrip("/")
        self.timeout = timeout or settings.portal_auth_timeout
        self.request_fn = request_fn or httpx.request

    def _request(self, method: str, path: str, *, token: str = "", payload: dict | None = None, client_ip: str = "") -> httpx.Response:
        headers = {"Accept": "application/json"}
        if token:
            headers["Cookie"] = f"portal_session={token}"
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
        try:
            return self.request_fn(method, f"{self.base_url}{path}", json=payload, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise PortalAuthError("统一账号服务暂时不可用，请稍后重试。") from exc

    @staticmethod
    def _error(response: httpx.Response, fallback: str) -> PortalAuthError:
        try:
            message = response.json().get("error")
        except (ValueError, AttributeError):
            message = None
        return PortalAuthError(message if isinstance(message, str) else fallback, response.status_code)

    @staticmethod
    def _session_token(response: httpx.Response) -> str:
        jar = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            jar.load(header)
        morsel = jar.get("portal_session")
        if not morsel or not morsel.value:
            raise PortalAuthError("统一账号服务没有返回有效会话。")
        return morsel.value

    def login(self, username: str, password: str, client_ip: str = "") -> tuple[str, PortalIdentity]:
        response = self._request(
            "POST", "/api/integrations/ai4science/login",
            payload={"username": username, "password": password}, client_ip=client_ip,
        )
        if response.status_code != 200:
            raise self._error(response, "账号或密码错误。")
        token = self._session_token(response)
        return token, self.me(token, client_ip)

    def register(self, username: str, display_name: str, password: str, client_ip: str = "") -> tuple[str, PortalIdentity]:
        response = self._request(
            "POST", "/api/integrations/ai4science/register",
            payload={"username": username, "display_name": display_name, "password": password}, client_ip=client_ip,
        )
        if response.status_code != 201:
            raise self._error(response, "账号注册失败。")
        token = self._session_token(response)
        return token, self.me(token, client_ip)

    def me(self, token: str, client_ip: str = "") -> PortalIdentity:
        response = self._request("GET", "/api/integrations/ai4science/me", token=token, client_ip=client_ip)
        if response.status_code != 200:
            raise self._error(response, "登录状态已失效，请重新登录。")
        body = response.json()
        return PortalIdentity(
            username=str(body["username"]),
            display_name=str(body.get("display_name") or body["username"]),
            role=str(body.get("role") or "sales"),
        )

    def accounts(self, token: str, client_ip: str = "") -> list[dict]:
        """平台统一账号名册（仅平台管理员可用）。权限中心据此列出全部注册账号。"""
        response = self._request("GET", "/api/accounts", token=token, client_ip=client_ip)
        if response.status_code != 200:
            raise self._error(response, "统一账号名册读取失败，请确认当前账号是平台管理员。")
        try:
            items = response.json().get("items")
        except (ValueError, AttributeError) as exc:
            raise PortalAuthError("统一账号名册返回格式异常。") from exc
        if not isinstance(items, list):
            raise PortalAuthError("统一账号名册返回格式异常。")
        return [item for item in items if isinstance(item, dict)]

    def save_account(self, token: str, payload: dict, client_ip: str = "") -> None:
        """按统一账号服务的规则更新账号；这里只用于开关 AI for Science 的访问权限。"""
        response = self._request("POST", "/api/accounts/save", payload=payload, token=token, client_ip=client_ip)
        if response.status_code != 200:
            raise self._error(response, "统一账号服务拒绝了这次账号更新。")

    def logout(self, token: str, client_ip: str = "") -> None:
        response = self._request("POST", "/api/integrations/ai4science/logout", token=token, payload={}, client_ip=client_ip)
        if response.status_code not in {200, 401}:
            raise self._error(response, "退出失败，请稍后重试。")
