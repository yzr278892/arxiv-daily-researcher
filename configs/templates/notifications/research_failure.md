# 通知模板 — 研究趋势分析失败
#
# 可用变量（使用 {变量名} 引用）：
#   {status}              — 状态文本（SUCCESS / FAILED）
#   {timestamp}           — 运行时间戳
#   {keywords}            — 搜索关键词
#   {date_range}          — 时间范围
#   {total_papers}        — 搜索到的论文总数
#   {tldr_count}          — TLDR 生成成功数
#   {trend_skills_count}  — 趋势分析维度数
#   {report_list}         — 报告路径列表（已格式化）
#   {error_message}       — 错误信息
#
# 修改此文件即可自定义研究趋势分析失败通知的样式和内容。

## ArXiv Trend Research

<font color="warning">**研究趋势分析失败**</font> | {timestamp}

**搜索关键词**: {keywords}
**时间范围**: {date_range}

**错误信息**
> {error_message}

**分析统计**
> 搜索论文 **{total_papers}** 篇 | TLDR **{tldr_count}** 篇 | 分析维度 **{trend_skills_count}** 个

{report_list}

