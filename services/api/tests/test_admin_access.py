"""账号权限：新账号自举可用 + 管理员的项目授权中心。

这一组测试用独立的临时数据库（dependency_overrides 覆盖 get_db），
不碰进程里其它测试共用的引擎，也不碰真实库。
"""
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import admin_routes
from app.database import Base, get_db
from app.main import app
from app.models import AuditEvent, Project, ProjectMember
from app.portal_auth import PortalAuthError

DB_PATH = Path(tempfile.gettempdir()) / "ai4science_admin_access_test.db"
engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}", connect_args={"check_same_thread": False})
TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def override_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_db
client = TestClient(app)

ADMIN = {"X-Dev-User": "root_admin", "X-Dev-Role": "admin"}
KATE = {"X-Dev-User": "kate_new", "X-Dev-Role": "researcher"}
BOB = {"X-Dev-User": "bob_guest", "X-Dev-Role": "researcher"}
BASE = "/api/v1/drug-discovery"


def setup_module():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_module():
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(engine)
    engine.dispose()
    DB_PATH.unlink(missing_ok=True)


def test_new_account_bootstraps_its_own_workspace():
    """注册完直接进工作台：没有可访问项目时自动建个人工作区，而不是卡住。"""
    first = client.post(f"{BASE}/workspace/bootstrap", json={}, headers=KATE)
    assert first.status_code == 200, first.text
    project = first.json()
    assert project["owner_id"] == "kate_new"
    assert project["project_type"] == "research"
    assert project["status"] == "active"

    listed = client.get(f"{BASE}/projects", headers=KATE).json()
    assert [item["id"] for item in listed] == [project["id"]]

    # 再次进入不会重复建项目，并且能按 id 记住上次打开的项目
    again = client.post(f"{BASE}/workspace/bootstrap",
                        json={"preferred_project_id": project["id"]}, headers=KATE)
    assert again.status_code == 200
    assert again.json()["id"] == project["id"]
    assert len(client.get(f"{BASE}/projects", headers=KATE).json()) == 1


def test_access_center_is_admin_only():
    assert client.get(f"{BASE}/admin/access", headers=KATE).status_code == 403
    assert client.get(f"{BASE}/admin/access", headers=ADMIN).status_code == 200


def test_a_brand_new_account_can_use_every_project():
    """要求 27：权限全面放开，任何账号都能查看、修改并使用别人的项目。"""
    created = client.post(f"{BASE}/admin/projects",
                          json={"owner": "kate_new", "name": "权限测试项目", "disease": "未选择疾病"},
                          headers=ADMIN)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    assert created.json()["owner_id"] == "kate_new"
    assert created.json()["members"][0]["user_id"] == "kate_new"

    # 一个从来没被授权过的账号，打开项目就是 200，不再需要先授权
    fresh = {"X-Dev-User": "brand_new_account", "X-Dev-Role": "researcher"}
    assert client.get(f"{BASE}/projects/{project_id}", headers=fresh).status_code == 200
    assert client.get(f"{BASE}/projects/{project_id}/members", headers=fresh).status_code == 200
    edited = client.patch(f"{BASE}/projects/{project_id}", json={"objective": "被其他账号修改"}, headers=fresh)
    assert edited.status_code == 200, edited.text
    assert edited.json()["objective"] == "被其他账号修改"

    # 项目列表对任何账号都不再过滤
    listed = client.get(f"{BASE}/projects", params={"limit": 100}, headers=fresh).json()
    assert project_id in {item["id"] for item in listed}

    # 授权 / 收回仍然可用，但只作为分工标记，不再构成访问门槛
    granted = client.post(f"{BASE}/admin/access/grant",
                          json={"username": "bob_guest", "project_id": project_id, "role": "viewer"},
                          headers=ADMIN)
    assert granted.status_code == 201, granted.text
    assert {"user_id": "bob_guest", "role": "viewer", "role_label": "只读"} in granted.json()["members"]
    assert client.get(f"{BASE}/projects/{project_id}", headers=BOB).status_code == 200

    upgraded = client.post(f"{BASE}/admin/access/grant",
                           json={"username": "bob_guest", "project_id": project_id, "role": "member"},
                           headers=ADMIN)
    assert upgraded.status_code == 201
    roles = {item["user_id"]: item["role"] for item in upgraded.json()["members"]}
    assert roles["bob_guest"] == "member"

    revoked = client.post(f"{BASE}/admin/access/revoke",
                          json={"username": "bob_guest", "project_id": project_id}, headers=ADMIN)
    assert revoked.status_code == 200
    assert "bob_guest" not in {item["user_id"] for item in revoked.json()["members"]}
    # 收回授权之后，访问照旧（放开模式下所有人可用全部项目）
    assert client.get(f"{BASE}/projects/{project_id}", headers=BOB).status_code == 200


