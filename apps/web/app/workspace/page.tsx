"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuthUser } from "@/components/AuthSession";
import { lastWorkspaceProjectKey, openDefaultWorkspace } from "@/lib/workspaceProjects";

export default function WorkspaceEntry() {
  const router = useRouter();
  const user = useAuthUser();
  const started = useRef(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (started.current || !user) return;
    started.current = true;
    openDefaultWorkspace(window.localStorage.getItem(lastWorkspaceProjectKey(user.username)))
      .then((project) => router.replace(`/projects/${project.id}?view=fungal_targets`))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "工作台加载失败"));
  }, [router, user]);

  return <AppShell title="正在进入研发项目" subtitle="主页直接打开最近使用的项目；疾病可在各分析模块中搜索和切换。">
    {error ? <div className="notice error" role="alert">{error}<button type="button" className="secondary-button" onClick={() => { started.current = false; setError(""); window.location.reload(); }}>重试</button></div>
      : <div className="loading-panel" role="status">正在进入研发工作台…</div>}
  </AppShell>;
}
