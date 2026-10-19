# Worker service

该目录保留生产形态异步 Worker 边界。Docker Compose 中 Worker 使用 API 包的 Celery application（`app.worker`），使任务状态、Adapter 和数据库事务共享同一实现；正式形态下 PostgreSQL 保存权威任务状态，Redis 只承担队列。

## 当前生产状态

`jingmiao` 资源有限，当前没有部署 Celery、Redis 或常驻 Worker。线上轻量任务使用 FastAPI inline 执行，重型科研计算已经在 RCEES 个人隔离目录中通过 Slurm 离线运行，再把通过质量门的紧凑快照部署到 API。

## 下一步

正式远程 Worker 应采用 RCEES 出站 pull 模式：使用短期、最小权限任务令牌领取 Job，通过签名对象路径下载输入，提交 Slurm，回传状态、日志摘要、输出哈希和紧凑结果。RCEES 不开放入站端口、不持有门户管理员凭据、不直连业务数据库。
