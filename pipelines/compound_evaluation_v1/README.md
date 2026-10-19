# Compound Evaluation v1

> 生产状态：已通过 RCEES Slurm Job `9089` 并上线。100 个靶点中 50 个精确映射、41 个有严格活性；生成 344 个候选并发布前 300 个。

以 ChEMBL 37 的人源单蛋白结合实验生成靶点—化合物候选，保留 pChEMBL、实验/文献重复数和机制方向。RDKit 计算分子描述符、QED、Lipinski/Veber 与 PAINS/Brenk 警示。五个轻量随机森林/极端随机树 ADMET 基线使用 TDC 官方 scaffold-held-out test 评测，并逐化合物报告最近训练结构相似度和适用域。

实验活性、规则筛选和模型预测始终分开；体外结合不等于体内药效，ADMET 概率不等于临床安全结论。

单个 ChEMBL 请求失败会在 `target_registry` 中标记为 `request_failed` 并继续处理，其比例必须不高于 5% 才能通过整批质量门；未映射、无合格活性和请求失败是三种不同状态。

网页展示 ChEMBL 官方二维结构 SVG、标准 SMILES、实验活性与复现度、临床元数据、RDKit 结构性质与警示、五端点预测和适用域；图像失败时回退到 SMILES，实验、规则和预测三类信息不混成一个“AI 预测”。完整指标见 [`../../docs/VIRTUAL_CELL_AND_COMPOUND_EVALUATION_V1.md`](../../docs/VIRTUAL_CELL_AND_COMPOUND_EVALUATION_V1.md)。
