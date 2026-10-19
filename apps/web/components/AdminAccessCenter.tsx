"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { KeyRound, RefreshCw, ShieldCheck, Trash2, UserPlus } from "lucide-react";
import { api } from "@/lib/api";

type RoleOption = { value: string; label: string; description: string };
type AccountProject = { id: string; name: string; disease: string; status: string; role: string; role_label: string };
type Account = {
  username: string; display_name: string; role_label: string; project_count: number; projects: AccountProject[];
  platform_role_label: string; system_enabled: boolean | null; account_enabled: boolean;
};
type Member = { user_id: string; role: string; role_label: string };
type AdminProject = {
  id: string; name: string; disease: string; status: string; project_type: string;
  owner_id: string; member_count: number; updated_at: string; members: Member[];
};
type Overview = {
  accounts: Account[]; projects: AdminProject[]; roles: RoleOption[]; workspace_name: string;
  system_roster_available: boolean; system_total: number; system_pending: number; access_control_enabled: boolean;
};

export function AdminAccessCenter() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [account, setAccount] = useState("");
  const [projectId, setProjectId] = useState("");
  const [role, setRole] = useState("member");
  const [newOwner, setNewOwner] = useState("");
  const [newName, setNewName] = useState("");
  const [newDisease, setNewDisease] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const value = await api<Overview>("/admin/access");
      setData(value);
      setError("");
      setProjectId((current) => (current && value.projects.some((item) => item.id === current) ? current : value.projects[0]?.id ?? ""));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "权限数据读取失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function run(label: string, action: () => Promise<unknown>, message: string) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
      setNotice(message);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请稍后重试。");
    } finally {
      setBusy("");
    }
  }

  function grant() {
    const username = account.trim();
    if (!username) { setError("请填写要授权的账号（注册时的账号名）。"); return; }
    if (!projectId) { setError("请选择要授权的项目。"); return; }
    void run("grant",
      () => api("/admin/access/grant", { method: "POST", body: JSON.stringify({ username, project_id: projectId, role }) }),
      `已把「${username}」加入项目，角色：${data?.roles.find((item) => item.value === role)?.label ?? role}。`);
  }

  function revoke(target: string, targetProject: string, name: string) {
    if (!window.confirm(`确定收回「${name}」在该项目的权限？`)) return;
    void run(`revoke-${target}`,
      () => api("/admin/access/revoke", { method: "POST", body: JSON.stringify({ username: target, project_id: targetProject }) }),
      `已收回「${name}」的项目权限。`);
  }

  function changeRole(target: string, targetProject: string, nextRole: string) {
    void run(`role-${target}`,
      () => api("/admin/access/grant", { method: "POST", body: JSON.stringify({ username: target, project_id: targetProject, role: nextRole }) }),
      `已把「${target}」的角色调整为${data?.roles.find((item) => item.value === nextRole)?.label ?? nextRole}。`);
  }

  function createProject() {
    const owner = newOwner.trim();
    if (!owner) { setError("请填写项目所属账号。"); return; }
    if (newName.trim().length < 2) { setError("项目名称至少 2 个字。"); return; }
    void run("create",
      () => api("/admin/projects", {
        method: "POST",
        body: JSON.stringify({ owner, name: newName.trim(), disease: newDisease.trim() || "未选择疾病", objective: "" }),
      }),
      `已为「${owner}」新建项目「${newName.trim()}」并设为负责人。`);
    setNewName("");
  }

  function setSystemAccess(username: string, enabled: boolean) {
    void run(`system-${username}`,
      () => api("/admin/access/system", { method: "POST", body: JSON.stringify({ username, enabled }) }),
      enabled ? `已开通「${username}」的 AI for Science 访问权限。` : `已关闭「${username}」的 AI for Science 访问权限。`);
  }

  const accounts = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase("zh-CN");
    const items = data?.accounts ?? [];
    if (!keyword) return items;
    return items.filter((item) => [item.username, item.display_name].some((value) => value.toLocaleLowerCase("zh-CN").includes(keyword)));
  }, [data, query]);

  if (loading && !data) {
    return <div className="loading-panel" role="status">正在读取账号与项目权限…</div>;
  }

  if (error && !data) {
    return <div className="notice error" role="alert">{error}<button type="button" className="secondary-button" onClick={() => void load()}>重试</button></div>;
  }

  return (
    <div className="admin-center">
      <section className="catalog-summary" aria-label="权限概览">
        <div><strong>{data?.accounts.length ?? 0}</strong><span>账号</span><small>登录过或已分配项目的账号</small></div>
        <div><strong>{data?.projects.length ?? 0}</strong><span>研发项目</span><small>可分配权限的项目总数</small></div>
        <div><strong>{data?.accounts.reduce((total, item) => total + item.project_count, 0) ?? 0}</strong><span>授权关系</span><small>账号 × 项目的成员关系</small></div>
        <div><strong>{data?.system_total ?? data?.accounts.length ?? 0}</strong><span>平台账号</span><small>{data?.system_roster_available ? "来自统一账号服务" : "统一账号服务暂时读不到，按本地记录显示"}</small></div>
        <div><strong>{data?.system_pending ?? 0}</strong><span>待开通系统</span><small>还没有 AI for Science 访问权限的账号</small></div>
        <button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={15} /> 刷新
        </button>
      </section>

      {notice && <div className="notice success">{notice}</div>}
      {error && <div className="notice error">{error}</div>}
      {data && data.access_control_enabled === false && (
        <div className="notice success">
          权限已全面放开：所有账号都能访问全部 {data.projects.length} 个项目，收回授权也不会再挡住访问。
        </div>
      )}

      <section className="section-block">
        <div className="section-title">
          <div><span>分配权限</span><h2>把账号加入研发项目</h2></div>
        </div>
        <p className="admin-hint">
          <ShieldCheck size={15} /> 权限已全面放开：所有账号都能查看并使用全部项目，新账号进入工作台时也会自动获得一个属于自己的「{data?.workspace_name ?? "AI for Science 研发工作区"}」。
          下面的授权用来给项目做分工标记（负责人可改资料并分配成员，成员可上传数据 / 运行分析，只读可查看），不再限制访问。
        </p>
        <div className="admin-grant">
          <label>账号
            <input list="admin-account-options" value={account} onChange={(event) => setAccount(event.target.value)} placeholder="注册时使用的账号名" />
            <datalist id="admin-account-options">
              {(data?.accounts ?? []).map((item) => <option key={item.username} value={item.username}>{item.display_name}</option>)}
            </datalist>
          </label>
          <label>项目
            <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
              {(data?.projects ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>角色
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {(data?.roles ?? []).map((item) => <option key={item.value} value={item.value}>{item.label}（{item.description}）</option>)}
            </select>
          </label>
          <button type="button" className="primary-button" onClick={grant} disabled={busy === "grant" || !data?.projects.length}>
            <KeyRound size={16} /> {busy === "grant" ? "授权中…" : "授权"}
          </button>
        </div>

        <div className="admin-grant admin-grant-create">
          <label>账号
            <input value={newOwner} onChange={(event) => setNewOwner(event.target.value)} placeholder="为哪个账号建项目" />
          </label>
          <label>项目名称
            <input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="例如：肺癌新靶点探索" />
          </label>
          <label>疾病（可留空）
            <input value={newDisease} onChange={(event) => setNewDisease(event.target.value)} placeholder="例如：非小细胞肺癌" />
          </label>
          <button type="button" className="secondary-button" onClick={createProject} disabled={busy === "create"}>
            <UserPlus size={16} /> {busy === "create" ? "创建中…" : "新建并授权"}
          </button>
        </div>
      </section>

      <section className="section-block">
        <div className="section-title project-list-heading">
          <div><span>研发项目</span><h2>项目成员与权限</h2></div>
        </div>
        <div className="admin-projects">
          {(data?.projects ?? []).map((project) => (
            <article className="admin-project" key={project.id}>
              <header>
                <div>
                  <h3>{project.name}</h3>
                  <p>{project.disease} · 负责人 {project.owner_id} · {project.member_count} 个成员</p>
                </div>
                <button type="button" className="admin-pick" onClick={() => setProjectId(project.id)} disabled={projectId === project.id}>
                  {projectId === project.id ? "已选中" : "选中"}
                </button>
              </header>
              <ul className="admin-members">
                {project.members.map((member) => (
                  <li key={member.user_id}>
                    <b>{member.user_id}</b>
                    {member.user_id === project.owner_id ? (
                      <span className="admin-owner">负责人</span>
                    ) : (
                      <>
                        <select value={member.role} onChange={(event) => changeRole(member.user_id, project.id, event.target.value)} disabled={busy === `role-${member.user_id}`}>
                          {(data?.roles ?? []).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                        </select>
                        <button type="button" onClick={() => revoke(member.user_id, project.id, member.user_id)} disabled={busy === `revoke-${member.user_id}`} aria-label={`收回 ${member.user_id} 的权限`}>
                          <Trash2 size={14} />
                        </button>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </article>
          ))}
          {!data?.projects.length && <div className="empty-state"><h3>还没有研发项目</h3><p>先用上面的「新建并授权」为某个账号建第一个项目。</p></div>}
        </div>
      </section>

      <section className="section-block">
        <div className="section-title project-list-heading">
          <div><span>账号</span><h2>全部账号与权限范围</h2>
            <p className="admin-hint"><ShieldCheck size={15} /> 名册来自平台统一账号服务（含从未登录过的账号）；「未开通 AI for Science」的账号登录时会被系统挡在门外，可以在这里一键开通。</p>
          </div>
          <label className="project-search"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索账号或姓名" aria-label="搜索账号或姓名" /></label>
        </div>
        <div className="admin-accounts">
          {accounts.map((item) => (
            <article className="admin-account" key={item.username}>
              <div>
                <b>{item.display_name}</b>
                <small>{item.username} · {item.platform_role_label} · 项目角色 {item.role_label} · {item.project_count} 个项目</small>
              </div>
              <div className="admin-account-tags">
                {item.projects.map((project) => <span key={project.id}>{project.name}（{project.role_label}）</span>)}
                {!item.projects.length && <span className="admin-account-none">未分配任何项目</span>}
              </div>
              <div className="admin-account-system">
                {item.system_enabled === null ? (
                  <span className="admin-system-badge unknown">系统权限按本地记录</span>
                ) : (
                  <>
                    <span className={`admin-system-badge ${item.system_enabled ? "on" : "off"}`}>
                      {item.system_enabled ? "已开通 AI for Science" : "未开通 AI for Science"}
                    </span>
                    {!item.account_enabled && <span className="admin-system-badge off">账号已停用</span>}
                    <button type="button" className="admin-system-toggle" disabled={busy === `system-${item.username}`}
                            onClick={() => setSystemAccess(item.username, !item.system_enabled)}>
                      {busy === `system-${item.username}` ? "处理中…" : item.system_enabled ? "关闭访问" : "开通访问"}
                    </button>
                  </>
                )}
              </div>
            </article>
          ))}
          {!accounts.length && <div className="empty-state"><h3>没有匹配账号</h3><p>账号在第一次登录 / 注册时登记；也可以直接在上面的输入框里输入账号名授权。</p></div>}
        </div>
      </section>
    </div>
  );
}