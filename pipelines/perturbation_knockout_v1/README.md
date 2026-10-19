# Virtual Intervention and Gene Knockout v1

> 生产状态：连续校准 v2 已通过 RCEES Slurm Job `9082` 并上线。虚拟干预含 440 条结果；基因敲除发布 500 条，DepMap 对照 AUROC 0.994112、AUPRC 0.996626。

This pipeline consumes the versioned rheumatoid-arthritis Novel Target
Discovery v1 snapshot and produces two independently labelled product layers.

- Virtual intervention is a transparent target-direction and unsigned
  disease-module attenuation baseline. It uses donor-aware disease expression,
  genetics and network reach, but does not claim to predict a complete
  post-perturbation transcriptome.
- Gene knockout summarizes measured DepMap 24Q4 Chronos fitness effects. The
  hematopoietic subset is an out-of-domain proxy for RA immune cells; it is
  useful for dependency and pan-essentiality warnings, not RA efficacy.

The knockout benchmark uses DepMap common-essential and nonessential controls.
Known-drug labels are held out of the virtual-intervention score and reported
only as a retrospective sanity check.

虚拟干预页面展示建议方向、单细胞方向分量、遗传支持、网络相对位置、直接证据模态、供体数和连续置信度。基因敲除页面展示全模型/免疫代理效应、覆盖、连续广泛依赖风险和可展开原始统计量。
