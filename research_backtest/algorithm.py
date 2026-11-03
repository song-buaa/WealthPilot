"""多年基础设施固定数量基准；沿用P1-C-2开盘point，不是实盘执行。"""
from pathlib import Path
from datetime import timedelta
import json
from QuantConnect.Algorithm import QCAlgorithm
from QuantConnect import Resolution, DataNormalizationMode, TimeZones, AccountType
from QuantConnect.Brokerages import DefaultBrokerageModel
from QuantConnect.Data.Market import TradeBar
from QuantConnect.Orders.Fees import FeeModel, OrderFee
from QuantConnect.Orders.Slippage import NullSlippageModel
from QuantConnect.Securities import CashAmount
from open_feed import FrozenOpenPoint


class FixedResearchFee(FeeModel):
    def GetOrderFee(self, parameters):
        return OrderFee(CashAmount(1, 'USD'))


class AVHistoryBenchmark(QCAlgorithm):
    def Initialize(self):
        self.model=json.loads(Path('/data/model.json').read_text())
        self.SetStartDate(*map(int,self.model['anchor'].split('-')))
        self.SetEndDate(*map(int,self.model['end'].split('-')))
        self.SetTimeZone(TimeZones.NewYork); self.SetCash(self.model['capital'])
        self.SetBenchmark(lambda time: 1.0)
        self.SetBrokerageModel(DefaultBrokerageModel(AccountType.Cash))
        self.Settings.DailyPreciseEndTime=True
        self.symbols={}; self.seen=set(); self.daily=[]; self.orders=[]; self.actions=[]; self.opens=[]
        for ticker in self.model['symbols']:
            sec=self.AddEquity(ticker,Resolution.Daily,fillForward=False,leverage=1,extendedMarketHours=False)
            sec.SetDataNormalizationMode(DataNormalizationMode.Raw)
            sec.SetFeeModel(FixedResearchFee()); sec.SetSlippageModel(NullSlippageModel())
            self.symbols[ticker]=sec.Symbol
        self.feed=self.AddData(FrozenOpenPoint,'AVHOPEN',Resolution.Daily).Symbol
        self.Schedule.On(self.DateRules.EveryDay(next(iter(self.symbols.values()))),
                         self.TimeRules.At(17,5),self.Decide)

    def cash(self):
        return dict(settled={str(k):str(v.Amount) for k,v in self.Portfolio.CashBook.items()},
                    unsettled={str(k):str(v.Amount) for k,v in self.Portfolio.UnsettledCashBook.items()})

    def clock(self):
        return self.UtcTime.strftime('%Y-%m-%dT%H:%M:%SZ')

    def OnData(self,data):
        day=self.Time.strftime('%Y-%m-%d')
        if data.ContainsKey(self.feed):
            payload=json.loads(str(data[self.feed]['payload_json']))
            if payload['available_at'] != self.clock(): raise ValueError('OPEN_FRONTIER_MISMATCH')
            if day in self.seen: raise ValueError('FULL_BAR_VISIBLE_AT_OPEN')
            for p in payload['opens']:
                sym=self.symbols[p['symbol']];px=float(p['open'])
                self.Securities[sym].SetMarketPrice(TradeBar(self.Time,sym,px,px,px,px,0,timedelta(0)))
            self.opens.append(dict(day=day,utc=self.clock(),full_bar_seen=False))
        for sym,e in data.Dividends.items():
            self.actions.append(dict(type='DIVIDEND',symbol=str(sym),utc=self.clock(),
                distribution=str(e.Distribution),quantity=str(self.Portfolio[sym].Quantity),cash=self.cash()))
        for sym,e in data.Splits.items():
            self.actions.append(dict(type='SPLIT',symbol=str(sym),utc=self.clock(),
                factor=str(e.SplitFactor),split_type=str(e.Type),quantity=str(self.Portfolio[sym].Quantity),cash=self.cash()))
        if all(data.Bars.ContainsKey(sym) for sym in self.symbols.values()):
            self.seen.add(day)
            if day >= self.model['start']:
                self.daily.append(dict(day=day,utc=self.clock(),nav=str(self.Portfolio.TotalPortfolioValue),
                    cash=self.cash(),fees_cumulative=str(self.Portfolio.TotalFees),
                    holdings={t:str(self.Portfolio[s].Quantity) for t,s in self.symbols.items()},
                    closes={t:str(data.Bars[s].Close) for t,s in self.symbols.items()}))

    def Decide(self):
        day=self.Time.strftime('%Y-%m-%d')
        if day not in self.seen: return
        for p in self.model['plan']:
            if p['signal_date']==day:
                self.MarketOnOpenOrder(self.symbols[p['symbol']],p['quantity'],tag='FIXED_PRIOR_CLOSE_INFRA_TEST')

    def OnOrderEvent(self,e):
        self.orders.append(dict(id=e.OrderId,symbol=str(e.Symbol),utc=str(e.UtcTime),
            status=str(e.Status),quantity=str(e.FillQuantity),price=str(e.FillPrice),
            fee=str(e.OrderFee.Value.Amount),message=str(e.Message),cash=self.cash()))

    def OnEndOfAlgorithm(self):
        Path('/results/history-observation.json').write_text(json.dumps(dict(
            daily=self.daily,orders=self.orders,actions=self.actions,opens=self.opens,
            total_orders=len(list(self.Transactions.GetOrders())),
            scope='NATIVE_RAW_OPEN_POINT_PROXY_DEFAULT_CASH_SETTLEMENT_NOT_BROKER'),indent=2))
