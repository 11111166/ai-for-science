"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  Activity, ChevronLeft, ChevronRight, CircleAlert, Clock3, LogIn, RefreshCw,
  Search, ShieldCheck, Users,
} from "lucide-react";
import { api } from "@/lib/api";

type Category = "all" | "auth" | "project_access" | "business";

type LogItem = {
  id: string;
  created_at: string;
  actor_id: string;
  actor_name: string;
  action: string;
  action_label: string;
  category: Exclude<Category, "all">;
  entity_type: string;
  entity_id: string;
  project_id: string | null;
  project_name: string | null;
  detail_summary: string;
  ip: string;
};

type Stats = {
  total: number;
  logins: number;
  login_failures: number;
  today: number;
  active_accounts: number;
};

type ActionOption = { value: string; label: string; count: number };
type AccountOption = { username: string; display_name: string };
type ProjectOption = { id: string; name: string };

type LogPage = {
  items: LogItem[];
  total: number;
  limit: number;
  offset: number;
  stats: Stats;
  actions: ActionOption[];
  accounts: AccountOption[];
  projects: ProjectOption[];
};

type FormState = {
  keyword: string;
  username: string;
  projectId: string;
  action: string;
  start: string;
  end: string;
};

const EMPTY_FORM: FormState = { keyword: "", username: "", projectId: "", action: "", start: "", end: "" };

const CATEGORY_TABS: { id: Category; label: string }[] = [
  { id: "all", label: "全部记录" },
  { id: "auth", label: "登录 / 退出" },
  { id: "project_access", label: "进入项目" },
  { id: "business", label: "业务操作" },
];

const TIME_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
});

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : TIME_FORMAT.format(date);
}

function dayStartIso(value: string) {
  return value ? new Date(`${value}T00:00:00+08:00`).toISOString() : "";
}

function dayEndIso(value: string) {
  return value ? new Date(`${value}T23:59:59.999+08:00`).toISOString() : "";
}

