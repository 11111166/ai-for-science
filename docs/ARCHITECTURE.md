# 系统架构

## 总览

```
Browser
  |  Next.js 16 (apps/web)
  |  - 工作台、项目、PFD 画布、权限与审计页面
  |  - NEXT_PUBLIC_API_URL 指向 FastAPI
  v
FastAPI (services/api/app)
  |  - 认证与项目权限
  |  - 工艺开发 / PFD / CAD 图纸
  |  - 化合物、靶点、组学、蛋白结构
  |  - 任务、缓存、审计
  v
SQLite / PostgreSQL      Redis       本地文件 / S3 / MinIO
```

## 前端

- `apps/web/app`：Next.js App Router 页面，包含登录、工作台、项目、管理后台和操作日志。
- `apps/web/components`：业务组件。PFD 相关核心组件为 `ProcessPfdWorkspace.tsx`，负责节点拖拽、参数编辑、图纸切换、预览与导出。
- `apps/web/lib`：API 客户端、展示层格式化和共享工具。
- `apps/web/public/process`：公开示例图（SVG）。

前端不直接持有数据库或模型密钥。所有写操作通过 FastAPI，并在服务端校验项目成员身份。

## 后端

- `app/main.py`：应用装配、生命周期、任务恢复和路由注册。
- `app/auth.py` / `app/auth_routes.py`：会话、角色和项目访问控制。
- `app/process_pfd.py`：PFD 草稿、修订、版本和导出接口。
- `app/pfd_cad_sheet.py`：双图纸（图例页 + 工段页）的 SVG/DXF 生成。
- `app/pfd_symbols.py`：设备、阀门、仪表符号库。
- `app/process_intake.py`：多模态工艺资料接入，调用 OpenAI 兼容接口抽取结构化流程。
- `app/process_development.py`、`process_scaleup.py`、`process_portfolio.py`：实验条件、放大评估与项目组合。
- `app/admin_routes.py`：账号与审计日志（仅管理员）。
- 其余模块覆盖靶点、化合物、ADMET、蛋白结构和组学分析。

## 数据模型

核心对象：

- `Project`：项目与成员。
- `ProcessAssessment`：放大评估，是 PFD 的上游输入。
- `ProcessDrawing`：图纸草稿，保存图结构、修订号和来源评估。
- `ProcessDrawingVersion`：不可变版本，用于回退与对比。
- 任务、事件、审计记录：统一记录长任务和用户操作。

图纸 JSON 的核心结构：

```json
{
  "title": "工段名称",
  "graph": {
    "nodes": [{"id": "R-101", "kind": "reactor", "label": "反应釜", "x": 320, "y": 240, "w": 140, "h": 180, "params": {"温度": "37 °C"}}],
    "edges": [{"id": "e1", "from": "R-101", "to": "E-101", "kind": "main", "label": "500 kg/h"}]
  }
}
```

## 认证模式

| 模式 | 用途 | 说明 |
|---|---|---|
| `dev` | 本地开发 | 本地账号，禁止用于生产 |
| `proxy` | 受信反向代理 | 由网关注入用户头，API 校验共享密钥 |
| `portal` | 外部统一账号 | 仅允许 loopback 门户地址 |

生产环境启动时会强制校验：`dev` 模式直接拒绝启动，`proxy`/`portal` 的共享密钥至少 32 位。

## 任务与缓存

- 默认 `JOB_EXECUTION_MODE=inline`，长任务在 API 进程内执行；生产可切换 Celery。
- 响应缓存写入数据库，按契约版本和用户范围隔离。
- 上传文件默认落本地目录，可切换 S3/MinIO。

## 部署边界

仓库只提供开发与自托管所需的最小配置。生产部署还需要：

- 反向代理与 TLS；
- PostgreSQL、Redis、对象存储；
- 密钥管理与日志脱敏；
- 备份、监控和回滚流程。
