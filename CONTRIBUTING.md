# Contributing

感谢参与 AI for Science。

## 开发流程

1. Fork 仓库并从 `main` 创建分支。
2. 保持改动聚焦，一个分支只解决一个问题。
3. 提交前运行相关测试：
   - 后端：`cd services/api && pytest -q tests`
   - 前端：`npm run test --workspace=@drug-discovery/web`
   - 构建：`npm run build:web`
4. 在 Pull Request 中说明动机、改动范围、验证方式和已知风险。

## 代码约定

- 前端使用 TypeScript 与现有组件风格，不引入新的状态管理库。
- 后端保持 FastAPI + SQLAlchemy 结构，路由、模型和业务逻辑分层。
- 图纸相关改动必须同时检查 SVG 预览与 DXF 导出，不要只改其中一侧。
- 不提交任何真实用户数据、内部地址、密钥、数据库或上传文件。
- 新增依赖需说明用途、许可证和体积影响。

## 制图改动

修改设备符号、比例、线型或图层前，请先阅读 `docs/PFD_CAD.md`，并在 PR 中附上修改前后的图面对比。
