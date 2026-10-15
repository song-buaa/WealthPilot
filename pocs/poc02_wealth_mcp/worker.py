"""Dedicated business-runtime process; stdout contains only an allowlisted contract."""
import argparse
import json
import os
from pathlib import Path
import sys
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def configure_environment(config):
    # No dotenv loading or inherited broker credentials. Optional Tiger
    # read-only cash configuration is a separate local operator choice.
    for key in list(os.environ):
        if key.startswith(("TIGER_", "IBKR_", "FUTU_", "OPENAI_")):
            os.environ.pop(key)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PUBLIC_DEMO_MODE"] = "false"
    tiger = config.get("tiger_cash", {})
    cash_configured = all(tiger.get(k) for k in ("TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"))
    if tiger and not cash_configured:
        raise ValueError("INCOMPLETE_CASH_CONFIGURATION")
    for key in ("TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"):
        if cash_configured:
            os.environ[key] = tiger[key]
    return cash_configured


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text())
        cash_configured = configure_environment(config)
        from reader import read_overview, ReadError
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = read_overview(config["database_path"], int(config["portfolio_id"]),
                                   cash_configured=cash_configured,
                                   database_contract=config.get("database_contract_path"))
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    except Exception as error:
        safe = {"PORTFOLIO_NOT_FOUND"}
        code = str(error) if type(error).__name__ == "ReadError" and str(error) in safe else "WEALTH_READ_UNAVAILABLE"
        print(json.dumps({"ok": False, "error": code}))


if __name__ == "__main__":
    main()