def test_owner_permission_cannot_be_revoked_from_the_center():
    created = client.post(f"{BASE}/admin/projects",
                          json={"owner": "kate_new", "name": "负责人保护测试", "disease": "未选择疾病"},
                          headers=ADMIN)
    project_id = created.json()["id"]
    refused = client.post(f"{BASE}/admin/access/revoke",
                          json={"username": "kate_new", "project_id": project_id}, headers=ADMIN)
    assert refused.status_code == 409
    assert "负责人" in refused.json()["detail"]


def test_overview_lists_accounts_projects_and_roles():
    payload = client.get(f"{BASE}/admin/access", headers=ADMIN).json()
    assert payload["access_control_enabled"] is False, "本轮权限已全面放开"
    assert {role["value"] for role in payload["roles"]} == {"owner", "member", "viewer"}
    accounts = {item["username"]: item for item in payload["accounts"]}
    assert "kate_new" in accounts
    assert accounts["kate_new"]["project_count"] >= 1
    projects = {item["name"]: item for item in payload["projects"]}
    assert "权限测试项目" in projects
    assert projects["权限测试项目"]["member_count"] >= 1


def test_login_records_account_display_name():
    """登录 / 注册时把账号与姓名写进审计表，权限中心才能列出真实姓名。"""
    recorded = client.post(f"{BASE}/auth/login",
                           json={"username": "kate_new", "password": "local-preview"}, headers=KATE)
    assert recorded.status_code == 200
    with TestingSession() as db:
        details = {row.actor_id: row.details for row in db.scalars(select(AuditEvent)).all()}
    assert details.get("kate_new", {}).get("display_name") == "kate_new"


class _FakePortalAuthClient:
    """假的平台统一账号服务：记录调用，供断言使用。"""

    roster: list[dict] = []
    saved: list[dict] = []
    fail_roster = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    def accounts(self, token: str, client_ip: str = "") -> list[dict]:
        if _FakePortalAuthClient.fail_roster:
            raise PortalAuthError("统一账号名册读取失败。")
        return [dict(item) for item in _FakePortalAuthClient.roster]

    def save_account(self, token: str, payload: dict, client_ip: str = "") -> None:
        _FakePortalAuthClient.saved.append(dict(payload))


def _use_fake_portal(monkeypatch, roster, fail=False):
    monkeypatch.setattr(admin_routes, "PortalAuthClient", _FakePortalAuthClient)
    _FakePortalAuthClient.roster = roster
    _FakePortalAuthClient.saved = []
    _FakePortalAuthClient.fail_roster = fail


def test_overview_falls_back_to_local_records_when_roster_is_unavailable(monkeypatch):
    """统一账号服务读不到时，页面退回本地记录，并如实标注（不假装知道系统权限）。"""
    _use_fake_portal(monkeypatch, [], fail=True)
    payload = client.get(f"{BASE}/admin/access", headers=ADMIN).json()
    assert payload["system_roster_available"] is False
    assert payload["system_total"] == 0
    accounts = {item["username"]: item for item in payload["accounts"]}
    assert accounts["kate_new"]["system_enabled"] is None
    assert accounts["kate_new"]["platform_role_label"] == "研发用户"


