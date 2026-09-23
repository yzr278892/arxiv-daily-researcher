# Fork 中值得借鉴的方向

截至 2026-09-23，已查看 GitHub 可访问的 53 个 fork。以下是适合独立验证的思路，不建议直接合并旧代码。

1. **先建立评分基准，再改策略。** [q1w2e3r4-1 的 EuroSys 评测](https://github.com/q1w2e3r4-1/arxiv-daily-researcher/commit/ae56c485198b31f88187b2b697a2ef87699df6e7)提供论文集、标签和逐篇预测脚本。其[报告结果](https://github.com/q1w2e3r4-1/arxiv-daily-researcher/commit/59e3778905f9c9a0b4ca0125c5551d3307366db2)中，多模型委员会准确率 90.58%，低于单模型 GLM 的 91.30%；标签存在噪声，不能据此断言委员会更优。可借鉴可复现评测、分领域指标和边界样本分析。
2. **把语义偏好先放进影子评分。** [peipeijiang 的反馈驱动排序](https://github.com/peipeijiang/arxiv-daily-researcher/commit/1c5af74a8a1beb9942e8e3235d26c953246fa037)尝试正负反馈、时间衰减、语义向量及多样性控制。主线已有偏好学习，应先对比现有资格判定与排序契约，在不改变实际推荐的影子模式中测量收益和误伤。
3. **从已入库论文发现候选主题。** [hyperchem 的主题发现](https://github.com/hyperchem/arxiv-daily-researcher/commit/d22376a757c12c015ef61735bf29ba2305dc7792)和[多来源扩展](https://github.com/hyperchem/arxiv-daily-researcher/commit/10b33c2015ed4bcab9efd3adcbe4b71ac938eafb)可启发“新兴主题建议”，但应先验证跨源去重、漂移和成本，再决定是否进入每日流程。

优先顺序：评分基准 → 影子偏好实验 → 主题发现原型。fork 中的大量提交是个人配置或自动生成历史，不应复制进仓库。
