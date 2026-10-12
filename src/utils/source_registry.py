"""Declarative paper-source registry shared by the worker and WebUI.

Only data is accepted here. A portable configuration may describe an
ISSN-backed OpenAlex journal or opt into the built-in Hugging Face Papers
adapter, but it can never provide an import path or executable Python code.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List


OPENALEX_JOURNAL_TYPE = "openalex_journal"
HUGGINGFACE_PAPERS_TYPE = "huggingface_papers"
HUGGINGFACE_PAPERS_SOURCE_NAME = "huggingface_papers"
CORE_SOURCE_CODES = ("arxiv", "prl")

SOURCE_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$", re.IGNORECASE)


# PRL remains a first-class core worker source for backward compatibility.
# The WebUI presents it alongside the copyable built-in declarations, but
# never persists it as one: it stays in ``enabled_sources``. OpenAlexSource
# imports the same mapping, so adding a custom journal through config does not
# require editing worker code.
OPENALEX_JOURNAL_CATALOG: Dict[str, Dict[str, Any]] = {
    "prl": {
        "full_name": "Physical Review Letters",
        "issn": ["0031-9007", "1079-7114"],
        "display_name": "PRL",
    },
    "pra": {
        "full_name": "Physical Review A",
        "issn": ["2469-9926", "1050-2947"],
        "display_name": "PRA",
    },
    "prb": {
        "full_name": "Physical Review B",
        "issn": ["2469-9950", "1098-0121"],
        "display_name": "PRB",
    },
    "prc": {
        "full_name": "Physical Review C",
        "issn": ["2469-9985", "0556-2813"],
        "display_name": "PRC",
    },
    "prd": {
        "full_name": "Physical Review D",
        "issn": ["2470-0010", "1550-7998"],
        "display_name": "PRD",
    },
    "pre": {
        "full_name": "Physical Review E",
        "issn": ["2470-0045", "1539-3755"],
        "display_name": "PRE",
    },
    "prx": {
        "full_name": "Physical Review X",
        "issn": ["2160-3308"],
        "display_name": "PRX",
    },
    "prxq": {
        "full_name": "PRX Quantum",
        "issn": ["2691-3399"],
        "display_name": "PRX Quantum",
    },
    "rmp": {
        "full_name": "Reviews of Modern Physics",
        "issn": ["0034-6861", "1539-0756"],
        "display_name": "RMP",
    },
    "jcap": {
        "full_name": "Journal of Cosmology and Astroparticle Physics",
        "issn": ["1475-7508", "1475-7516"],
        "display_name": "JCAP",
    },
    "nature": {
        "full_name": "Nature",
        "issn": ["0028-0836", "1476-4687"],
        "display_name": "Nature",
    },
    "nature_physics": {
        "full_name": "Nature Physics",
        "issn": ["1745-2473", "1745-2481"],
        "display_name": "Nat. Phys.",
    },
    "nature_communications": {
        "full_name": "Nature Communications",
        "issn": ["2041-1723"],
        "display_name": "Nat. Commun.",
    },
    "nature_astronomy": {
        "full_name": "Nature Astronomy",
        "issn": ["2397-3366"],
        "display_name": "Nat. Astron.",
    },
    "science": {
        "full_name": "Science",
        "issn": ["0036-8075", "1095-9203"],
        "display_name": "Science",
    },
    "science_advances": {
        "full_name": "Science Advances",
        "issn": ["2375-2548"],
        "display_name": "Sci. Adv.",
    },
    "science_bulletin": {
        "full_name": "Science Bulletin",
        "issn": ["2095-9273", "2095-9281"],
        "display_name": "Sci. Bull.",
    },
    "npj_quantum_information": {
        "full_name": "npj Quantum Information",
        "issn": ["2056-6387"],
        "display_name": "npj QI",
    },
    "quantum": {
        "full_name": "Quantum",
        "issn": ["2521-327X"],
        "display_name": "Quantum",
    },
    "new_journal_of_physics": {
        "full_name": "New Journal of Physics",
        "issn": ["1367-2630"],
        "display_name": "NJP",
    },
}


def _required_text(raw: Dict[str, Any], name: str, label: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"额外来源 {label} 的 {name} 必须是非空字符串")
    normalized = value.strip()
    if len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"额外来源 {label} 的 {name} 包含无效字符或过长")
    return normalized


def validate_source_definitions(
    definitions: Any,
    *,
    reserved_codes: Iterable[str] = CORE_SOURCE_CODES,
) -> List[Dict[str, Any]]:
    """Validate and normalize JSON-compatible extra-source declarations."""
    if definitions is None:
        return []
    if not isinstance(definitions, list):
        raise ValueError("额外来源 definitions 必须是 JSON 数组")
    if len(definitions) > 100:
        raise ValueError("额外来源最多允许 100 项")

    reserved = {str(code).strip().lower() for code in reserved_codes}
    seen = set()
    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(definitions):
        if not isinstance(raw, dict):
            raise ValueError(f"额外来源第 {index + 1} 项必须是对象")
        allowed = {"type", "code", "display_name", "full_name", "issn"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(
                f"额外来源第 {index + 1} 项包含不支持字段: {', '.join(sorted(unknown))}"
            )

        source_type = raw.get("type")
        if source_type not in {OPENALEX_JOURNAL_TYPE, HUGGINGFACE_PAPERS_TYPE}:
            raise ValueError(
                f"额外来源第 {index + 1} 项 type 必须是 "
                f"{OPENALEX_JOURNAL_TYPE} 或 {HUGGINGFACE_PAPERS_TYPE}"
            )

        code = raw.get("code")
        if not isinstance(code, str):
            raise ValueError(f"额外来源第 {index + 1} 项 code 必须是字符串")
        code = code.strip().lower()
        if not SOURCE_CODE_RE.fullmatch(code):
            raise ValueError(
                f"额外来源第 {index + 1} 项 code 无效；只能使用小写字母、数字和下划线"
            )
        if code in reserved:
            raise ValueError(f"额外来源 code 与核心来源冲突: {code}")
        if code in seen:
            raise ValueError(f"额外来源 code 重复: {code}")

        display_name = _required_text(raw, "display_name", code)
        full_name = _required_text(raw, "full_name", code)
        item: Dict[str, Any] = {
            "type": source_type,
            "code": code,
            "display_name": display_name,
            "full_name": full_name,
        }

        if source_type == OPENALEX_JOURNAL_TYPE:
            issn = raw.get("issn")
            if not isinstance(issn, list) or not issn:
                raise ValueError(f"额外来源 {code} 的 issn 必须是非空数组")
            normalized_issn = []
            for value in issn:
                if not isinstance(value, str):
                    raise ValueError(f"额外来源 {code} 的 ISSN 必须是字符串")
                value = value.strip().upper()
                if not ISSN_RE.fullmatch(value):
                    raise ValueError(f"额外来源 {code} 的 ISSN 无效: {value}")
                if value not in normalized_issn:
                    normalized_issn.append(value)
            item["issn"] = normalized_issn
        else:
            if code != HUGGINGFACE_PAPERS_SOURCE_NAME:
                raise ValueError(
                    "huggingface_papers 类型必须使用固定 code: huggingface_papers"
                )
            if "issn" in raw:
                raise ValueError("huggingface_papers 类型不接受 issn 字段")

        seen.add(code)
        normalized.append(item)
    return normalized


def merge_source_catalog(
    builtins: Dict[str, Dict[str, Any]], definitions: Any
) -> Dict[str, Dict[str, Any]]:
    """Return a journal catalog extended with validated OpenAlex entries."""
    catalog = copy.deepcopy(builtins)
    for definition in validate_source_definitions(definitions):
        if definition["type"] != OPENALEX_JOURNAL_TYPE:
            continue
        code = definition["code"]
        catalog[code] = {
            "full_name": definition["full_name"],
            "issn": list(definition["issn"]),
            "display_name": definition["display_name"],
        }
    return catalog


def source_codes_from_definitions(definitions: Any) -> List[str]:
    return [item["code"] for item in validate_source_definitions(definitions)]


def source_display_names(definitions: Any = None) -> Dict[str, str]:
    """Build stable human-facing names for reports and the local viewer."""
    names = {
        "arxiv": "arXiv",
        "semantic_scholar": "Semantic Scholar",
        HUGGINGFACE_PAPERS_SOURCE_NAME: "Hugging Face Papers",
    }
    names.update(
        {code: info["full_name"] for code, info in OPENALEX_JOURNAL_CATALOG.items()}
    )
    for definition in validate_source_definitions(definitions):
        names[definition["code"]] = definition["full_name"]
    return names


def builtin_extra_source_definitions() -> List[Dict[str, Any]]:
    """Return copyable declarations for every former non-core checkbox."""
    definitions: List[Dict[str, Any]] = []
    for code, info in OPENALEX_JOURNAL_CATALOG.items():
        if code == "prl":
            continue
        definitions.append(
            {
                "type": OPENALEX_JOURNAL_TYPE,
                "code": code,
                "display_name": info["display_name"],
                "full_name": info["full_name"],
                "issn": list(info["issn"]),
            }
        )
    definitions.append(
        {
            "type": HUGGINGFACE_PAPERS_TYPE,
            "code": HUGGINGFACE_PAPERS_SOURCE_NAME,
            "display_name": "Hugging Face Papers",
            "full_name": "Hugging Face Papers（补充精选流）",
        }
    )
    return copy.deepcopy(definitions)


def definitions_for_builtin_codes(codes: Iterable[str]) -> List[Dict[str, Any]]:
    """Convert former built-in source codes to the declarative form."""
    requested = {str(code).strip().lower() for code in codes}
    templates = {item["code"]: item for item in builtin_extra_source_definitions()}
    unknown = sorted(requested - set(templates))
    if unknown:
        raise ValueError(f"未知额外来源代码: {', '.join(unknown)}")
    return [copy.deepcopy(templates[code]) for code in templates if code in requested]
