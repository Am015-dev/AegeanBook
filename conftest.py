import pytest

import db
import gh
import network


@pytest.fixture(autouse=True)
def no_real_data(monkeypatch):
    """Every test gets its own data only: not the committed data/*.csv, and nothing from GitHub (a GITHUB_TOKEN in the environment
    would otherwise download the repository's live files into the test databases)."""
    for name in ("DAILY", "DAILY_HOTEL", "DAILY_AWARD", "ROUTES", "HOTEL_SEARCHES", "SKYEXPRESS"):
        monkeypatch.setattr(db, name, f"/nonexistent/{name.lower()}.csv")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(gh, "data_file", lambda path, tok=None: None)
    monkeypatch.setattr(network, "OUT", network.Path("/nonexistent/network.csv"))  # the route network found so far: no pair hidden in tests
