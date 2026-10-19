"""管理员权限中心与研发工作区自举。

这一块解决两件事：

1. 新注册的账号进入工作台时，如果没有任何可访问的研发项目，就自动为它建一个
   属于自己的「AI for Science 研发工作区」；注册完即可开始用，不再卡在
   「当前账号没有可访问的研发项目，请联系管理员配置默认项目」这一步。
2. 管理员在工作台里多了「权限管理」页面：能看到系统里有哪些账号、哪些项目、
   每个项目有哪些成员，并给账号分配 / 调整 / 收回项目角色
   （负责人 owner / 成员 member / 只读 viewer），也可以直接替某个账号新建项目。

要求 27 起权限全面放开（auth.PROJECT_ACCESS_CONTROL = False）：所有登录账号都能访问全部
项目。项目角色（owner / member / viewer）仍然保留，但只用于分工标记与审计追溯，不再作为
访问门槛；本页的授权 / 收回与「开通 / 关闭系统访问」继续可用。
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from .auth import PROJECT_ACCESS_CONTROL, UserContext, get_current_user
from .database import get_db
from .models import AuditEvent, Project, ProjectMember
from .portal_auth import PortalAuthClient, PortalAuthError
from .schemas import ProjectOut

router = APIRouter(prefix="/api/v1/drug-discovery", tags=["administration"])

GENERAL_WORKSPACE_DISEASE = "未选择疾病"
GENERAL_WORKSPACE_NAME = "AI for Science 研发工作区"
GENERAL_WORKSPACE_OBJECTIVE = "按模块选择疾病并开展分析。"

ROLE_LABELS = {"owner": "负责人", "member": "成员", "viewer": "只读"}
ROLE_DESCRIPTIONS = {
    "owner": "可修改项目资料、分配与收回成员权限",
    "member": "可上传数据、运行分析与提交计算任务",
    "viewer": "只能查看项目内容与结果，不能修改",
}
ACCOUNT_ACTIONS = ("auth.register", "auth.login", "auth.logout")
SYSTEM_PERMISSION = "ai4science"
PROJECT_ACCESS_ACTIONS = ("project.entered", "project.module_viewed")

ACTION_LABELS = {
    "auth.register": "注册账号",
    "auth.login": "登录",
    "auth.logout": "退出登录",
    "auth.login_failed": "登录失败",
    "project.entered": "进入项目",
    "project.module_viewed": "查看项目模块",
    "project.created": "创建项目",
    "project.updated": "修改项目",
    "member.upserted": "调整项目成员",
    "member.removed": "移除项目成员",
    "account.system_access": "调整系统访问权限",
    "asset.uploaded": "上传数据文件",
    "dataset.created": "创建数据集",
    "job.created": "提交分析任务",
    "job.cancelled": "取消分析任务",
    "target.updated": "更新靶点",
    "targets.selection_updated": "更新靶点选择",
    "ranking.decided": "提交候选决策",
    "report.created": "生成决策报告",
    "library.imported": "导入分子库",
    "validation.created": "创建验证任务",
    "validation.updated": "更新验证任务",
    "validation.result_imported": "导入验证结果",
    "omics.example_installed": "安装测序示例数据",
    "protein_structure.created": "创建蛋白结构研究",
    "protein_structure.alphafold_loaded": "载入 AlphaFold 结构",
    "process.created": "创建工艺研究",
    "process.pfd_created": "创建 PFD 图纸",
    "process.pfd_version_created": "新建 PFD 图纸版本",
    "process.pfd_updated": "修改 PFD 图纸",
    "process.pfd_ai_intake": "AI 识图生成 PFD",
    "process.hte_created": "创建 HTE 实验",
    "process.hte_executed": "执行 HTE 实验",
    "process.order_assessed": "订单工艺评估",
    "process.scale_assessed": "工艺放大评估",
    "process.portfolio_created": "创建工艺路线组合",
}

VIEW_LABELS = {
    "overview": "项目总览", "data": "数据管理", "omics": "测序数据分析",
    "targets": "靶点发现", "fungal_targets": "真菌靶标", "bacterial_targets": "细菌靶标",
    "perturbation": "虚拟干预", "gene_perturbation": "基因敲除", "virtual_cell": "虚拟细胞",
    "protein_structure": "蛋白质结构", "compounds": "化合物评价",
    "process": "工艺开发与优化", "ranking": "候选决策", "methodology": "数据与方法",
    "report": "决策报告",
}
ROLE_DESCRIPTIONS_FOR_LOG = {"owner": "负责人", "member": "成员", "viewer": "只读"}
JOB_TYPE_LABELS = {
    "target_discovery": "靶点发现", "perturbation": "虚拟干预", "gene_knockout": "基因敲除",
    "virtual_cell": "虚拟细胞", "compound_matching": "化合物匹配", "admet": "ADMET 评价",
    "ranking": "候选决策", "omics_analysis": "测序分析", "fungal_compound": "真菌化合物探索",
    "bacterial_compound": "细菌化合物探索",
}
ACTION_CATEGORY = {
    "auth": "登录 / 退出",
    "project_access": "项目访问",
    "business": "业务操作",
}


class AccountProjectOut(BaseModel):
    id: str
    name: str
    disease: str
    status: str
    role: str
    role_label: str


class AccountOut(BaseModel):
    username: str
    display_name: str
    role_label: str
    project_count: int
    projects: list[AccountProjectOut]
    platform_role_label: str = "研发用户"
    system_enabled: bool | None = None
    account_enabled: bool = True


class MemberOut(BaseModel):
    user_id: str
    role: str
    role_label: str


class RoleOut(BaseModel):
    value: str
    label: str
    description: str


class AdminProjectOut(BaseModel):
    id: str
    name: str
    disease: str
    status: str
    project_type: str
    owner_id: str
    member_count: int
    updated_at: datetime
    members: list[MemberOut]


class AccessOverviewOut(BaseModel):
    accounts: list[AccountOut]
    projects: list[AdminProjectOut]
    roles: list[RoleOut]
    workspace_name: str
    system_roster_available: bool = False
    system_total: int = 0
    system_pending: int = 0
    access_control_enabled: bool = True


class GrantRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=64)
    role: Literal["owner", "member", "viewer"]


class RevokeRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=64)


class AdminProjectCreate(BaseModel):
    owner: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=2, max_length=180)
    disease: str = Field(default=GENERAL_WORKSPACE_DISEASE, min_length=1, max_length=180)
    objective: str = Field(default="", max_length=3000)


class BootstrapRequest(BaseModel):
    preferred_project_id: str | None = Field(default=None, max_length=64)


class SystemAccessRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    enabled: bool


class SystemAccessOut(BaseModel):
    username: str
    system_enabled: bool


class ActivityLogItemOut(BaseModel):
    id: str
    created_at: datetime
    actor_id: str
    actor_name: str
    action: str
    action_label: str
    category: str
    entity_type: str
    entity_id: str
    project_id: str | None = None
    project_name: str | None = None
    detail_summary: str = ""
    ip: str = ""


class ActivityLogStatsOut(BaseModel):
    total: int
    logins: int
    login_failures: int
    today: int
    active_accounts: int


class ActivityLogActionOut(BaseModel):
    value: str
    label: str
    count: int


class ActivityLogAccountOut(BaseModel):
    username: str
    display_name: str


class ActivityLogProjectOut(BaseModel):
    id: str
    name: str


class ActivityLogPageOut(BaseModel):
    items: list[ActivityLogItemOut]
    total: int
    limit: int
    offset: int
    stats: ActivityLogStatsOut
    actions: list[ActivityLogActionOut]
    accounts: list[ActivityLogAccountOut]
    projects: list[ActivityLogProjectOut]


def require_admin(user: UserContext) -> UserContext:
    if user.system_role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可以分配项目权限。")
    return user


def record_event(db: Session, user: UserContext, action: str, entity_type: str, entity_id: str,
                 project_id: str | None = None, details: dict | None = None) -> None:
    db.add(AuditEvent(project_id=project_id, actor_id=user.id, action=action, entity_type=entity_type,
                      entity_id=entity_id, details=details or {}))


def _account_dir(db: Session) -> dict[str, str]:
    """账号 -> 显示名。取登录 / 注册时写下的审计记录，用最新的那一份。"""
    names: dict[str, str] = {}
    rows = db.execute(
        select(AuditEvent.actor_id, AuditEvent.details)
        .where(AuditEvent.action.in_(ACCOUNT_ACTIONS))
        .order_by(AuditEvent.created_at)
    ).all()
    for actor_id, details in rows:
        display = details.get("display_name") if isinstance(details, dict) else None
        if actor_id and isinstance(display, str) and display.strip():
            names[actor_id] = display.strip()
    return names


def _project_members(db: Session) -> tuple[list[Project], dict[str, dict[str, str]]]:
    projects = db.scalars(select(Project).order_by(Project.updated_at.desc())).all()
    members: dict[str, dict[str, str]] = {project.id: {} for project in projects}
    for member in db.scalars(select(ProjectMember)).all():
        if member.project_id in members:
            members[member.project_id][member.user_id] = member.role
    for project in projects:
        # 负责人即使没有成员记录也算 owner，权限判断本来就是这么做的。
        members[project.id].setdefault(project.owner_id, "owner")
    return projects, members


def _project_out(project: Project, members: dict[str, str]) -> AdminProjectOut:
    return AdminProjectOut(
        id=project.id,
        name=project.name,
        disease=project.disease,
        status=project.status,
        project_type=project.project_type,
        owner_id=project.owner_id,
        member_count=len(members),
        updated_at=project.updated_at,
        members=[MemberOut(user_id=user_id, role=role, role_label=ROLE_LABELS.get(role, role))
                 for user_id, role in sorted(members.items())],
    )


def _load_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在或已被删除。")
    return project


def _portal_roster(token: str | None) -> list[dict] | None:
    """平台统一账号名册（全部注册账号）。读不到时返回 None，界面退回审计表里的账号。"""
    if not token:
        return None
    try:
        return PortalAuthClient().accounts(token)
    except PortalAuthError:
        return None


def _account_entries(projects: list[Project], members: dict[str, dict[str, str]]) -> dict[str, list[AccountProjectOut]]:
    by_user: dict[str, list[AccountProjectOut]] = {}
    for project in projects:
        for user_id, role in members[project.id].items():
            by_user.setdefault(user_id, []).append(AccountProjectOut(
                id=project.id, name=project.name, disease=project.disease, status=project.status,
                role=role, role_label=ROLE_LABELS.get(role, role),
            ))
    for entries in by_user.values():
        entries.sort(key=lambda item: item.name)
    return by_user


def _project_role_label(entries: list[AccountProjectOut]) -> str:
    roles = {item.role for item in entries}
    primary = "owner" if "owner" in roles else ("member" if "member" in roles else "viewer")
    return ROLE_LABELS.get(primary, primary) if entries else "未分配项目"


@router.get("/admin/access", response_model=AccessOverviewOut)
def access_overview(db: Session = Depends(get_db), user: UserContext = Depends(get_current_user),
                    ai4science_session: str | None = Cookie(default=None)):
    require_admin(user)
    projects, members = _project_members(db)
    names = _account_dir(db)
    by_user = _account_entries(projects, members)
    roster = _portal_roster(ai4science_session)
    accounts: list[AccountOut] = []
    seen: set[str] = set()

    def add_account(username: str, display_name: str, platform_role: str,
                    system_enabled: bool | None, account_enabled: bool) -> None:
        if not username or username in seen:
            return
        seen.add(username)
        entries = by_user.get(username, [])
        accounts.append(AccountOut(
            username=username,
            display_name=display_name or username,
            role_label=_project_role_label(entries),
            project_count=len(entries),
            projects=entries,
            platform_role_label=platform_role,
            system_enabled=system_enabled,
            account_enabled=account_enabled,
        ))

    if roster:
        # 平台管理员排在最前面，其余按注册时间；姓名优先取统一账号服务里的。
        for item in sorted(roster, key=lambda entry: (entry.get("role") != "admin", str(entry.get("created_at") or ""))):
            username = str(item.get("username") or "").strip()
            permissions = [str(value) for value in (item.get("permissions") or [])]
            is_admin = item.get("role") == "admin"
            add_account(
                username,
                str(item.get("display_name") or names.get(username) or ""),
                "平台管理员" if is_admin else "研发用户",
                bool(is_admin or SYSTEM_PERMISSION in permissions),
                bool(item.get("enabled", True)),
            )
    for username, display_name in sorted(names.items()):
        add_account(username, display_name, "研发用户", None, True)
    for username in sorted(by_user):
        # 有项目、但在名册和审计里都没有的账号也不能漏（例如项目负责人）。
        add_account(username, names.get(username, username), "研发用户", None, True)

    return AccessOverviewOut(
        accounts=accounts,
        projects=[_project_out(project, members[project.id]) for project in projects],
        roles=[RoleOut(value=value, label=ROLE_LABELS[value], description=ROLE_DESCRIPTIONS[value])
               for value in ("owner", "member", "viewer")],
        workspace_name=GENERAL_WORKSPACE_NAME,
        system_roster_available=bool(roster),
        system_total=len(roster) if roster else 0,
        system_pending=sum(1 for item in accounts if item.system_enabled is False),
        access_control_enabled=PROJECT_ACCESS_CONTROL,
    )


@router.post("/admin/access/system", response_model=SystemAccessOut)
def set_system_access(payload: SystemAccessRequest, db: Session = Depends(get_db),
                      user: UserContext = Depends(get_current_user),
                      ai4science_session: str | None = Cookie(default=None)):
    """开通 / 关闭某个账号对 AI for Science 的访问权限（走平台统一账号服务，可审计）。"""
    require_admin(user)
    if not ai4science_session:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="统一账号服务会话不可用，请退出后重新登录再试。")
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="请填写要开通的账号。")
    client = PortalAuthClient()
    try:
        roster = client.accounts(ai4science_session)
    except PortalAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    record = next((item for item in roster if str(item.get("username") or "").strip() == username), None)
    if not record:
        raise HTTPException(status_code=404, detail="统一账号服务里没有这个账号。")
    permissions = [str(value) for value in (record.get("permissions") or []) if str(value) != SYSTEM_PERMISSION]
    if payload.enabled:
        permissions.append(SYSTEM_PERMISSION)
    try:
        client.save_account(ai4science_session, {
            "username": username,
            "display_name": str(record.get("display_name") or username),
            "role": str(record.get("role") or "sales"),
            "enabled": bool(record.get("enabled", True)),
            "permissions": permissions,
            "password": "",
        })
    except PortalAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    record_event(db, user, "account.system_access", "account", username,
                 details={"system": SYSTEM_PERMISSION, "enabled": payload.enabled, "via": "admin-access-center"})
    db.commit()
    return SystemAccessOut(username=username, system_enabled=payload.enabled)


@router.post("/admin/access/grant", response_model=AdminProjectOut, status_code=201)
def grant_access(payload: GrantRequest, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_admin(user)
    project = _load_project(db, payload.project_id)
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="请填写要授权的账号。")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id,
                                                  ProjectMember.user_id == username))
    if member:
        member.role = payload.role
    else:
        db.add(ProjectMember(project_id=project.id, user_id=username, role=payload.role))
    record_event(db, user, "member.upserted", "project_member", username, project.id,
                 {"role": payload.role, "via": "admin-access-center"})
    db.commit()
    return _project_out(project, _project_members(db)[1][project.id])


@router.post("/admin/access/revoke", response_model=AdminProjectOut)
def revoke_access(payload: RevokeRequest, db: Session = Depends(get_db), user: UserContext = Depends(get_current_user)):
    require_admin(user)
    project = _load_project(db, payload.project_id)
    username = payload.username.strip()
    if username == project.owner_id:
        raise HTTPException(status_code=409, detail="项目负责人的权限不能在这里收回；请先把项目交给其他负责人。")
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == project.id,
                                                  ProjectMember.user_id == username))
    if member:
        db.delete(member)
        record_event(db, user, "member.removed", "project_member", username, project.id,
                     {"via": "admin-access-center"})
        db.commit()
    return _project_out(project, _project_members(db)[1][project.id])


@router.post("/admin/projects", response_model=AdminProjectOut, status_code=201)
def create_project_for_account(payload: AdminProjectCreate, db: Session = Depends(get_db),
                               user: UserContext = Depends(get_current_user)):
    require_admin(user)
    owner = payload.owner.strip()
    if not owner:
        raise HTTPException(status_code=400, detail="请填写项目所属账号。")
    project = Project(
        name=payload.name.strip(),
        disease=payload.disease.strip() or GENERAL_WORKSPACE_DISEASE,
        disease_id=None,
        objective=payload.objective.strip(),
        status="active",
        project_type="research",
        research_track="full",
        owner_id=owner,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner, role="owner"))
    record_event(db, user, "project.created", "project", project.id, project.id,
                 {"owner": owner, "via": "admin-access-center"})
    db.commit()
    db.refresh(project)
    return _project_out(project, _project_members(db)[1][project.id])


@router.post("/workspace/bootstrap", response_model=ProjectOut)
def workspace_bootstrap(payload: BootstrapRequest | None = None, db: Session = Depends(get_db),
                        user: UserContext = Depends(get_current_user)):
    """进入工作台时调用：返回一个可访问的项目，没有就建一个个人工作区。"""
    # 要求 27：权限全面放开，所有账号都能看到全部研发项目；仍优先返回本人项目 / 指定项目。
    query = select(Project).where(Project.project_type == "research", Project.status == "active") \
        .order_by(Project.updated_at.desc())
    visible = db.scalars(query).all()
    preferred = payload.preferred_project_id if payload else None
    if preferred:
        for project in visible:
            if project.id == preferred:
                return project
    for project in visible:
        if project.owner_id == user.id:
            return project
    if visible:
        return visible[0]
    project = Project(
        name=GENERAL_WORKSPACE_NAME,
        disease=GENERAL_WORKSPACE_DISEASE,
        disease_id=None,
        objective=GENERAL_WORKSPACE_OBJECTIVE,
        status="active",
        project_type="research",
        research_track="full",
        owner_id=user.id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    record_event(db, user, "project.created", "project", project.id, project.id,
                 {"reason": "workspace-bootstrap"})
    db.commit()
    db.refresh(project)
    return project

# ---------------------------------------------------------------------------
# 管理员操作日志
# ---------------------------------------------------------------------------
def _action_label(action: str) -> str:
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    if action.startswith("process.pfd"):
        return "PFD 图纸操作"
    if action.startswith("process."):
        return "工艺开发操作"
    if action.startswith("protein_structure."):
        return "蛋白结构操作"
    if action.startswith("auth."):
        return "账号操作"
    return action


def _action_category(action: str) -> str:
    if action.startswith("auth."):
        return "auth"
    if action in PROJECT_ACCESS_ACTIONS:
        return "project_access"
    return "business"


def _brief_value(key: str, value) -> str | None:
    if value is None or value == "":
        return None
    if key == "role":
        return ROLE_DESCRIPTIONS_FOR_LOG.get(str(value), str(value))
    if key == "type":
        return JOB_TYPE_LABELS.get(str(value), str(value))
    if key == "enabled":
        return "已开通" if bool(value) else "已关闭"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value[:120]
    if isinstance(value, list):
        return f"{len(value)} 项"
    if isinstance(value, dict):
        return f"{len(value)} 项"
    return str(value)[:120]


def _detail_summary(action: str, details: dict | None) -> str:
    data = details if isinstance(details, dict) else {}
    if action == "project.entered":
        return "打开项目工作台"
    if action == "project.module_viewed":
        view = str(data.get("view") or "")
        return f"查看模块：{VIEW_LABELS.get(view, view or '未命名模块')}"
    if action in {"auth.login", "auth.logout", "auth.register"}:
        provider = {"portal": "统一账号方式", "dev": "本地预览方式"}.get(str(data.get("provider")), "")
        return provider or "账号操作"
    if action == "auth.login_failed":
        return str(data.get("reason") or "账号或密码校验失败")

    labels = {
        "role": "角色", "type": "任务类型", "mode": "运行模式", "owner": "所属账号",
        "enabled": "系统访问", "system": "系统", "reason": "原因", "version": "版本",
        "size": "文件大小", "hash": "文件摘要", "target": "靶点", "view": "页面",
        "status": "状态", "accepted": "接收数量", "routes": "路线数",
    }
    skip = {"display_name", "ip", "user_agent", "provider", "via", "project_name"}
    parts: list[str] = []
    for key, value in data.items():
        if key in skip or len(parts) >= 4:
            continue
        brief = _brief_value(str(key), value)
        if brief:
            parts.append(f"{labels.get(str(key), str(key))}：{brief}")
    return "；".join(parts)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@router.get("/admin/logs", response_model=ActivityLogPageOut)
def activity_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    username: str | None = Query(default=None, max_length=120),
    action: str | None = Query(default=None, max_length=100),
    project_id: str | None = Query(default=None, max_length=64),
    category: Literal["all", "auth", "project_access", "business"] | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    keyword: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    user: UserContext = Depends(get_current_user),
):
    """管理员查看全系统操作日志；登录、进入项目、业务操作都在这里可追溯。"""
    require_admin(user)
    filters = []
    if username and username.strip():
        filters.append(AuditEvent.actor_id == username.strip())
    if action and action.strip():
        filters.append(AuditEvent.action == action.strip())
    if project_id and project_id.strip():
        filters.append(AuditEvent.project_id == project_id.strip())
    if category == "auth":
        filters.append(AuditEvent.action.like("auth.%"))
    elif category == "project_access":
        filters.append(AuditEvent.action.in_(PROJECT_ACCESS_ACTIONS))
    elif category == "business":
        filters.append(~AuditEvent.action.like("auth.%"))
        filters.append(~AuditEvent.action.in_(PROJECT_ACCESS_ACTIONS))
    if start is not None:
        filters.append(AuditEvent.created_at >= _as_utc(start))
    if end is not None:
        filters.append(AuditEvent.created_at <= _as_utc(end))
    if keyword and keyword.strip():
        term = f"%{keyword.strip()}%"
        filters.append(or_(
            AuditEvent.actor_id.ilike(term),
            AuditEvent.action.ilike(term),
            AuditEvent.entity_id.ilike(term),
            cast(AuditEvent.details, String).ilike(term),
        ))

    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0
    rows = db.scalars(
        select(AuditEvent).where(*filters)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(limit).offset(offset)
    ).all()

    project_rows = db.execute(select(Project.id, Project.name)).all()
    project_names = {row[0]: row[1] for row in project_rows}
    names = _account_dir(db)
    actor_rows = db.scalars(select(AuditEvent.actor_id).distinct()).all()
    accounts = [
        ActivityLogAccountOut(username=actor, display_name=names.get(actor, actor))
        for actor in sorted({item for item in actor_rows if item})
    ]

    items = []
    for row in rows:
        details = row.details if isinstance(row.details, dict) else {}
        items.append(ActivityLogItemOut(
            id=row.id,
            created_at=_as_utc(row.created_at),
            actor_id=row.actor_id,
            actor_name=names.get(row.actor_id, ""),
            action=row.action,
            action_label=_action_label(row.action),
            category=_action_category(row.action),
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            project_id=row.project_id,
            project_name=project_names.get(row.project_id) if row.project_id else None,
            detail_summary=_detail_summary(row.action, details),
            ip=str(details.get("ip") or ""),
        ))

    action_counts = db.execute(
        select(AuditEvent.action, func.count())
        .group_by(AuditEvent.action)
        .order_by(func.count().desc(), AuditEvent.action)
    ).all()
    actions = [
        ActivityLogActionOut(value=value, label=_action_label(value), count=count)
        for value, count in action_counts
    ]

    beijing = timezone(timedelta(hours=8))
    now_beijing = datetime.now(beijing)
    today_start = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    active_cutoff = now_beijing - timedelta(days=7)
    stats = ActivityLogStatsOut(
        total=db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
        logins=db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "auth.login")) or 0,
        login_failures=db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "auth.login_failed")) or 0,
        today=db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.created_at >= today_start)) or 0,
        active_accounts=db.scalar(
            select(func.count(func.distinct(AuditEvent.actor_id)))
            .where(AuditEvent.created_at >= active_cutoff)
        ) or 0,
    )

    return ActivityLogPageOut(
        items=items, total=total, limit=limit, offset=offset, stats=stats,
        actions=actions, accounts=accounts,
        projects=[ActivityLogProjectOut(id=pid, name=name) for pid, name in project_rows],
    )
