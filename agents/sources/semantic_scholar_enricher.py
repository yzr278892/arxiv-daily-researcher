"""
Semantic Scholar 数据增强器

通过 Semantic Scholar API 获取 AI 生成的 TLDR 和其他增强信息。
"""

import logging
import requests
from typing import Optional, Dict
from urllib.parse import quote

logger = logging.getLogger(__name__)


class SemanticScholarEnricher:
    """
    Semantic Scholar 数据增强器。

    功能：
    - 根据 DOI 获取论文的 AI 生成 TLDR
    - 获取其他补充信息（引用数、影响力评分等）
    """

    API_BASE_URL = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 Semantic Scholar 增强器。

        参数:
            api_key: Semantic Scholar API Key（可选，提高速率限制）
        """
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ArxivDailyResearcher/2.0 (https://github.com/yzr278892/arxiv-daily-researcher; yzr278892@gmail.com)"
        })

        if api_key:
            self.session.headers.update({
                "x-api-key": api_key
            })

    def get_tldr(self, doi: str) -> Optional[str]:
        """
        获取论文的 AI 生成 TLDR。

        参数:
            doi: 论文的 DOI

        返回:
            Optional[str]: TLDR 文本，失败时返回 None
        """
        try:
            # 清理 DOI（移除可能的前缀）
            clean_doi = doi.replace("https://doi.org/", "").replace("DOI:", "").strip()

            # 构建请求 URL
            url = f"{self.API_BASE_URL}/paper/DOI:{clean_doi}"
            params = {
                "fields": "tldr"
            }

            response = self.session.get(url, params=params, timeout=10)

            # 如果找不到论文，静默返回 None
            if response.status_code == 404:
                logger.debug(f"Semantic Scholar 未找到论文: {clean_doi}")
                return None

            response.raise_for_status()
            data = response.json()

            # 提取 TLDR
            tldr_obj = data.get("tldr")
            if tldr_obj and isinstance(tldr_obj, dict):
                tldr_text = tldr_obj.get("text", "")
                if tldr_text:
                    logger.debug(f"成功获取 TLDR: {clean_doi[:30]}...")
                    return tldr_text

            return None

        except requests.exceptions.RequestException as e:
            logger.debug(f"Semantic Scholar API 请求失败: {e}")
            return None
        except Exception as e:
            logger.debug(f"获取 TLDR 失败: {e}")
            return None

    def get_paper_info(self, doi: str) -> Optional[Dict]:
        """
        获取论文的完整信息（包括 TLDR、引用数、arXiv链接等）。

        参数:
            doi: 论文的 DOI

        返回:
            Optional[Dict]: 包含各种信息的字典，失败时返回 None
        """
        try:
            clean_doi = doi.replace("https://doi.org/", "").replace("DOI:", "").strip()

            url = f"{self.API_BASE_URL}/paper/DOI:{clean_doi}"
            params = {
                "fields": "tldr,citationCount,influentialCitationCount,publicationTypes,externalIds"
            }

            response = self.session.get(url, params=params, timeout=10)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            result = {}

            # 提取 TLDR
            tldr_obj = data.get("tldr")
            if tldr_obj and isinstance(tldr_obj, dict):
                result["tldr"] = tldr_obj.get("text")

            # 提取引用数
            if "citationCount" in data:
                result["citation_count"] = data["citationCount"]

            if "influentialCitationCount" in data:
                result["influential_citation_count"] = data["influentialCitationCount"]

            if "publicationTypes" in data:
                result["publication_types"] = data["publicationTypes"]

            # 提取 arXiv ID（关键新增功能）
            external_ids = data.get("externalIds", {})
            if external_ids and "ArXiv" in external_ids:
                arxiv_id = external_ids["ArXiv"]
                result["arxiv_id"] = arxiv_id
                result["arxiv_url"] = f"https://arxiv.org/abs/{arxiv_id}"
                logger.debug(f"找到 arXiv 版本: {arxiv_id}")

            return result if result else None

        except Exception as e:
            logger.debug(f"获取 Semantic Scholar 信息失败: {e}")
            return None

    def get_arxiv_id(self, doi: str) -> Optional[str]:
        """
        专门获取论文的 arXiv ID。

        参数:
            doi: 论文的 DOI

        返回:
            Optional[str]: arXiv ID，如 "2401.12345"，失败时返回 None
        """
        try:
            clean_doi = doi.replace("https://doi.org/", "").replace("DOI:", "").strip()

            url = f"{self.API_BASE_URL}/paper/DOI:{clean_doi}"
            params = {
                "fields": "externalIds"
            }

            response = self.session.get(url, params=params, timeout=10)

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()

            external_ids = data.get("externalIds", {})
            if external_ids and "ArXiv" in external_ids:
                return external_ids["ArXiv"]

            return None

        except Exception as e:
            logger.debug(f"获取 arXiv ID 失败: {e}")
            return None
