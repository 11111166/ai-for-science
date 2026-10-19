"use client";

import { AdminAccessCenter } from "@/components/AdminAccessCenter";
import { AppShell } from "@/components/AppShell";

export default function AdminPage() {
  return (
    <AppShell
      title="账号与权限管理"
      subtitle="把账号加入研发项目并分配角色（负责人 / 成员 / 只读），也可以在这里开通或关闭某个账号的 AI for Science 访问权限。新注册的账号如果没有项目，会自动获得一个个人研发工作区，不再卡在「没有项目权限」。"
    >
      <AdminAccessCenter />
    </AppShell>
  );
}
