"use client";

import { AdminActivityLog } from "@/components/AdminActivityLog";
import { AppShell } from "@/components/AppShell";

export default function AdminLogsPage() {
  return (
    <AppShell
      title="操作日志"
      subtitle="按账号、动作、项目和时间查看登录、退出、进入项目与业务操作记录。时间统一按北京时间显示，供管理员追溯系统使用情况。"
    >
      <AdminActivityLog />
    </AppShell>
  );
}
