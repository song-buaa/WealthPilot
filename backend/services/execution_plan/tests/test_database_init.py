"""数据库初始化必须注册执行计划 ORM 表。"""
from sqlalchemy import inspect

from app import database


def test_init_db_registers_execution_plan_tables(tmp_path, monkeypatch):
    """全新 SQLite 初始化后，执行计划及分批表可直接使用。"""
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "execution_plan.db"))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)

    database.init_db()

    table_names = set(inspect(database.get_engine()).get_table_names())
    assert {"execution_plans", "execution_tranches"} <= table_names
