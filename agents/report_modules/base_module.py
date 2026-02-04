"""
模块渲染器基类和格式化工具

提供通用的格式化方法，支持多种Markdown格式输出。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple


class FormatHelper:
    """
    格式化工具类，提供各种Markdown格式转换方法。

    支持两种admonition风格：
    - mkdocs: !!! note "标题"
    - github: > [!NOTE]
    """

    def __init__(self, admonition_style: str = "mkdocs"):
        """
        初始化格式化工具。

        参数:
            admonition_style: admonition风格，可选 "mkdocs" 或 "github"
        """
        self.admonition_style = admonition_style

    def format_as_quote(self, content: str) -> List[str]:
        """
        格式化为引用块。

        参数:
            content: 要格式化的内容

        返回:
            List[str]: 格式化后的行列表
        """
        if not content:
            return []
        lines = []
        for line in content.split('\n'):
            lines.append(f"> {line}")
        lines.append("")
        return lines

    def format_as_admonition(
        self,
        content: str,
        title: str = "",
        admonition_type: str = "note"
    ) -> List[str]:
        """
        格式化为admonition提示框。

        参数:
            content: 内容
            title: 标题（将作为admonition的类型声明）
            admonition_type: 类型，如 note, info, tip, warning, danger 等（备用）

        返回:
            List[str]: 格式化后的行列表
        """
        if not content:
            return []

        lines = []

        if self.admonition_style == "github":
            # GitHub风格: > [!TIP] 或 > [!标题]
            # 如果有标题，使用标题作为类型
            if title:
                lines.append(f"> [!{title}]")
            else:
                type_map = {
                    "note": "NOTE",
                    "info": "NOTE",
                    "tip": "TIP",
                    "success": "TIP",
                    "warning": "WARNING",
                    "danger": "CAUTION",
                    "abstract": "NOTE",
                }
                gh_type = type_map.get(admonition_type, "NOTE")
                lines.append(f"> [!{gh_type}]")
            lines.append(">")
            for line in content.split('\n'):
                lines.append(f"> {line}")
            lines.append("")
        else:
            # MkDocs风格: !!! tip 标题（不使用引号）
            if title:
                # 使用标题作为admonition的声明
                lines.append(f'!!! {admonition_type} {title}')
            else:
                lines.append(f'!!! {admonition_type}')
            lines.append("")
            for line in content.split('\n'):
                lines.append(f"    {line}")
            lines.append("")

        return lines

    def format_as_table(
        self,
        rows: List[Tuple],
        headers: List[str]
    ) -> List[str]:
        """
        格式化为Markdown表格。

        参数:
            rows: 数据行列表，每行是一个元组
            headers: 表头列表

        返回:
            List[str]: 格式化后的行列表
        """
        if not rows or not headers:
            return []

        lines = []
        # 表头
        lines.append("| " + " | ".join(headers) + " |")
        # 分隔线
        lines.append("|" + "|".join(["------" for _ in headers]) + "|")
        # 数据行
        for row in rows:
            cells = [str(cell) for cell in row]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

        return lines

    def format_as_list(
        self,
        items: List[str],
        list_style: str = "bullet"
    ) -> List[str]:
        """
        格式化为列表。

        参数:
            items: 列表项
            list_style: 列表样式，"bullet" 或 "numbered"

        返回:
            List[str]: 格式化后的行列表
        """
        if not items:
            return []

        lines = []
        for idx, item in enumerate(items, 1):
            if list_style == "numbered":
                lines.append(f"{idx}. {item}")
            else:
                lines.append(f"- {item}")
        lines.append("")

        return lines

    def format_as_inline(self, items: List[str], separator: str = ", ") -> List[str]:
        """
        格式化为行内显示。

        参数:
            items: 项目列表
            separator: 分隔符

        返回:
            List[str]: 格式化后的行列表
        """
        if not items:
            return []
        return [separator.join(items), ""]

    def format_as_heading(
        self,
        content: str,
        level: int = 3
    ) -> List[str]:
        """
        格式化为标题。

        参数:
            content: 标题内容
            level: 标题级别 (1-6)

        返回:
            List[str]: 格式化后的行列表
        """
        if not content:
            return []
        level = max(1, min(6, level))
        return [f"{'#' * level} {content}", ""]

    def format_as_bold(self, content: str) -> List[str]:
        """
        格式化为粗体。

        参数:
            content: 内容

        返回:
            List[str]: 格式化后的行列表
        """
        if not content:
            return []
        return [f"**{content}**", ""]

    def format_as_plain(self, content: str) -> List[str]:
        """
        格式化为纯文本。

        参数:
            content: 内容

        返回:
            List[str]: 格式化后的行列表
        """
        if not content:
            return []
        return [content, ""]

    def wrap_collapsible(
        self,
        lines: List[str],
        title: str,
        default_open: bool = False
    ) -> List[str]:
        """
        包装为可折叠块。

        参数:
            lines: 要包装的内容行
            title: 折叠块标题
            default_open: 是否默认展开

        返回:
            List[str]: 包装后的行列表
        """
        if not lines:
            return []

        result = []
        if default_open:
            result.append("<details open>")
        else:
            result.append("<details>")
        result.append(f"<summary>{title}</summary>")
        result.append("")
        result.extend(lines)
        # 确保内容末尾有空行
        if result[-1] != "":
            result.append("")
        result.append("</details>")
        result.append("<br>")  # 使用<br>作为显式空行，避免和下一个模块挤在一起
        result.append("")  # <br>后加空行，确保下一个模块正常显示

        return result

    def format_label(self, label: str, content: str) -> List[str]:
        """
        格式化为带标签的内容。

        参数:
            label: 标签
            content: 内容

        返回:
            List[str]: 格式化后的行列表
        """
        if not content:
            return []
        return [f"**{label}**: {content}", ""]


class BaseModuleRenderer(ABC):
    """
    模块渲染器基类。

    所有具体的模块渲染器都应继承此类并实现 render 方法。
    """

    def __init__(self, format_helper: FormatHelper):
        """
        初始化渲染器。

        参数:
            format_helper: 格式化工具实例
        """
        self.format_helper = format_helper

    @abstractmethod
    def render(self, data: Dict[str, Any], config: Dict[str, Any]) -> List[str]:
        """
        渲染模块内容。

        参数:
            data: 论文数据
            config: 模块配置

        返回:
            List[str]: 渲染后的Markdown行列表
        """
        pass

    def is_enabled(self, config: Dict[str, Any]) -> bool:
        """
        检查模块是否启用。

        参数:
            config: 模块配置

        返回:
            bool: 是否启用
        """
        return config.get("enabled", True)

    def get_format(self, config: Dict[str, Any]) -> str:
        """
        获取模块格式。

        参数:
            config: 模块配置

        返回:
            str: 格式名称
        """
        return config.get("format", "plain")

    def should_collapsible(self, config: Dict[str, Any]) -> bool:
        """
        检查是否需要折叠。

        参数:
            config: 模块配置

        返回:
            bool: 是否需要折叠
        """
        return config.get("collapsible", False)

    def get_collapsible_default_open(self, config: Dict[str, Any]) -> bool:
        """
        获取折叠块是否默认展开。

        参数:
            config: 模块配置

        返回:
            bool: 是否默认展开
        """
        return config.get("collapsible_default_open", False)

    def get_label(self, config: Dict[str, Any]) -> str:
        """
        获取模块标签。

        参数:
            config: 模块配置

        返回:
            str: 标签文本
        """
        return config.get("label", config.get("name", ""))

    def apply_format(
        self,
        content: Any,
        config: Dict[str, Any]
    ) -> List[str]:
        """
        根据配置应用格式。

        参数:
            content: 内容（字符串或列表）
            config: 模块配置

        返回:
            List[str]: 格式化后的行列表
        """
        fmt = self.get_format(config)
        label = self.get_label(config)
        is_collapsible = self.should_collapsible(config)

        if fmt == "quote":
            if isinstance(content, str):
                lines = self.format_helper.format_as_quote(content)
            else:
                lines = self.format_helper.format_as_quote("\n".join(content))
        elif fmt == "admonition":
            admonition_type = config.get("admonition_type", "note")
            # 如果是collapsible，不传label给admonition，避免重复
            admonition_label = "" if is_collapsible else label
            if isinstance(content, str):
                lines = self.format_helper.format_as_admonition(content, admonition_label, admonition_type)
            else:
                lines = self.format_helper.format_as_admonition("\n".join(content), admonition_label, admonition_type)
        elif fmt == "list":
            if isinstance(content, list):
                list_style = config.get("list_style", "bullet")
                lines = self.format_helper.format_as_list(content, list_style)
            else:
                lines = self.format_helper.format_as_plain(str(content))
        elif fmt == "inline":
            if isinstance(content, list):
                lines = self.format_helper.format_as_inline(content)
            else:
                lines = self.format_helper.format_as_plain(str(content))
        elif fmt == "table":
            # 表格需要特殊处理，由具体渲染器实现
            lines = self.format_helper.format_as_plain(str(content))
        elif fmt == "heading":
            level = config.get("heading_level", 3)
            lines = self.format_helper.format_as_heading(str(content), level)
        elif fmt == "bold":
            lines = self.format_helper.format_as_bold(str(content))
        else:  # plain
            if isinstance(content, str):
                lines = self.format_helper.format_as_plain(content)
            else:
                lines = self.format_helper.format_as_plain("\n".join(str(c) for c in content))

        # 应用折叠
        if is_collapsible and lines:
            default_open = self.get_collapsible_default_open(config)
            lines = self.format_helper.wrap_collapsible(lines, label, default_open)

        return lines
