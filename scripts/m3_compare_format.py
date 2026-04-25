"""
M3-6 格式合约对比脚本。

对 LI:US 分别取 v1 和 v2 路径输出，对比 5 个维度：
1. 字符串前缀
2. URL 标签
3. 单行性
4. 长度
5. 总行数
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AV_DEV_MOCK", "1")


def get_v2_output(symbol_str: str) -> list[str]:
    """从 ViewpointRepository.query_for_decision 获取 v2 输出。"""
    from app.database import get_session
    from research_v2 import repository

    session = get_session()
    try:
        return repository.query_for_decision(session, symbol_str)
    finally:
        session.close()


def get_v1_online_output(asset_name: str) -> list[str]:
    """从 _search_research_online 获取 v1 联网搜索输出（作为 v1 路径参考）。"""
    from decision_engine.data_loader import _search_research_online
    return _search_research_online(asset_name)


def check_prefix(lines: list[str], label: str) -> bool:
    """检查每行是否带 [用户资料] 或 [联网参考] 前缀。"""
    valid = ("[用户资料]", "[联网参考]")
    all_ok = True
    for i, line in enumerate(lines):
        if not line.startswith(valid):
            print(f"  ❌ [{label}] line {i} 缺少前缀: {line[:60]}...")
            all_ok = False
    return all_ok


def check_url_tag(lines: list[str], label: str) -> bool:
    """检查带 URL 的行是否有 [ref:xxx] 标签。"""
    all_ok = True
    for i, line in enumerate(lines):
        if "http" in line and "[ref:" not in line:
            print(f"  ⚠️  [{label}] line {i} 有 URL 但无 [ref:] 标签: {line[:80]}...")
            all_ok = False
    return all_ok


def check_single_line(lines: list[str], label: str) -> bool:
    """检查每行是否不含换行符。"""
    all_ok = True
    for i, line in enumerate(lines):
        if "\n" in line:
            print(f"  ❌ [{label}] line {i} 包含换行符")
            all_ok = False
    return all_ok


def check_length(lines: list[str], label: str) -> bool:
    """检查每行长度是否在合理范围（warning 但不 fail）。"""
    all_ok = True
    for i, line in enumerate(lines):
        ln = len(line)
        if ln < 20:
            print(f"  ⚠️  [{label}] line {i} 过短 ({ln} 字): {line}")
            all_ok = False
        elif ln > 300:
            print(f"  ⚠️  [{label}] line {i} 过长 ({ln} 字): {line[:80]}...")
            all_ok = False
    return all_ok


def main():
    print("=" * 70)
    print("M3-6 格式合约对比")
    print("=" * 70)

    # 获取 v2 输出
    print("\n--- v2 输出（ViewpointRepository.query_for_decision）---")
    v2_lines = get_v2_output("LI:US")
    print(f"v2 行数: {len(v2_lines)}")
    for i, line in enumerate(v2_lines):
        print(f"  [{i}] {line[:120]}{'...' if len(line) > 120 else ''}")

    # 获取 v1 联网搜索输出（作为 v1 路径参考）
    print("\n--- v1 参考（_search_research_online）---")
    v1_online = get_v1_online_output("理想汽车")
    print(f"v1 联网搜索行数: {len(v1_online)}")
    for i, line in enumerate(v1_online[:5]):
        print(f"  [{i}] {line[:120]}{'...' if len(line) > 120 else ''}")
    if len(v1_online) > 5:
        print(f"  ... 共 {len(v1_online)} 条")

    # 对比 5 个维度
    print("\n" + "=" * 70)
    print("格式合约对比结果")
    print("=" * 70)

    results = {}

    # 维度 1: 前缀
    print("\n[维度 1] 字符串前缀")
    if v2_lines:
        ok = check_prefix(v2_lines, "v2")
        results["前缀"] = "PASS" if ok else "FAIL"
        print(f"  → v2: {'PASS ✅' if ok else 'FAIL ❌'}")
    else:
        results["前缀"] = "SKIP (v2 无数据)"
        print(f"  → v2: SKIP (无数据)")

    if v1_online:
        ok = check_prefix(v1_online, "v1")
        print(f"  → v1: {'PASS ✅' if ok else 'FAIL ❌'}")

    # 维度 2: URL 标签
    print("\n[维度 2] URL 标签")
    if v2_lines:
        ok = check_url_tag(v2_lines, "v2")
        results["URL标签"] = "PASS" if ok else "WARN"
        print(f"  → v2: {'PASS ✅' if ok else 'WARN ⚠️'}")
    else:
        results["URL标签"] = "SKIP"
        print(f"  → v2: SKIP")

    # 维度 3: 单行性
    print("\n[维度 3] 单行性")
    if v2_lines:
        ok = check_single_line(v2_lines, "v2")
        results["单行性"] = "PASS" if ok else "FAIL"
        print(f"  → v2: {'PASS ✅' if ok else 'FAIL ❌'}")
    else:
        results["单行性"] = "SKIP"
        print(f"  → v2: SKIP")

    # 维度 4: 长度
    print("\n[维度 4] 长度范围")
    if v2_lines:
        ok = check_length(v2_lines, "v2")
        results["长度"] = "PASS" if ok else "WARN"
        print(f"  → v2: {'PASS ✅' if ok else 'WARN ⚠️'}")
    else:
        results["长度"] = "SKIP"
        print(f"  → v2: SKIP")

    # 维度 5: 总行数比较
    print("\n[维度 5] 总行数")
    v2_count = len(v2_lines)
    v1_count = len(v1_online)
    print(f"  v2: {v2_count} 行")
    print(f"  v1 联网搜索: {v1_count} 行")
    if v1_count > 0 and v2_count > 0:
        ratio = v2_count / v1_count
        in_range = 0.3 <= ratio <= 3.0
        results["总行数"] = "PASS" if in_range else "WARN"
        print(f"  比率: {ratio:.1f}x ({'合理范围 ✅' if in_range else '偏离 ⚠️'})")
    elif v2_count == 0 and v1_count == 0:
        results["总行数"] = "PASS (两者都无数据)"
        print(f"  两者都无数据")
    else:
        results["总行数"] = "INFO"
        print(f"  无法直接比较（一方无数据）")

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)
    for dim, status in results.items():
        icon = "✅" if "PASS" in status else "⚠️" if "WARN" in status or "SKIP" in status else "❌"
        print(f"  {icon} {dim}: {status}")

    critical_fail = any("FAIL" in v for k, v in results.items() if k in ("前缀", "单行性"))
    if critical_fail:
        print("\n⛔ 关键维度失败，需要修复")
        sys.exit(1)
    else:
        print("\n✅ 格式合约对比通过")

    # ── Fallback 行为断言 ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Fallback 行为断言")
    print("=" * 70)
    _run_fallback_assertions()


def _run_fallback_assertions():
    """验证 _load_research 的 fallback 行为正确性。"""
    from unittest.mock import patch
    from app.database import get_session

    session = get_session()
    try:
        # 断言 a: v2 有数据时，_load_research 返回行数 == v2 单独行数
        print("\n[断言 a] v2 有数据时，返回行数 == v2 单独行数")
        from research_v2 import repository
        v2_only = repository.query_for_decision(session, "LI:US")
        if v2_only:
            from decision_engine.data_loader import _load_research
            lr_result = _load_research(session, pid=1, asset_name="理想汽车")
            if len(lr_result) == len(v2_only):
                print(f"  ✅ PASS: _load_research={len(lr_result)}, v2={len(v2_only)}")
            else:
                print(f"  ❌ FAIL: _load_research={len(lr_result)}, v2={len(v2_only)}")
        else:
            print(f"  ⚠️  SKIP: v2 无 confirmed 数据，无法验证")

        # 断言 b: v2 空时，_load_research 调用 _search_research_online 一次
        print("\n[断言 b] v2 空时，调用联网搜索一次")
        with patch("decision_engine.data_loader._search_research_online", return_value=["[联网参考] mock"]) as mock_online:
            from decision_engine.data_loader import _load_research as lr
            result_b = lr(session, pid=1, asset_name="不存在的标的XYZ")
            if mock_online.call_count == 1:
                print(f"  ✅ PASS: _search_research_online 被调用 {mock_online.call_count} 次")
            else:
                print(f"  ❌ FAIL: _search_research_online 被调用 {mock_online.call_count} 次（预期 1）")

        # 断言 c: v2 命中时，_search_research_online 不被调用
        print("\n[断言 c] v2 命中时，不调用联网搜索")
        if v2_only:
            with patch("decision_engine.data_loader._search_research_online") as mock_online_c:
                from decision_engine.data_loader import _load_research as lr2
                result_c = lr2(session, pid=1, asset_name="理想汽车")
                if mock_online_c.call_count == 0:
                    print(f"  ✅ PASS: _search_research_online 未被调用")
                else:
                    print(f"  ❌ FAIL: _search_research_online 被调用 {mock_online_c.call_count} 次（预期 0）")
        else:
            print(f"  ⚠️  SKIP: v2 无 confirmed 数据，无法验证")

    finally:
        session.close()


if __name__ == "__main__":
    main()
