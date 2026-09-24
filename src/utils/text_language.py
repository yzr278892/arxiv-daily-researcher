"""Small language checks shared by TL;DR generation and report rendering."""

import re


def has_chinese_text(value: str) -> bool:
    """Accept Chinese prose that may contain English terms or formulas."""
    return len(re.findall(r"[\u3400-\u9fff]", value)) >= 3
