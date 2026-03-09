# 通知模板 — 研究趋势分析成功
#
# 可用变量（使用 {变量名} 引用）：
#   {status}               — 状态文本（SUCCESS / FAILED）
#   {timestamp}            — 运行时间戳
#   {keywords}             — 搜索关键词
#   {date_range}           — 时间范围
#   {total_papers}         — 搜索到的论文总数
#   {tldr_count}           — TLDR 生成成功数
#   {trend_skills_count}   — 趋势分析维度数
#   {report_list}          — 报告路径列表（已格式化）
#   {error_message}        — 错误信息（成功时为空）
#   {token_usage_section}  — Token 消耗统计（已格式化，关闭追踪时为空）
#
# 修改此文件即可自定义研究趋势分析成功通知的样式和内容。

## ArXiv Trend Research

<font color="info">**研究趋势分析完成**</font> | {timestamp}

**搜索关键词**: {keywords}
**时间范围**: {date_range}

**分析统计**
> 搜索论文 <font color="info">**{total_papers}**</font> 篇 | TLDR <font color="info">**{tldr_count}**</font> 篇 | 分析维度 <font color="info">**{trend_skills_count}**</font> 个

{token_usage_section}

{report_list}

