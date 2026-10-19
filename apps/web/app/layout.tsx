import type { Metadata } from "next";
import "ketcher-react/dist/index.css";
import "./globals.css";
import "./fungal-discovery.css";
import "./bacterial-discovery.css";
import { AuthSession } from "@/components/AuthSession";

export const metadata: Metadata = {
  title: "AI for Science",
  description: "面向药物发现、工艺开发与 PFD/CAD 图纸生成的 AI 研发工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body><AuthSession>{children}</AuthSession></body>
    </html>
  );
}