def test_overview_merges_the_whole_platform_roster(monkeypatch):
    """名册来自统一账号服务：含从未登录过、已停用、还没开通本系统的账号。"""
    _use_fake_portal(monkeypatch, [
        {"username": "kate_new", "display_name": "凯特研究员", "role": "sales", "enabled": True,
         "permissions": [], "created_at": "2026-09-01 10:00:00"},
        {"username": "111111", "display_name": "系统管理员", "role": "admin", "enabled": True,
         "permissions": [], "created_at": "2026-07-31 12:58:24"},
        {"username": "never_logged_in", "display_name": "还没登录过", "role": "sales", "enabled": True,
         "permissions": ["procurement", "ai4science"], "created_at": "2026-09-30 09:00:00"},
        {"username": "disabled_one", "display_name": "已停用账号", "role": "sales", "enabled": False,
         "permissions": [], "created_at": "2026-09-30 09:10:00"},
    ])
    payload = client.get(f"{BASE}/admin/access", headers=ADMIN,
                         cookies={"ai4science_session": "portal-token"}).json()
    assert payload["system_roster_available"] is True
    assert payload["system_total"] == 4
    assert payload["access_control_enabled"] is False
    accounts = {item["username"]: item for item in payload["accounts"]}
    assert [item["username"] for item in payload["accounts"]][0] == "111111"
    assert accounts["kate_new"]["display_name"] == "凯特研究员"
    assert accounts["kate_new"]["system_enabled"] is False
    assert accounts["kate_new"]["project_count"] >= 1
    assert accounts["111111"]["system_enabled"] is True
    assert accounts["111111"]["platform_role_label"] == "平台管理员"
    assert accounts["never_logged_in"]["system_enabled"] is True
    assert accounts["never_logged_in"]["project_count"] == 0
    assert accounts["disabled_one"]["system_enabled"] is False
    assert accounts["disabled_one"]["account_enabled"] is False
    assert payload["system_pending"] == 2


def test_admin_opens_and_closes_the_platform_access(monkeypatch):
    """管理员一键开通 / 关闭某个账号的 AI for Science 访问权限。"""
    _use_fake_portal(monkeypatch, [
        {"username": "newcomer", "display_name": "新同学", "role": "sales", "enabled": True,
         "permissions": ["procurement"], "created_at": "2026-10-01 09:00:00"},
    ])
    cookie = {"ai4science_session": "portal-token"}
    assert client.post(f"{BASE}/admin/access/system", json={"username": "newcomer", "enabled": True},
                       headers=KATE, cookies=cookie).status_code == 403
    assert client.post(f"{BASE}/admin/access/system", json={"username": "newcomer", "enabled": True},
                       headers=ADMIN).status_code == 503

    opened = client.post(f"{BASE}/admin/access/system", json={"username": "newcomer", "enabled": True},
                         headers=ADMIN, cookies=cookie)
    assert opened.status_code == 200, opened.text
    assert opened.json() == {"username": "newcomer", "system_enabled": True}
    assert _FakePortalAuthClient.saved[-1]["permissions"] == ["procurement", "ai4science"]
    assert _FakePortalAuthClient.saved[-1]["password"] == ""
    assert _FakePortalAuthClient.saved[-1]["role"] == "sales"
    assert _FakePortalAuthClient.saved[-1]["enabled"] is True

    closed = client.post(f"{BASE}/admin/access/system", json={"username": "newcomer", "enabled": False},
                         headers=ADMIN, cookies=cookie)
    assert closed.status_code == 200
    assert _FakePortalAuthClient.saved[-1]["permissions"] == ["procurement"]

    missing = client.post(f"{BASE}/admin/access/system", json={"username": "ghost", "enabled": True},
                          headers=ADMIN, cookies=cookie)
    assert missing.status_code == 404
    with TestingSession() as db:
        actions = [row.action for row in db.scalars(select(AuditEvent)).all()]
    assert actions.count("account.system_access") == 2


