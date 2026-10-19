from dataclasses import dataclass
import hmac

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import Project, ProjectMember
from .portal_auth import PortalAuthClient, PortalAuthError


DEV_IDENTITY_COOKIE = "ai4science_dev_identity"


@dataclass(frozen=True)
class UserContext:
    id: str
    name: str
    system_role: str


def require_trusted_proxy(x_ai4science_proxy_secret: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if settings.app_env != "production":
        return
    supplied_secret = x_ai4science_proxy_secret or ""
    if not settings.proxy_auth_secret or not hmac.compare_digest(supplied_secret, settings.proxy_auth_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Trusted platform entry is required")


def get_current_user(
    request: Request,
    x_dev_user: str | None = Header(default=None),
    x_dev_role: str | None = Header(default=None),
    x_platform_user: str | None = Header(default=None),
    x_platform_name: str | None = Header(default=None),
    x_platform_role: str | None = Header(default=None),
    x_ai4science_proxy_secret: str | None = Header(default=None),
    ai4science_session: str | None = Cookie(default=None),
    ai4science_dev_identity: str | None = Cookie(default=None),
) -> UserContext:
    verified = getattr(request.state, "verified_user", None)
    if isinstance(verified, UserContext):
        return verified
    settings = get_settings()
    if settings.auth_provider == "portal":
        supplied_secret = x_ai4science_proxy_secret or ""
        if not settings.proxy_auth_secret or not hmac.compare_digest(supplied_secret, settings.proxy_auth_secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Trusted platform identity is required")
        if not ai4science_session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录 AI for Science。")
        try:
            identity = PortalAuthClient().me(ai4science_session, request.client.host if request.client else "")
        except PortalAuthError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        role = "admin" if identity.role == "admin" else "researcher"
        return UserContext(id=identity.username, name=identity.display_name, system_role=role)
    if settings.auth_provider == "proxy":
        supplied_secret = x_ai4science_proxy_secret or ""
        if not settings.proxy_auth_secret or not hmac.compare_digest(supplied_secret, settings.proxy_auth_secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Trusted platform identity is required")
        user_id = (x_platform_user or "").strip()
        role = (x_platform_role or "viewer").strip().lower()
        if not user_id or role not in {"admin", "researcher", "viewer"}:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid platform identity")
        return UserContext(id=user_id, name=(x_platform_name or user_id).strip(), system_role=role)
    if settings.auth_provider == "dev":
        if settings.app_env not in {"development", "test"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Development identity is disabled")
        # Local preview: the explicit header remains available for tests and tooling,
        # while a real browser login stores its account in an HttpOnly dev cookie.
        user_id = (x_dev_user or ai4science_dev_identity or "").strip()
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录 AI for Science。")
        role = (x_dev_role or "admin").strip().lower()
        if role not in {"admin", "researcher", "viewer"}:
            raise HTTPException(status_code=400, detail="Invalid development role")
        return UserContext(id=user_id, name=user_id, system_role=role)
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Platform AuthProvider is not configured")


# 要求 27：业务方要求「所有账号都能正常使用所有项目」，因此默认不再按项目成员身份拦截。
# 保持为 False 即全面放开（任何登录账号都能访问全部项目）；改成 True 可恢复原来的
# 「管理员 / 负责人 / 项目成员」分级访问控制，回滚时只需要动这一行。
PROJECT_ACCESS_CONTROL = False
ROLE_WEIGHT = {"viewer": 1, "member": 2, "owner": 3}


def require_project_access(db: Session, project_id: str, user: UserContext, minimum: str = "viewer") -> Project:
    """项目访问校验：放开模式下只校验项目是否存在；分级模式下按成员角色判断。"""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not PROJECT_ACCESS_CONTROL:
        return project
    if user.system_role == "admin" or project.owner_id == user.id:
        return project
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id))
    if not member or ROLE_WEIGHT.get(member.role, 0) < ROLE_WEIGHT[minimum]:
        raise HTTPException(status_code=403, detail="Project access denied")
    return project
