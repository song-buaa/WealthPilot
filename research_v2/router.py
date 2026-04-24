"""
InfoRouter — 按 symbol 市场后缀分发到对应 Adapter。

路由规则:
  :US → AlphaVantageAdapter
  :HK / :SH / :SZ → v2 不覆盖专属 Adapter，仅 UserUpload 可用
  所有 symbol → UserUpload 始终可用（并列）
"""

import logging
from datetime import datetime
from typing import Optional

from research_v2.adapters.alpha_vantage import AlphaVantageAdapter
from research_v2.adapters.base import AdapterQuotaError, InfoAdapter, RawFact
from research_v2.adapters.user_upload import UserUploadAdapter
from research_v2.symbol import Symbol

logger = logging.getLogger(__name__)


class InfoRouter:
    """按 symbol 市场后缀选择合适的 Adapter 并执行 fetch。"""

    def __init__(self) -> None:
        self._av_adapter = AlphaVantageAdapter()
        self._upload_adapter = UserUploadAdapter()

    def get_auto_adapters(self, symbol: Symbol) -> list[InfoAdapter]:
        """返回可自动拉取的 Adapter 列表（不含 UserUpload，因为它需要用户主动上传）。"""
        adapters: list[InfoAdapter] = []
        if symbol.market == "US":
            adapters.append(self._av_adapter)
        elif symbol.market in ("HK", "SH", "SZ"):
            logger.info(
                "%s 市场暂无专属 Adapter（v2 不覆盖），仅支持 UserUpload",
                symbol.market,
            )
        return adapters

    def fetch_all(
        self, symbol: Symbol, since: Optional[datetime] = None
    ) -> list[RawFact]:
        """对 symbol 调用所有可用的自动 Adapter，合并结果。

        AdapterQuotaError 被捕获并降级为空结果 + 日志。
        """
        adapters = self.get_auto_adapters(symbol)
        results: list[RawFact] = []

        for adapter in adapters:
            try:
                facts = adapter.fetch([symbol], since=since)
                results.extend(facts)
            except AdapterQuotaError as e:
                logger.warning(
                    "Adapter %s 配额受限，降级跳过: %s",
                    adapter.adapter_id,
                    e,
                )

        return results

    @property
    def upload_adapter(self) -> UserUploadAdapter:
        """获取 UserUpload adapter 实例（用于用户主动上传场景）。"""
        return self._upload_adapter

    @property
    def av_adapter(self) -> AlphaVantageAdapter:
        """获取 AlphaVantage adapter 实例（用于单独调用子能力场景）。"""
        return self._av_adapter
