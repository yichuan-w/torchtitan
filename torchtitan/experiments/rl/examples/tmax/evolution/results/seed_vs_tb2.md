# Seeds (TerminalWorld 1,530) vs Terminal-Bench 2 (89)

## 题面长度（token 数，Q1/中位/Q3）
- seeds: 84 / 106 / 133
- TB2:   82 / 120 / 208

## 参考解规模（非空非注释行数，Q1/中位/Q3）
- seeds: 7 / 10 / 18
- TB2:   20 / 61 / 137

## 领域构成（TB2 由 embedding kNN(k=5) 投票映射到 TW 的领域标签）

| 领域 | seeds % | TB2 % |
|---|---|---|
| Software Development | 22.5 | 14.6 |
| System Administration | 13.1 | 3.4 |
| Scripting & Automation | 11.9 | 30.3 |
| Containers & Orchestration | 10.7 | 2.2 |
| Environment Setup | 9.7 | 6.7 |
| Security | 9.6 | 10.1 |
| Version Control | 4.1 | 3.4 |
| Networking | 3.5 | 0.0 |
| Database Operations | 2.6 | 4.5 |
| File & Storage | 2.3 | 3.4 |
| Scientific Computing | 2.0 | 6.7 |
| Debugging & Testing | 1.8 | 4.5 |
| Deployment & CI/CD | 1.7 | 0.0 |
| Data Analysis | 1.4 | 2.2 |
| Cloud & Infrastructure | 1.2 | 0.0 |
| Media Processing | 0.5 | 0.0 |
| ML Training & Experiments | 0.5 | 4.5 |
| Performance Optimization | 0.5 | 1.1 |
| Games & Entertainment | 0.3 | 2.2 |
| Tutorial & Demo | 0.1 | 0.0 |

## 覆盖度（每道 TB2 题到最近 seed 的 cosine）
- 中位 0.471，Q1 0.421，Q3 0.515
- cosine<0.30 的 TB2 题（seed 空间够不着的）: 3/89: fix-git, adaptive-rejection-sampler, filter-js-from-html, mcmc-sampling-stan, overfull-hbox, video-processing, install-windows-3.11, sam-cell-seg …按距离升序前8

---

## 修正（2026-08-10）：改用 TB2 官方 category 后的领域对比

上文「领域构成」一节的 TB2 侧数字来自 embedding kNN 映射，**已废弃**——TB2 的
task.toml 自带官方 `category`（16 类），官方口径里没有 "scripting" 类，kNN 版
的 "Scripting & Automation 30.3%" 是映射伪影。官方口径统计（89 题）：

software-engineering 26 (29.2%) / system-administration 9 / security 8 /
scientific-computing 8 / data-science 8 / file-operations 5 / debugging 5 /
model-training 4 / mathematics 4 / data-processing 4 / machine-learning 3 /
其余 6 类各 1。

手工对齐后的结论：
- 对齐良好：软工/构建 29.2% vs 22.5%；系统管理 10.1% vs 13.1%；安全 9.0% vs 9.6%。
- TB2 显著更重：科学计算+数学 13.5% vs 2.0%；数据类合计 14.6% vs 1.4%；
  ML/模型训练 7.9% vs 0.5%——TB2 有 ~36% 的题在这一侧，种子只有 ~4%。
- 种子单边多：Containers 10.7% vs 0；Networking 3.5% vs 0。
- 行动含义：滚题时向 sci/data/ML 定向补种或加难；Containers/Networking 种子
  对 TB2 不挣分（对通用能力未必无用）。
