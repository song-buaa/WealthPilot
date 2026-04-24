"""
UserUploadAdapter — 包装现有 parse_text / parse_url / parse_pdf 逻辑。

将用户上传的文本/URL/PDF 转换为 RawFact，供 ViewpointProcessor 加工。
"""

import logging
from datetime import datetime
from typing import Optional

from research_v2.adapters.base import InfoAdapter, RawFact
from research_v2.schemas import SourceRef, SourceType
from research_v2.symbol import Symbol

logger = logging.getLogger(__name__)


class UserUploadAdapter(InfoAdapter):
    """用户上传信息源适配器。

    与 AlphaVantage 不同，UserUpload 不按 symbol 拉取，
    而是接收用户主动上传的内容，转换为 RawFact。
    """

    @property
    def adapter_id(self) -> str:
        return "user_upload"

    @property
    def supported_source_types(self) -> list[SourceType]:
        return [SourceType.USER_UPLOAD]

    def is_symbol_supported(self, symbol: Symbol) -> bool:
        return True  # 用户上传支持所有市场

    def fetch(self, symbols: list[Symbol], since: Optional[datetime] = None) -> list[RawFact]:
        """UserUpload 不支持主动拉取，返回空列表。
        用户上传通过 fetch_from_text / fetch_from_url 方法处理。
        """
        return []

    def fetch_from_text(
        self,
        content: str,
        title: str = "",
        source_url: Optional[str] = None,
        affected_symbols: Optional[list[Symbol]] = None,
    ) -> RawFact:
        """从纯文本/Markdown 构造 RawFact。"""
        refs: list[SourceRef] = []
        if source_url:
            refs.append(SourceRef(ref_type="url", ref_value=source_url, title=title or None))

        return RawFact(
            source_type=SourceType.USER_UPLOAD,
            source_url=source_url,
            as_of=datetime.now(),
            affected_symbols=affected_symbols or [],
            payload={
                "title": title,
                "raw_content": content,
                "content_type": "text",
            },
            source_refs=refs,
        )

    def fetch_from_url(
        self,
        url: str,
        affected_symbols: Optional[list[Symbol]] = None,
    ) -> RawFact:
        """抓取 URL 正文后构造 RawFact。复用现有的 _fetch_url_text。"""
        from backend.services.research_service import _fetch_url_text

        content = _fetch_url_text(url)
        if not content:
            raise RuntimeError("无法抓取该链接内容")

        return self.fetch_from_text(
            content=content,
            title="",
            source_url=url,
            affected_symbols=affected_symbols,
        )

    def fetch_from_pdf(
        self,
        file_bytes: bytes,
        filename: str,
        affected_symbols: Optional[list[Symbol]] = None,
    ) -> RawFact:
        """解析 PDF 后构造 RawFact。复用现有的 _extract_pdf_text。"""
        from backend.services.research_service import _extract_pdf_text

        text, error = _extract_pdf_text(file_bytes)
        if error:
            raise RuntimeError(f"PDF 解析失败: {error}")
        if not text.strip():
            raise RuntimeError("PDF 中未提取到可读文字")

        return self.fetch_from_text(
            content=text,
            title=filename,
            affected_symbols=affected_symbols,
        )
