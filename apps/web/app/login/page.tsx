"use client";

import { Atom, KeyRound, ShieldCheck, UserPlus } from "lucide-react";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { AuthUser } from "@/components/AuthSession";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("login");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const username = String(values.get("username") || "").trim();
    const password = String(values.get("password") || "");
    const confirm = String(values.get("confirm") || "");
    if (mode === "register" && password !== confirm) {
      setError("两次输入的密码不一致。");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await api<AuthUser>(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify(mode === "login"
          ? { username, password }
          : { username, display_name: String(values.get("display_name") || "").trim(), password }),
      });
      router.replace("/workspace");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  }

  function switchMode(next: Mode) {
    setMode(next);
    setError("");
  }

  return (
    <main className="auth-page">
      <section className="auth-intro">
        <div className="auth-brand"><span className="brand-mark"><Atom size={24} /></span><div><strong>AI for Science</strong><small>AI 药物发现与研发决策平台</small></div></div>
        <div>
          <p className="eyebrow">AI DRUG DISCOVERY 研发工作区</p>
          <h1>把疾病证据推进到<br />可执行的研发决策</h1>
          <p>围绕靶点价值、生物学效应和下一步验证优先级，集中管理项目、计算结果与人工决策。</p>
        </div>
        <ul>
          <li><ShieldCheck size={18} /><span><b>统一账号与权限</b><small>支持本地账号或外部身份服务，项目数据保持独立。</small></span></li>
          <li><KeyRound size={18} /><span><b>操作可追溯</b><small>登录、注册和进入系统均写入统一审计记录。</small></span></li>
        </ul>
      </section>

      <section className="auth-panel">
        <div className="auth-tabs" role="tablist" aria-label="账号操作">
          <button type="button" className={mode === "login" ? "active" : ""} onClick={() => switchMode("login")}>账号登录</button>
          <button type="button" className={mode === "register" ? "active" : ""} onClick={() => switchMode("register")}>注册账号</button>
        </div>
        <div className="auth-panel-heading">
          <span>{mode === "login" ? <KeyRound size={22} /> : <UserPlus size={22} />}</span>
          <div><h2>{mode === "login" ? "进入研发工作区" : "创建研发账号"}</h2><p>{mode === "login" ? "使用平台账号登录。" : "注册后即可创建项目并使用全部模块。"}</p></div>
        </div>
        <form onSubmit={submit} className="auth-form">
          {mode === "register" && <label>姓名或团队称呼<input name="display_name" minLength={2} maxLength={60} autoComplete="name" required placeholder="例如：张研究员 / 转化医学组" /></label>}
          <label>账号<input name="username" minLength={mode === "register" ? 3 : 1} maxLength={40} pattern={mode === "register" ? "[A-Za-z0-9_-]+" : undefined} autoComplete="username" required placeholder="请输入账号" /></label>
          <label>密码<input name="password" type="password" minLength={mode === "register" ? 6 : 1} maxLength={200} autoComplete={mode === "login" ? "current-password" : "new-password"} required placeholder={mode === "register" ? "至少 6 位" : "请输入密码"} /></label>
          {mode === "register" && <label>确认密码<input name="confirm" type="password" minLength={6} maxLength={200} autoComplete="new-password" required placeholder="再次输入密码" /></label>}
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="primary-button auth-submit" type="submit" disabled={submitting}>{submitting ? "正在处理…" : mode === "login" ? "登录并进入平台" : "注册并进入平台"}</button>
        </form>
        <p className="auth-boundary">本系统用于科研与工程演示。请勿使用与其他网站相同的密码，也不要在公共设备保存登录信息。</p>
      </section>
    </main>
  );
}
