"""
Portfolio API 路由 — 账户总览
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from fastapi.responses import PlainTextResponse, Response

from app import state as _state
from backend.services import portfolio_service as svc

router = APIRouter()


def _pid() -> int:
    """获取默认 portfolio_id（startup() 已保证初始化）"""
    return _state.portfolio_id


# ── 总览 ──────────────────────────────────────────────────────────────────────

@router.get("/summary")
def get_summary():
    """资产总览：BalanceSheet 结构化数据"""
    result = svc.get_summary(_pid())
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/positions")
def get_positions(segment: Optional[str] = Query(default=None, description="过滤 segment: 投资|养老|公积金")):
    """持仓列表，可按 segment 过滤"""
    return svc.get_positions(_pid(), segment=segment)


@router.get("/liabilities")
def get_liabilities():
    """负债列表"""
    return svc.get_liabilities(_pid())


@router.get("/alerts")
def get_alerts():
    """偏差预警列表（策略偏离 / 纪律触发 / 风险暴露）"""
    return svc.get_alerts(_pid())


# ── 导入 ──────────────────────────────────────────────────────────────────────

@router.post("/import/csv")
async def import_positions_csv(file: UploadFile = File(...)):
    """
    导入持仓 CSV（全量覆盖）。
    Content-Type: multipart/form-data
    """
    data = await file.read()
    result = svc.import_from_csv(data, _pid(), content_type="positions")
    if result["errors"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.post("/liabilities/import/csv")
async def import_liabilities_csv(file: UploadFile = File(...)):
    """
    导入负债 CSV（全量覆盖）。
    """
    data = await file.read()
    result = svc.import_from_csv(data, _pid(), content_type="liabilities")
    if result["errors"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.post("/import/broker-csv")
async def import_broker_csv(
    file: UploadFile = File(...),
    broker: str = Query(..., description="券商名称：老虎证券 | 富途证券"),
):
    """
    老虎证券 / 富途证券 CSV 导入（按平台替换，其他平台数据不受影响）。
    """
    data = await file.read()
    result = svc.import_from_broker_csv(data, broker, _pid())
    if result["errors"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    return result


@router.post("/import/screenshot")
async def import_screenshot(
    file: UploadFile = File(...),
    platform: str = Query(..., description="银行/券商名称，如 招商银行|支付宝|建设银行|雪盈证券|国金证券"),
):
    """
    银行/券商截图解析导入（Vision API）。
    """
    data = await file.read()
    result = svc.import_from_screenshot(data, platform, _pid())
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


# ── 删除 ──────────────────────────────────────────────────────────────────────

@router.delete("/positions")
def clear_positions():
    """清空所有持仓（保留负债）"""
    svc.clear_positions(_pid())
    return {"message": "持仓已清空"}


# ── 导出 ──────────────────────────────────────────────────────────────────────

def _csv_response(text: str, filename: str) -> Response:
    """返回带 UTF-8 BOM 的 CSV（兼容 Excel / Mac 本地打开不乱码）"""
    bom_bytes = "\ufeff".encode("utf-8") + text.encode("utf-8")
    return Response(
        content=bom_bytes,
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/positions.csv")
def export_positions():
    """导出持仓 CSV"""
    return _csv_response(svc.export_positions_csv(_pid()), "positions.csv")


@router.get("/export/liabilities.csv")
def export_liabilities():
    """导出负债 CSV"""
    return _csv_response(svc.export_liabilities_csv(_pid()), "liabilities.csv")


@router.get("/template/positions.csv")
def position_template():
    """下载持仓 CSV 模板"""
    return _csv_response(svc.get_position_csv_template(), "positions_template.csv")


@router.get("/template/liabilities.csv")
def liability_template():
    """下载负债 CSV 模板"""
    return _csv_response(svc.get_liability_csv_template(), "liabilities_template.csv")


@router.post("/import/fund-e-excel")
async def import_fund_e_excel(file: UploadFile = File(...)):
    """
    导入基金E账户App导出的Excel(.xlsx)。
    按销售机构(platform)分组替换持仓,不影响境外券商和中信证券。
    """
    import tempfile, os
    from app.fund_e_account_importer import parse_fund_e_excel

    content = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        platform_positions = parse_fund_e_excel(tmp_path)
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail=f"文件解析失败: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not platform_positions:
        raise HTTPException(status_code=400, detail="未解析到任何持仓数据")

    portfolio_id = _pid()
    total = 0
    platform_counts = {}

    for platform, positions in platform_positions.items():
        svc._replace_positions_by_platform(portfolio_id, platform, positions)
        platform_counts[platform] = len(positions)
        total += len(positions)

    return {
        "success": True,
        "total": total,
        "platforms": platform_counts,
        "message": f"成功导入 {total} 条基金持仓",
    }
