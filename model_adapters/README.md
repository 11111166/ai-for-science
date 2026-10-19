# Model adapters

本目录定义与 Web/API 解耦的版本化科研模型契约。当前同时存在两类实现：

1. 模型无关的 Virtual Cell 请求/响应与 `NotConfiguredVirtualCellAdapter`；
2. 生产 API 中对 RCEES 版本化快照的只读 Adapter，包括靶点、新靶点、虚拟干预/敲除、Virtual Cell 和化合物评价。

当前没有安装或运行 CPA、GEARS。线上 Virtual Cell v1 使用供体级病例—健康状态轴和有上限反事实投影，不声称生成完整扰动后表达矩阵。

## 输入输出契约

- 单细胞输入使用带版本的 AnnData/H5AD，保留 raw counts、基因 ID、细胞注释、供体和批次；
- 基因使用稳定 Ensembl ID，同时保留 HGNC symbol；
- 扰动记录 `knockout | knockdown | activation | overexpression`、基因、guide、细胞情境和时间；
- 输出声明 completed、not_configured、out_of_domain 或 failed；
- 输出保存 input hash、模型/Adapter 版本、参数、置信度、OOD 和 provenance；
- 大矩阵留在对象存储或 RCEES，API 数据库只保存引用和紧凑结果。

## 当前生产数据路径

| 模块 | 数据 | Adapter 方法 |
|---|---|---|
| 已有证据靶点 | Open Targets/HGNC/HPA compact snapshot | 三轴结果只读适配；未覆盖疾病回退实时 Open Targets |
| 新靶点 | GWAS/eQTL/CELLxGENE/PPI compact snapshot | 疾病匹配、质量门和证据字段适配 |
| 虚拟干预/敲除 | RA 多模态 + DepMap 24Q4 | 选择靶点覆盖检查与结果适配 |
| Virtual Cell | RA donor-level snapshot | 疾病、靶点和质量门检查；返回投影与区间 |
| 化合物/ADMET | ChEMBL 37/RDKit/TDC snapshot | 严格靶点匹配并分离实验、规则和预测字段 |

任何生成式模型上线前必须按 perturbation、gene、donor/cell context 留出测试，报告表达相关性、差异基因方向、通路一致性、校准、不确定性和 OOD 拒答。预测只能用于假设生成。
