"""Synthetic native exports with the observed headers; never real account data."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

HK_HEADER = '证券名称\t证券代码\t交易市场\t当前数量\t股份数量\t可用股份\t参考成本价(港币)\t最新价(港币)\t持仓成本(人民币)\t最新市值(人民币)\t参考盈亏(人民币)\t参考盈亏比例(%)\t冻结数量\t在途买入\t在途卖出\t'
STOCK_HEADER = '证券代码\t证券名称\t股票余额\t可用余额\t冻结数量\t盈亏\t成本价\t盈亏比例()\t市价\t市值\t成本金额\t市场代码\t交易市场\t股东帐户\t实际数量\t单位数量\t资讯\t历史成交\t'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def make_batch(tmp_path, *, batch='first', cash='30.00', empty=False, query=None):
    root = (Path(tmp_path)/'exports').resolve()
    root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
    directory=root/batch;directory.mkdir(exist_ok=True);directory.chmod(0o700)
    query=query or datetime.now(timezone.utc)-timedelta(seconds=2)
    saved=query.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d  %H:%M:%S')
    total=__import__('decimal').Decimal(cash)+(0 if empty else 1460)
    stock='标题：资金股票\n营业部名称：SYNTHETIC\n股东：合成用户\n'+f'资金余额：{cash}\t 可取金额：0.00\n冻结金额：0.25\t 股票市值：{0 if empty else 1460}.00\n可用余额：-0.25\t 总 资 产：{total}\n\n------------------------------\n'+STOCK_HEADER+'\n'
    if not empty:stock+='510500\t沪深合成ETF\t100\t100\t0\t-123\t4.000\t-25.000\t3.000\t300.00\t400.00\t2\t上海Ａ股\tSYNTHETIC-SHAREHOLDER\t100\t1\t\t\t\n'
    stock+='\n------------------------------\n保存时间：'+saved+' \n'
    hgt=HK_HEADER+'\n'
    sgt=HK_HEADER+'\n'
    if not empty:
        hgt+='合成汽车\t="02015"\t沪HK\t10.00\t10.00\t10.00\t110.0\t90.0\t1000.00\t800.00\t-250.00\t-25.00\t0\t0.00\t0.00\t\n'
        hgt+='合成乙\t="01234"\t沪HK\t5.00\t5.00\t5.00\t50.0\t45.0\t210.00\t200.00\t-20.00\t-9.50\t0\t0.00\t0.00\t\n'
        sgt+='合成汽车\t="02015"\t深HK\t2.00\t2.00\t2.00\t110.0\t90.0\t200.00\t160.00\t-45.00\t-22.50\t0\t0.00\t0.00\t\n'
    files={'stock':stock.encode('gb18030'),'hgt':hgt.encode('gb18030'),'sgt':sgt.encode('gb18030')}
    receipt=dict(schema='guojin.native-batch.v1',portfolio_id=7,account_id='fixture-owner',
        account_total_assets_cny=str(total),files={k:dict(account_id='fixture-owner',account_verified_in_gui=True,
            evidence_ref='synthetic GUI evidence',queried_at=query.isoformat(),row_count=0 if empty else {'stock':1,'hgt':2,'sgt':1}[k],
            sha256=hashlib.sha256(v).hexdigest()) for k,v in files.items()})
    for key,name in {'stock':'stock.txt','hgt':'hgt.xls','sgt':'sgt.xls'}.items():
        (directory/name).write_bytes(files[key]);(directory/name).chmod(0o600)
    write_json(directory/'batch.json',receipt)
    write_json(root/'ready.json',dict(batch=batch))
    config=dict(schema='guojin.native.v1',portfolio_id=7,account_id='fixture-owner',inbox=str(root),
                shareholder_accounts=['SYNTHETIC-SHAREHOLDER'],legacy_positions=[])
    config_path=root/'config.json';write_json(config_path,config)
    return SimpleNamespace(root=root,directory=directory,files=files,receipt=receipt,config=config,config_path=config_path,query=query)