def test_activity_log_records_project_entry_and_module_views():
    """进入项目、切换模块会留痕；同一页面短时间内重复打开只记一次。"""
    created = client.post(f"{BASE}/admin/projects",
                          json={"owner": "log_reader", "name": "操作日志测试项目", "disease": "未选择疾病"},
                          headers=ADMIN)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    reader = {"X-Dev-User": "log_reader", "X-Dev-Role": "researcher"}

    first = client.post(f"{BASE}/projects/{project_id}/activity", json={"view": "overview"}, headers=reader)
    assert first.status_code == 200, first.text
    assert first.json()["recorded"] is True
    assert first.json()["action"] == "project.entered"

    repeated = client.post(f"{BASE}/projects/{project_id}/activity", json={"view": "overview"}, headers=reader)
    assert repeated.status_code == 200
    assert repeated.json()["recorded"] is False

    module = client.post(f"{BASE}/projects/{project_id}/activity", json={"view": "process"}, headers=reader)
    assert module.status_code == 200, module.text
    assert module.json()["recorded"] is True
    assert module.json()["action"] == "project.module_viewed"

    payload = client.get(f"{BASE}/admin/logs", params={"project_id": project_id, "category": "project_access"},
                         headers=ADMIN).json()
    assert payload["total"] == 2
    assert {item["action"] for item in payload["items"]} == {"project.entered", "project.module_viewed"}
    assert all(item["actor_id"] == "log_reader" for item in payload["items"])
    assert any("工艺开发与优化" in item["detail_summary"] for item in payload["items"])
    assert any(item["project_name"] == "操作日志测试项目" for item in payload["items"])


def test_activity_log_is_admin_only_and_supports_filters():
    assert client.get(f"{BASE}/admin/logs", headers=KATE).status_code == 403

    payload = client.get(f"{BASE}/admin/logs", params={"category": "auth", "username": "kate_new"},
                         headers=ADMIN)
    assert payload.status_code == 200, payload.text
    body = payload.json()
    assert all(item["action"].startswith("auth.") for item in body["items"])
    assert all(item["actor_id"] == "kate_new" for item in body["items"])
    assert body["limit"] == 50 and body["offset"] == 0
    assert {"total", "logins", "login_failures", "today", "active_accounts"} <= set(body["stats"])
    assert any(item["value"] == "auth.login" for item in body["actions"])

    keyword = client.get(f"{BASE}/admin/logs", params={"keyword": "kate_new"}, headers=ADMIN)
    assert keyword.status_code == 200
    assert keyword.json()["total"] >= 1


def test_logout_is_recorded_for_the_current_account():
    response = client.post(f"{BASE}/auth/logout", headers=KATE)
    assert response.status_code == 204, response.text
    with TestingSession() as db:
        row = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.actor_id == "kate_new", AuditEvent.action == "auth.logout")
            .order_by(AuditEvent.created_at.desc())
        )
    assert row is not None
    assert row.details["provider"] == "dev"


def test_failed_login_is_recorded_without_password(monkeypatch):
    from types import SimpleNamespace
    from app import auth_routes

    class _FailingPortal:
        def __init__(self):
            pass

        def login(self, username, password, client_ip):
            raise PortalAuthError("账号或密码错误。", 401)

    monkeypatch.setattr(auth_routes, "get_settings",
                        lambda: SimpleNamespace(auth_provider="portal", app_env="test"))
    monkeypatch.setattr(auth_routes, "PortalAuthClient", _FailingPortal)
    response = client.post(f"{BASE}/auth/login",
                           json={"username": "intruder", "password": "must-not-be-logged"})
    assert response.status_code == 401

    with TestingSession() as db:
        row = db.scalar(
            select(AuditEvent)
            .where(AuditEvent.actor_id == "intruder", AuditEvent.action == "auth.login_failed")
            .order_by(AuditEvent.created_at.desc())
        )
    assert row is not None
    assert row.details["reason"] == "账号或密码错误。"
    assert "must-not-be-logged" not in str(row.details)


def test_dev_login_identity_is_used_by_subsequent_requests_and_cleared_on_logout():
    client.cookies.clear()
    try:
        logged_in = client.post(f"{BASE}/auth/login",
                                json={"username": "local_preview_user", "password": "local-preview"})
        assert logged_in.status_code == 200, logged_in.text
        me = client.get(f"{BASE}/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["username"] == "local_preview_user"
        assert me.json()["display_name"] == "local_preview_user"

        logged_out = client.post(f"{BASE}/auth/logout")
        assert logged_out.status_code == 204, logged_out.text
        assert client.get(f"{BASE}/auth/me").status_code == 401
    finally:
        client.cookies.clear()
