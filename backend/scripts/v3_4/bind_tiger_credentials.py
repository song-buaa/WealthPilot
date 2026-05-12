"""
WealthPilot v3.4 Tiger 凭证绑定 CLI

将 Tiger API 私钥安全写入系统 keyring(macOS Keychain / Linux Secret Service)。
绑定完成后,TigerBrokerAdapter 从 keyring 读取凭证,不再依赖文件路径。

用法:
  python backend/scripts/v3_4/bind_tiger_credentials.py                              # 交互绑定
  python backend/scripts/v3_4/bind_tiger_credentials.py --from-file backend/secrets/tiger_private_key.pem  # 从文件绑定
  python backend/scripts/v3_4/bind_tiger_credentials.py --show                       # 查看绑定状态
  python backend/scripts/v3_4/bind_tiger_credentials.py --unbind                     # 解绑
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from backend.services.action.brokers.credentials import KeyringCredentialProvider
from backend.services.action.brokers.tiger import TIGER_PAPER_ACCOUNT


def _detect_mode(account_id: str) -> str:
    return "paper" if account_id == TIGER_PAPER_ACCOUNT else "live"


def cmd_bind(args):
    """交互式或从文件绑定凭证。"""
    provider = KeyringCredentialProvider()

    tiger_id = args.tiger_id or input("Tiger ID: ").strip()
    account_id = args.account_id or input("Account ID: ").strip()

    if args.from_file:
        pk_path = Path(args.from_file)
        if not pk_path.exists():
            print(f"文件不存在: {pk_path}")
            sys.exit(1)
        private_key_pem = pk_path.read_text().strip()
    else:
        print("请粘贴 PEM 格式私钥(以 -----BEGIN RSA PRIVATE KEY----- 开头,")
        print("以 -----END RSA PRIVATE KEY----- 结尾),然后按两次回车:")
        lines = []
        while True:
            line = input()
            if not line and lines and lines[-1] == "":
                break
            lines.append(line)
        private_key_pem = "\n".join(lines).strip()

    if "BEGIN RSA PRIVATE KEY" not in private_key_pem:
        print("错误: 私钥格式不正确,应为 PKCS#1 PEM 格式")
        sys.exit(1)

    mode = _detect_mode(account_id)
    broker_key = f"tiger.{mode}"

    print(f"\n绑定信息:")
    print(f"  Tiger ID:    {tiger_id}")
    print(f"  Account ID:  {account_id}")
    print(f"  Mode:        {mode}")
    print(f"  Broker Key:  {broker_key}")
    print(f"  私钥指纹:    {private_key_pem.splitlines()[1][:16]}...")

    confirm = input("\n确认绑定? (y/N): ").strip().lower()
    if confirm != "y":
        print("已取消")
        return

    provider.save(broker_key, {
        "tiger_id": tiger_id,
        "account_id": account_id,
        "private_key_pem": private_key_pem,
    })
    print(f"\n凭证已写入 keyring: {broker_key}")
    if args.from_file:
        print(f"原文件 {args.from_file} 仍保留,你可以手动删除。")


def cmd_show(args):
    """查看绑定状态。"""
    provider = KeyringCredentialProvider()
    for key in ["tiger.paper", "tiger.live"]:
        creds = provider.load(key)
        if creds:
            pem = creds.get("private_key_pem", "")
            fingerprint = pem.splitlines()[1][:16] if len(pem.splitlines()) > 1 else "N/A"
            print(f"  {key}: tiger_id={creds.get('tiger_id')} "
                  f"account=***{creds.get('account_id', '')[-5:]} "
                  f"私钥指纹={fingerprint}...")
        else:
            print(f"  {key}: 未绑定")


def cmd_unbind(args):
    """解绑凭证。"""
    provider = KeyringCredentialProvider()
    for key in ["tiger.paper", "tiger.live"]:
        provider.delete(key)
        print(f"  已删除: {key}")


def main():
    parser = argparse.ArgumentParser(description="WealthPilot Tiger 凭证绑定 CLI")
    sub = parser.add_subparsers(dest="command")

    bind_p = sub.add_parser("bind", help="绑定凭证")
    bind_p.add_argument("--tiger-id", default=None)
    bind_p.add_argument("--account-id", default=None)
    bind_p.add_argument("--from-file", default=None, help="从 PEM 文件读取私钥")

    sub.add_parser("show", help="查看绑定状态")
    sub.add_parser("unbind", help="解绑所有凭证")

    args = parser.parse_args()

    if args.command == "bind":
        cmd_bind(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "unbind":
        cmd_unbind(args)
    else:
        # 默认交互绑定
        cmd_bind(argparse.Namespace(tiger_id=None, account_id=None, from_file=None))


if __name__ == "__main__":
    main()
