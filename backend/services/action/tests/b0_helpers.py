"""Explicit synthetic owner/review preparation for existing lifecycle tests."""
from app.models import Portfolio


def test_owner(session):
    session.info["portfolio_id"] = 1
    if session.get(Portfolio, 1) is None:
        session.add(Portfolio(id=1, name="Isolated test Portfolio"))
    session.commit()


def reviewed_place(manager, strategy_id, params):
    # Tests explicitly commit their own strategy preparation. Production callers
    # must do the same and obtain risk facts from the original risk service.
    manager._audit("b0_strategy_created", {"strategy_id": strategy_id})
    manager.session.commit()
    review_id = manager.create_order_review(strategy_id, params,
            risk_snapshot={"passed": True, "requires_confirmation": False, "warnings": []})
    manager.session.commit()
    return manager.place_order(strategy_id, {**params, "review_id": review_id})


def sync_committed(manager, order_id):
    manager.session.commit()
    return manager.sync_order_status(order_id)


def cancel_committed(manager, order_id):
    manager.session.commit()
    return manager.cancel_order(order_id)
