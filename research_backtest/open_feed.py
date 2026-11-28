"""每session一个Open-only时钟点；时间与价格由冻结数据转换生成。"""
from QuantConnect.Python import PythonData
from QuantConnect import SubscriptionTransportMedium
from QuantConnect.Data import SubscriptionDataSource
from datetime import datetime
import json


class FrozenOpenPoint(PythonData):
    def GetSource(self,config,date,isLiveMode):
        if isLiveMode:raise ValueError('FIXTURE_NOT_LIVE')
        return SubscriptionDataSource('/data/custom/fixture_open_events.jsonl',SubscriptionTransportMedium.LocalFile)

    def Reader(self,config,line,date,isLiveMode):
        if not line.strip():return None
        row=json.loads(line)
        event=FrozenOpenPoint()
        event.Symbol=config.Symbol
        # AddData明确使用纽约时区；点事件EndTime=Time，09:30只含当时已知Open。
        event.Time=datetime.strptime(row['session_date']+' 09:30','%Y-%m-%d %H:%M')
        event.Value=1
        event['payload_json']=line
        return event
