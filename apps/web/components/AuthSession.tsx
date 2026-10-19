"use client";

import { LogOut, UserRound } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type AuthUser = { username: string; display_name: string; role: string };

const AuthContext = createContext<AuthUser | null>(null);

export function useAuthUser() { return useContext(AuthContext); }

export function AuthSession({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === "/login" || pathname.endsWith("/login");
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    if (isLoginPage || user) return;
    let cancelled = false;
    api<AuthUser>("/auth/me")
      .then((value) => {
        if (!cancelled) setUser(value);
      })
      .catch(() => {
        if (!cancelled) router.replace("/login");
      });
    return () => { cancelled = true; };
  }, [isLoginPage, router, user]);

  if (isLoginPage) return <AuthContext.Provider value={null}>{children}</AuthContext.Provider>;
  if (!user) {
    return (
      <main className="auth-loading" aria-live="polite">
        <span className="brand-mark"><UserRound size={22} /></span>
        <strong>正在确认登录状态</strong>
        <p>正在连接账号服务，请稍候。</p>
      </main>
    );
  }
  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}

export function SessionControls() {
  const user = useContext(AuthContext);
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);
  if (!user) return null;

  async function logout() {
    setLeaving(true);
    try { await api<void>("/auth/logout", { method: "POST" }); }
    finally { router.replace("/login"); router.refresh(); }
  }

  return (
    <div className="session-controls">
      <span title={`${user.username} · ${user.role}`}><UserRound size={16} /><b>{user.display_name}</b><small>{user.role}</small></span>
      <button type="button" onClick={logout} disabled={leaving}><LogOut size={15} />{leaving ? "退出中" : "退出"}</button>
    </div>
  );
}
