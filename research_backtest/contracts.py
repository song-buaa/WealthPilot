"""从个人任务提取的纯文件契约；没有HTTP或凭据读取。"""
import json
from pathlib import Path
SYMBOLS=('SPY','QQQ','XLK','SMH','IGV')

def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
