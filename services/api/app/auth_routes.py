from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import DEV_IDENTITY_COOKIE, UserContext, get_current_user, require_trusted_proxy
from .config import get_settings
from .database import get_db
from .models import AuditEvent
from .portal_auth import PortalAuthClient, PortalAuthError, PortalIdentity


router = APIRouter(prefix="/api/v1/drug-discovery/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=200)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=2, max_length=60)
    password: str = Field(min_length=6, max_length=200)


class AuthUser(BaseModel):
    username: str
    display_name: str
    role: str


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "")


def public_identity(identity: PortalIdentity) -> AuthUser:
    return AuthUser(
        username=identity.username,
        display_name=identity.display_name,
        role="管理员" if identity.role == "admin" else "研发用户",
    )


def request_details(request: Request, provider: str) -> dict:
    """登录相关日志共用的请求元数据；不记录密码或会话令牌。"""
    return {
        "provider": provider,
        "ip": client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:300],
    }


def remember_account(db: Session, username: str, display_name: str, action: str,
                     request: Request | None = None, extra: dict | None = None) -> None:
    """把注册 / 登录 / 退出写进审计表，管理员权限中心据此列出账号与操作日志。"""
    details = {"display_name": display_name}
    if extra:
        details.update(extra)
    if request is not None:
        details.update(request_details(request, str(details.get("provider") or "portal")))
    db.add(AuditEvent(project_id=None, actor_id=username[:120], action=action, entity_type="account",
                      entity_id=username[:120], details=details))
    db.commit()


def remember_login_failure(db: Session, username: str, request: Request, reason: str,
                           status_code: int, provider: str) -> None:
    """登录失败也留痕；日志失败不能反过来阻断登录响应。"""
    try:
        details = request_details(request, provider)
        details.update({"reason": reason[:300], "status_code": status_code, "display_name": username})
        db.add(AuditEvent(project_id=None, actor_id=(username or "unknown")[:120],
                          action="auth.login_failed", entity_type="account",
                          entity_id=(username or "unknown")[:120], details=details))
        db.commit()
    except Exception:
        db.rollback()


def lowercase_cookie_paths(response: Response) -> None:
    """Rewrite every Set-Cookie header in place, preserving all cookies."""
    raw = response.headers.raw
    for index, (name, value) in enumerate(raw):
        if name.lower() == b"set-cookie":
            raw[index] = (name, value.replace(b"Path=", b"path="))


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.auth_session_cookie,
        token,
        max_age=settings.auth_session_ttl,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/apps/ai4science",
    )
    # The shared gateway rewrites uppercase ``Path=/`` for generic upstreams.
    # Cookie attributes are case-insensitive, so lowercase keeps the intended
    # AI mount path while avoiding a rewrite to the internal upstream label.
    lowercase_cookie_paths(response)


def set_dev_identity_cookie(response: Response, username: str) -> None:
    """Local preview only: keep the account entered on the login page."""
    response.set_cookie(
        DEV_IDENTITY_COOKIE,
        username[:120],
        max_age=get_settings().auth_session_ttl,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    lowercase_cookie_paths(response)


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().auth_session_cookie, path="/apps/ai4science")
    response.delete_cookie(DEV_IDENTITY_COOKIE, path="/")
    lowercase_cookie_paths(response)


def require_portal_provider() -> None:
    if get_settings().auth_provider != "portal":
        raise HTTPException(status_code=503, detail="统一账号登录尚未启用。")


@router.post("/login", response_model=AuthUser, dependencies=[Depends(require_trusted_proxy)])
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if settings.auth_provider == "dev" and settings.app_env in {"development", "test"}:
        # Local development preview only. The dev provider already trusts the X-Dev-User
        # header, so accept the local credentials instead of calling the company portal.
        # Production forbids the dev provider (see Settings.enforce_production_auth).
        name = payload.username.strip()
        if not name:
            raise HTTPException(status_code=400, detail="请填写账号。")
        set_dev_identity_cookie(response, name)
        remember_account(db, name, name, "auth.login", request=request, extra={"provider": "dev"})
        return AuthUser(username=name, display_name=name, role="管理员")
    require_portal_provider()
    username = payload.username.strip()
    try:
        token, identity = PortalAuthClient().login(username, payload.password, client_ip(request))
    except PortalAuthError as exc:
        remember_login_failure(db, username, request, str(exc), exc.status_code, "portal")
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    set_session_cookie(response, token)
    remember_account(db, identity.username, identity.display_name, "auth.login",
                     request=request, extra={"provider": "portal"})
    return public_identity(identity)


@router.post("/register", response_model=AuthUser, status_code=201, dependencies=[Depends(require_trusted_proxy)])
def register(payload: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if settings.auth_provider == "dev" and settings.app_env in {"development", "test"}:
        set_dev_identity_cookie(response, payload.username)
        remember_account(db, payload.username, payload.display_name, "auth.register",
                         request=request, extra={"provider": "dev"})
        return AuthUser(username=payload.username, display_name=payload.display_name, role="研发用户")
    require_portal_provider()
    try:
        token, identity = PortalAuthClient().register(
            payload.username.strip(), payload.display_name.strip(), payload.password, client_ip(request),
        )
    except PortalAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    set_session_cookie(response, token)
    remember_account(db, identity.username, identity.display_name, "auth.register",
                     request=request, extra={"provider": "portal"})
    return public_identity(identity)


@router.get("/me", response_model=AuthUser)
def me(user: Annotated[UserContext, Depends(get_current_user)]):
    return AuthUser(username=user.id, display_name=user.name, role="管理员" if user.system_role == "admin" else "研发用户")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_trusted_proxy)])
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    token = request.cookies.get(settings.auth_session_cookie, "")
    actor = ""
    display_name = ""
    provider = settings.auth_provider
    if settings.auth_provider == "portal" and token:
        client = PortalAuthClient()
        try:
            identity = client.me(token, client_ip(request))
            actor, display_name = identity.username, identity.display_name
        except PortalAuthError:
            pass
        try:
            client.logout(token, client_ip(request))
        except PortalAuthError:
            pass
    elif settings.auth_provider == "dev":
        actor = (request.headers.get("x-dev-user") or request.cookies.get(DEV_IDENTITY_COOKIE) or "").strip()
        display_name = actor
    if actor:
        try:
            remember_account(db, actor, display_name or actor, "auth.logout",
                             request=request, extra={"provider": provider})
        except Exception:
            db.rollback()
    clear_session_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return None
