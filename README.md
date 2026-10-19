# AI for Science

**从工艺信息到可交付的 PFD 与 CAD 图纸。**

AI for Science 是一个开源的 AI 研发工作台，面向药物发现与工艺开发两条主线。当前版本重点解决工艺设计中最耗时的环节：把文字、图片、表格里的工艺信息，转成可编辑的流程图，再生成符合工程制图习惯的 **图例图** 与 **工段图**，并导出 SVG / DXF。

- 在线体验：<https://business-cloud.tail54f227.ts.net:8443/apps/ai4science/>
- 制图规范：[`docs/PFD_CAD.md`](docs/PFD_CAD.md)
- 系统架构：[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- 快速演示：[`docs/DEMO.md`](docs/DEMO.md)

> 在线站点由维护者提供，注册后即可体验。请勿上传患者信息、员工信息、客户资料或未公开的商业数据。

## 核心能力

### 工艺开发与制图

- **多模态工艺接入**：输入工艺描述、粘贴设备清单、上传流程图照片或表格，由大模型抽取单元操作、物流、阀门、仪表和工艺参数，生成可编辑的 PFD 草稿。
- **Aspen 式流程结构**：反应器、精馏塔、换热器、储罐、泵、压缩机、阀门、仪表等设备节点，支持位号、参数、连接关系和修订版本。
- **双图纸输出**：一张图例（设备/阀门/仪表符号、图框、标题栏），一张工段图（核心流程），符合设计院常见出图习惯。
- **物料守恒检查**：主物料入口与出口流量不一致时，要求标注差额去向（排污、蒸汽、副产、损耗等），并在图上显式呈现。
- **工程化图面规则**：主物料线左右顶格进出车间、辅助物料线为实线且线宽为主物料线的 1/3、塔与储罐按主设备比例绘制、阀门与气动执行机构闭合、冷凝器按接管外径连接。
- **CAD 图层规范**：按设备、主物料、辅助物料、仪表、公用工程、文字、图框分层，预留颜色、线型、线宽设置。
- **导出**：SVG 预览、DXF（AC1009）下载，便于进入 AutoCAD 继续深化。

### 药物发现模块

平台同时保留了疾病数据与特征、靶点发现与排序、虚拟干预 / Virtual Cell、化合物匹配与评价、ADMET / QSAR、蛋白结构等模块。它们共享项目、权限、任务和审计基础设施，可作为工艺模块的上游证据来源。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Next.js 16、React 19、TypeScript、Tailwind CSS 4 |
| 后端 | FastAPI、SQLAlchemy 2、Alembic、Pydantic Settings |
| 数据 | SQLite（本地演示）/ PostgreSQL（生产形态）、本地文件或 S3/MinIO |
| 任务 | 进程内执行（默认）/ Celery + Redis |
| 模型 | 任意 OpenAI 兼容接口；默认适配 SiliconFlow 的 DeepSeek 文本与 OCR 模型 |
| 图纸 | 服务端 SVG/DXF 生成，前端 SVG 画布编辑 |

## 快速开始

### 环境要求

- Node.js 22+
- Python 3.11+（推荐 3.12）
- npm 10+

### 1. 安装依赖

```bash
npm install
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r services/api/requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

不配置模型密钥也可以使用手工绘图、示例数据和导出功能；配置 `SILICONFLOW_API_KEY` 后启用文字/图片自动识别。

### 3. 启动后端

```bash
cd services/api
uvicorn app.main:app --reload --port 8000
```

### 4. 启动前端

另开一个终端，在仓库根目录：

```bash
npm run dev:web
```

访问 <http://localhost:3000/login>，注册一个本地账号即可。开发模式默认使用 `AUTH_PROVIDER=dev`，本地账号保存在 SQLite 中。

### 5. 载入演示项目

```bash
cd services/api
python seed_local_demo.py
```

会创建一个“L-苯丙氨酸 酶法转化—结晶精制 工艺开发演示”项目，可直接进入工艺开发 → PFD 工作区体验。

## Docker

```bash
docker compose up --build
```

- 前端：<http://localhost:3000>
- API 文档：<http://localhost:8000/docs>

首次启动后载入演示数据：

```bash
docker compose exec api python seed_local_demo.py
```

## 目录结构

```
apps/web                 Next.js 前端（工作台、PFD 画布、登录与权限页面）
services/api             FastAPI 后端（项目、工艺、PFD/CAD、组学、化合物、审计）
services/worker          Celery Worker 边界说明
packages/contracts       Web/API 共享 TypeScript 契约
model_adapters           模型无关的虚拟细胞适配层
pipelines                可离线运行的版本化科研管线
docs                     架构、制图规范与演示说明
```

## 主要配置

| 变量 | 说明 | 默认 |
|---|---|---|
| `APP_ENV` | 运行环境 | `development` |
| `AUTH_PROVIDER` | `dev` / `proxy` / `portal` | `dev` |
| `DATABASE_URL` | SQLAlchemy 连接串 | `sqlite:///./drug_discovery.db` |
| `UPLOAD_DIR` | 上传文件目录 | `./data/uploads` |
| `CORS_ORIGINS` | 允许的前端来源 | `http://localhost:3000` |
| `SILICONFLOW_API_KEY` | 多模态工艺识别密钥 | 空 |
| `INTAKE_TEXT_MODEL` | 文本抽取模型 | `deepseek-ai/DeepSeek-V4-Flash` |
| `INTAKE_VL_MODEL` | 图片理解 / OCR 模型 | `deepseek-ai/DeepSeek-OCR` |
| `NEXT_PUBLIC_API_URL` | 前端访问的 API 前缀 | 开发模式指向 `localhost:8000` |

生产环境禁止 `AUTH_PROVIDER=dev`；使用 `proxy` 或 `portal` 时必须配置至少 32 位的 `PROXY_AUTH_SECRET`。

## 安全与数据边界

- 仓库不包含任何生产数据库、上传文件、账号日志、部署脚本或真实用户数据。
- 在线演示站与本地开发环境相互独立；本地修改不会影响线上。
- 模型调用会把用户提交的工艺文本/图片发送到所配置的模型服务。接入真实项目前，请确认数据出境、保密和供应商条款。
- 生成的 PFD 是**概念设计草稿**，不能替代工艺包、HAZOP、设备选型和施工图审查。

## 已知边界

- DXF 为 AC1009 兼容格式，复杂填充和字体在旧版 AutoCAD 中可能需要重新映射。
- 图片识别依赖 OCR 质量；手绘草图、低分辨率照片和遮挡会降低抽取准确率。
- 组学分析与部分化合物模块依赖外部快照数据，仓库只保留可公开的示例数据。
- 仓库暂未附带生产级反向代理、对象存储和密钥管理配置。

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始之前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。涉及安全问题时请不要公开提交，按 [`SECURITY.md`](SECURITY.md) 的方式联系维护者。

## 许可证

[MIT](LICENSE)
