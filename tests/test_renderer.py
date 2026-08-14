"""
Renderer 单元测试 — 5 个 case 覆盖：
1. user_upload 有 URL
2. user_upload 无 URL
3. alpha_vantage_news
4. alpha_vantage_fundamental
5. alpha_vantage_earnings
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_v2.schemas import (
    EventType,
    ExtractedKPI,
    FactsLayer,
    JudgmentLayer,
    NarrativeLayer,
    SourceRef,
    SourceType,
    ViewpointCard,
)
from research_v2.renderer import render_card


def _make_card(
    source_type: SourceType,
    thesis: str = "",
    bull_case: str = "",
    bear_case: str = "",
    url: str = "",
    kpi: ExtractedKPI = None,
    summary: str = "",
    event_type: EventType = EventType.OTHER,
) -> ViewpointCard:
    """构建测试用 ViewpointCard。"""
    refs = []
    if url:
        refs.append(SourceRef(ref_type="url", ref_value=url))

    return ViewpointCard(
        facts=FactsLayer(
            affected_symbols=["LI:US"],
            primary_symbol="LI:US",
            source_type=source_type,
            source_refs=refs,
            as_of=datetime(2026, 4, 1),
        ),
        narrative=NarrativeLayer(
            thesis=thesis,
            bull_case=bull_case,
            bear_case=bear_case,
            narrative_summary=summary,
            event_type=event_type,
            extracted_kpi=kpi,
        ),
        judgment=JudgmentLayer(),
    )


class TestRendererUserUploadWithURL(unittest.TestCase):
    """Case 1: user_upload 有 URL"""

    def test_output_has_user_prefix_and_ref(self):
        card = _make_card(
            source_type=SourceType.USER_UPLOAD,
            thesis="理想汽车Q1财报超预期，交付量同比增长35%，毛利率环比改善2个百分点",
            url="https://example.com/report",
            bull_case="新车型L6销量持续攀升，产品力强于竞品",
            bear_case="价格战压力依然存在，品牌溢价空间收窄",
            event_type=EventType.EARNINGS,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 1)
        self.assertTrue(lines[0].startswith("[投研观点]"))
        self.assertIn("[ref:https://example.com/report]", lines[0])
        self.assertIn("理想汽车Q1财报超预期", lines[0])
        for line in lines:
            self.assertNotIn("\n", line)


class TestRendererUserUploadNoURL(unittest.TestCase):
    """Case 2: user_upload 无 URL"""

    def test_output_has_user_prefix_no_ref(self):
        card = _make_card(
            source_type=SourceType.USER_UPLOAD,
            thesis="特斯拉Cybertruck产能爬坡低于预期，短期交付目标可能下调",
            bull_case="FSD技术领先优势明显，软件订阅收入增长可期",
            event_type=EventType.DELIVERY_OR_SALES_DATA,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 1)
        self.assertTrue(lines[0].startswith("[投研观点] "))
        self.assertNotIn("[ref:", lines[0])
        self.assertIn("特斯拉Cybertruck", lines[0])
        self.assertTrue(lines[0].endswith("(数据截至 2026-04-01)"))


class TestRendererAlphaVantageNews(unittest.TestCase):
    """Case 3: alpha_vantage_news"""

    def test_output_has_third_party_prefix(self):
        card = _make_card(
            source_type=SourceType.ALPHA_VANTAGE_NEWS,
            thesis="大和重申理想汽车买入评级，目标价上调至28美元",
            url="https://news.example.com/li-rating",
            kpi=ExtractedKPI(
                analyst_target_upside=15.3,
                target_price=28.0,
                current_price=24.3,
            ),
            event_type=EventType.ANALYST_RATING,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 1)
        self.assertTrue(lines[0].startswith("[Alpha Vantage]"))
        self.assertIn("[ref:https://news.example.com/li-rating]", lines[0])
        self.assertIn("大和重申理想汽车买入评级", lines[0])
        kpi_line = [l for l in lines if "目标价上行空间" in l]
        self.assertTrue(len(kpi_line) >= 1)
        for line in lines:
            self.assertIn("(数据截至 2026-04-01)", line)


class TestRendererAlphaVantageFundamental(unittest.TestCase):
    """Case 4: alpha_vantage_fundamental"""

    def test_kpi_rendering(self):
        card = _make_card(
            source_type=SourceType.ALPHA_VANTAGE_FUNDAMENTAL,
            thesis="理想汽车2025年全年营收1400亿元，同比增长48%，净利率提升至8.2%",
            kpi=ExtractedKPI(
                revenue_yoy=48.0,
                net_margin=8.2,
                gross_margin=22.5,
                free_cash_flow=120.0,
            ),
            event_type=EventType.FUNDAMENTAL_SNAPSHOT,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 2)
        self.assertTrue(lines[0].startswith("[Alpha Vantage] "))
        kpi_line = lines[1]
        self.assertIn("营收同比+48.0%", kpi_line)
        self.assertIn("净利率8.2%", kpi_line)
        self.assertNotIn("[ref:", kpi_line)
        for line in lines:
            self.assertIn("(数据截至 2026-04-01)", line)


class TestRendererAlphaVantageEarnings(unittest.TestCase):
    """Case 5: alpha_vantage_earnings"""

    def test_earnings_card(self):
        card = _make_card(
            source_type=SourceType.ALPHA_VANTAGE_EARNINGS,
            thesis="理想汽车Q4 EPS超预期12%，交付量创历史新高",
            url="https://api.example.com/earnings/LI",
            kpi=ExtractedKPI(
                eps_surprise_pct=12.0,
                earnings_yoy=85.0,
                deliveries_latest=160000,
                deliveries_yoy=25.3,
            ),
            bull_case="交付量持续增长，规模效应显现",
            bear_case="竞争加剧可能压制未来毛利率",
            event_type=EventType.EARNINGS,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 2)
        self.assertTrue(lines[0].startswith("[Alpha Vantage]"))
        self.assertIn("[ref:https://api.example.com/earnings/LI]", lines[0])
        self.assertIn("EPS超预期", lines[0])
        kpi_line = [l for l in lines if "盈利同比" in l]
        self.assertTrue(len(kpi_line) >= 1)
        self.assertIn("+85.0%", kpi_line[0])
        bull_bear = [l for l in lines if "看多" in l or "看空" in l]
        self.assertTrue(len(bull_bear) >= 1)
        for line in lines:
            self.assertIn("(数据截至 2026-04-01)", line)


class TestRendererAllLinesHavePrefix(unittest.TestCase):
    """Case 6: 所有返回行都带当前可追溯的具体来源标签。"""

    def test_all_lines_prefixed(self):
        card = _make_card(
            source_type=SourceType.ALPHA_VANTAGE_FUNDAMENTAL,
            thesis="理想汽车2025年全年营收1400亿元，同比增长48%",
            bull_case="新车型产品周期强劲，L6/L7销量持续攀升",
            bear_case="价格战压力持续，毛利率可能承压下行",
            kpi=ExtractedKPI(
                revenue_yoy=48.0,
                earnings_yoy=62.0,
                gross_margin=22.5,
                net_margin=8.2,
                deliveries_latest=500000,
                deliveries_yoy=35.0,
            ),
            event_type=EventType.FUNDAMENTAL_SNAPSHOT,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 2)
        valid_prefixes = ("[投研观点]", "[Alpha Vantage]", "[AKShare]", "[Perplexity]", "[数据源]")
        for i, line in enumerate(lines):
            self.assertTrue(
                line.startswith(valid_prefixes),
                f"Line {i} missing prefix: {line[:50]}...",
            )


class TestRendererFallbackToSummary(unittest.TestCase):
    """Case 7: thesis/bull/bear 都空时 fallback 到 narrative_summary"""

    def test_summary_fallback(self):
        card = _make_card(
            source_type=SourceType.USER_UPLOAD,
            summary="该研报讨论了新能源汽车行业2026年上半年的竞争格局变化和政策影响",
            event_type=EventType.OTHER,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 1)
        self.assertTrue(lines[0].startswith("[投研观点]"))
        self.assertIn("新能源汽车行业", lines[0])
        self.assertIn("(数据截至 2026-04-01)", lines[0])


class TestRendererEmptyKPI(unittest.TestCase):
    """Case 8: extracted_kpi 所有字段 None 时不产生 KPI 行也不抛异常"""

    def test_empty_kpi_no_crash(self):
        card = _make_card(
            source_type=SourceType.ALPHA_VANTAGE_NEWS,
            thesis="大和重申理想汽车买入评级，维持目标价25美元不变",
            kpi=ExtractedKPI(),
            event_type=EventType.OTHER,
        )
        lines = render_card(card)
        self.assertTrue(len(lines) >= 1)
        self.assertIn("大和重申", lines[0])
        kpi_lines = [l for l in lines if "营收同比" in l or "盈利同比" in l or "毛利率" in l]
        self.assertEqual(len(kpi_lines), 0, "Empty KPI should not produce KPI line")
        for line in lines:
            self.assertIn("(数据截至 2026-04-01)", line)


if __name__ == "__main__":
    unittest.main()
