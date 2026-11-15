import pytest

import db
import gh
import network
import price_insights


@pytest.fixture(autouse=True)
def no_real_data(monkeypatch):
    """Every test gets its own data only: not the committed data/*.csv, and nothing from GitHub (a GITHUB_TOKEN in the environment
    would otherwise download the repository's live files into the test databases)."""
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD", "ROUTES", "HOTEL_SEARCHES", "SKYEXPRESS"):
        monkeypatch.setattr(db, name, f"/nonexistent/{name.lower()}.csv")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(gh, "data_file", lambda path, tok=None: None)
    monkeypatch.setattr(network, "OUT", network.Path("/nonexistent/network.csv"))  # the route network found so far: no pair hidden in tests
    monkeypatch.setattr(price_insights, "HISTORY", "/nonexistent/history_prices.csv")  # every fare change ever collected: grows each day
    import xotelo  # no test asks Xotelo live: a test that needs its answers fakes _get itself

    def offline(path, **params):
        raise ConnectionError("no network in tests")
    monkeypatch.setattr(xotelo, "_get", offline)
