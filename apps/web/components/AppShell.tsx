"use client";

import Link from "next/link";
import { Atom, LayoutDashboard, ScrollText, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { SessionControls, useAuthUser } from "@/components/AuthSession";

export function AppShell({ children, title, subtitle }: { children: React.ReactNode; title: string; subtitle?: string }) {
  const user = useAuthUser();
  return (
    <div className="app-frame">
      <header className="topbar">
        <Link href="/workspace" className="brand" aria-label="返回 AI for Science 研发工作台">
          <span className="brand-mark"><Atom size={21} /></span>
          <span><strong>AI for Science</strong><small>AI 研发工作台</small></span>
        </Link>
        <nav className="top-actions" aria-label="主导航">
          <Link href="/workspace"><LayoutDashboard size={17} /> 项目</Link>
          {user?.role === "管理员" && <Link href="/admin"><SlidersHorizontal size={17} /> 权限管理</Link>}
          {user?.role === "管理员" && <Link href="/admin/logs"><ScrollText size={17} /> 操作日志</Link>}
          <span className="trace-badge"><ShieldCheck size={15} /> 可追溯研发环境</span>
          <SessionControls />
        </nav>
      </header>
      <main className="page-shell">
        <div className="page-heading">
          <div><p className="eyebrow">AI DRUG DISCOVERY 研发工作区</p><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
        </div>
        {children}
      </main>
    </div>
  );
}
