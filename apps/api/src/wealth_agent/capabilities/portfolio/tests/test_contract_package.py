from __future__ import annotations

from importlib import import_module


def test_portfolio_contract_package_is_importable() -> None:
    contract = import_module("wealth_agent.capabilities.portfolio.contract")

    assert contract.__name__ == "wealth_agent.capabilities.portfolio.contract"
