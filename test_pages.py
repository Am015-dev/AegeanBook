"""Every page under views/ runs against the data committed in data/ without raising: a page that only breaks on real data (a column the
CSV no longer has, a date past the horizon, an empty history) is caught here, not on Streamlit Cloud. The unit tests give each page its own
small data; this one uses the repository's."""
import glob
import os

import pytest

import db
import gh

HERE = os.path.dirname(os.path.abspath(__file__))
COMMITTED = {"DAILY": "daily.csv", "DAILY_HOTEL": "daily_hotel.csv", "DAILY_AWARD": "daily_award.csv", "ROUTES": "routes.csv",
             "HOTEL_SEARCHES": "hotel_searches.csv", "SKYEXPRESS": "skyexpress.csv"}


@pytest.mark.parametrize("page", sorted(os.path.basename(p) for p in glob.glob(os.path.join(HERE, "views", "*.py"))))
def test_every_page_runs_on_the_committed_data(page, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    for name, file in COMMITTED.items():  # conftest points these at nothing; here the real files, as the deployed app reads them
        monkeypatch.setattr(db, name, os.path.join(HERE, "data", file))
    assert gh.data_file("data/daily.csv") is None  # still nothing from GitHub: the committed copies only
    monkeypatch.chdir(tmp_path)  # tracker.db is built here from the CSVs, not in the checkout
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    at = AppTest.from_file(os.path.join(HERE, "views", page), default_timeout=120).run()
    assert not at.exception, (page, [e.value for e in at.exception])