export function AdminActivityLog() {
  const [data, setData] = useState<LogPage | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [applied, setApplied] = useState<FormState>(EMPTY_FORM);
  const [category, setCategory] = useState<Category>("all");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (category !== "all") params.set("category", category);
    if (applied.keyword.trim()) params.set("keyword", applied.keyword.trim());
    if (applied.username) params.set("username", applied.username);
    if (applied.projectId) params.set("project_id", applied.projectId);
    if (applied.action) params.set("action", applied.action);
    if (applied.start) params.set("start", dayStartIso(applied.start));
    if (applied.end) params.set("end", dayEndIso(applied.end));
    try {
      setData(await api<LogPage>(`/admin/logs?${params.toString()}`));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作日志读取失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }, [applied, category, limit, offset]);

  useEffect(() => { void load(); }, [load]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setOffset(0);
    setApplied(form);
  }

  function reset() {
    setForm(EMPTY_FORM);
    setApplied(EMPTY_FORM);
    setCategory("all");
    setOffset(0);
  }

  function chooseCategory(next: Category) {
    setCategory(next);
    setOffset(0);
    if (next === "auth" && form.action && !form.action.startsWith("auth.")) {
      setForm((current) => ({ ...current, action: "" }));
      setApplied((current) => ({ ...current, action: "" }));
    }
  }

  const pageCount = Math.max(1, Math.ceil((data?.total ?? 0) / limit));
  const pageIndex = Math.floor(offset / limit) + 1;

  return (
    <section className="activity-log">
      <div className="activity-log-head">
        <div>
          <p className="eyebrow">AUDIT TRAIL · 北京时间</p>
          <h2>全系统操作记录</h2>
          <p>记录账号登录、退出、进入项目与业务操作。日志只追加，不在工作台内修改或删除。</p>
        </div>
        <button type="button" className="activity-refresh" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={16} className={loading ? "activity-spin" : ""} /> 刷新
        </button>
      </div>

      <div className="activity-stats">
        <div><span><Activity size={16} />累计操作</span><b>{(data?.stats.total ?? 0).toLocaleString()}</b></div>
        <div><span><LogIn size={16} />登录次数</span><b>{(data?.stats.logins ?? 0).toLocaleString()}</b></div>
        <div className={(data?.stats.login_failures ?? 0) > 0 ? "attention" : ""}><span><ShieldCheck size={16} />登录失败</span><b>{(data?.stats.login_failures ?? 0).toLocaleString()}</b></div>
        <div><span><Clock3 size={16} />今日操作</span><b>{(data?.stats.today ?? 0).toLocaleString()}</b></div>
        <div><span><Users size={16} />近 7 天活跃账号</span><b>{(data?.stats.active_accounts ?? 0).toLocaleString()}</b></div>
      </div>

      <div className="activity-panel">
        <div className="activity-tabs" role="tablist" aria-label="日志类型">
          {CATEGORY_TABS.map((tab) => (
            <button key={tab.id} type="button" role="tab" aria-selected={category === tab.id}
              className={category === tab.id ? "active" : ""} onClick={() => chooseCategory(tab.id)}>
              {tab.label}
            </button>
          ))}
        </div>

        <form className="activity-filter" onSubmit={submit}>
          <label className="activity-search">
            <span>关键词</span>
            <div><Search size={15} /><input value={form.keyword} onChange={(event) => setForm({ ...form, keyword: event.target.value })}
              placeholder="账号、动作、项目或详情" /></div>
          </label>
          <label><span>账号</span>
            <select value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })}>
              <option value="">全部账号</option>
              {data?.accounts.map((item) => <option key={item.username} value={item.username}>{item.username}{item.display_name && item.display_name !== item.username ? ` · ${item.display_name}` : ""}</option>)}
            </select>
          </label>
          <label><span>动作</span>
            <select value={form.action} onChange={(event) => setForm({ ...form, action: event.target.value })}>
              <option value="">全部动作</option>
              {data?.actions.map((item) => <option key={item.value} value={item.value}>{item.label}（{item.count}）</option>)}
            </select>
          </label>
          <label><span>项目</span>
            <select value={form.projectId} onChange={(event) => setForm({ ...form, projectId: event.target.value })}>
              <option value="">全部项目</option>
              {data?.projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label><span>开始日期</span><input type="date" value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} /></label>
          <label><span>结束日期</span><input type="date" value={form.end} onChange={(event) => setForm({ ...form, end: event.target.value })} /></label>
          <div className="activity-filter-actions">
            <button type="submit" className="primary" disabled={loading}>查询</button>
            <button type="button" onClick={reset} disabled={loading}>重置</button>
          </div>
        </form>

        {error && <div className="activity-error" role="alert"><CircleAlert size={16} />{error}</div>}

        <div className="activity-table-wrap">
          <table className="activity-table">
            <thead>
              <tr><th>时间</th><th>账号</th><th>动作</th><th>项目</th><th>详情</th><th>IP</th></tr>
            </thead>
            <tbody>
              {data?.items.map((item) => (
                <tr key={item.id}>
                  <td className="activity-time">{formatTime(item.created_at)}</td>
                  <td><strong>{item.actor_name || item.actor_id}</strong>{item.actor_name && item.actor_name !== item.actor_id && <small>{item.actor_id}</small>}</td>
                  <td><span className={`activity-badge ${item.category}`} title={item.action}>{item.action_label}</span></td>
                  <td>{item.project_id ? <Link href={`/projects/${item.project_id}?view=overview`}>{item.project_name || item.project_id.slice(0, 8)}</Link> : <span className="activity-muted">—</span>}</td>
                  <td className="activity-detail">{item.detail_summary || <span className="activity-muted">—</span>}</td>
                  <td className="activity-ip">{item.ip || <span className="activity-muted">—</span>}</td>
                </tr>
              ))}
              {!loading && data?.items.length === 0 && (
                <tr><td colSpan={6} className="activity-empty">没有符合条件的操作记录。</td></tr>
              )}
              {loading && !data && (
                <tr><td colSpan={6} className="activity-empty">正在读取操作日志…</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="activity-pagination">
          <span>共 {(data?.total ?? 0).toLocaleString()} 条 · 第 {pageIndex} / {pageCount} 页</span>
          <label>每页
            <select value={limit} onChange={(event) => { setLimit(Number(event.target.value)); setOffset(0); }}>
              <option value={30}>30</option><option value={50}>50</option><option value={100}>100</option>
            </select>
          </label>
          <div>
            <button type="button" onClick={() => setOffset(Math.max(0, offset - limit))} disabled={offset === 0 || loading}><ChevronLeft size={15} />上一页</button>
            <button type="button" onClick={() => setOffset(offset + limit)} disabled={offset + limit >= (data?.total ?? 0) || loading}>下一页<ChevronRight size={15} /></button>
          </div>
        </div>
      </div>
    </section>
  );
}
