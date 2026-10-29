"""Shared SQLite store. Rows older than RETENTION_DAYS are deleted on every connect; fresh rows are reused instead of re-fetched."""
import sqlite3
from datetime import datetime, timedelta

RETENTION_DAYS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(day TEXT, orig TEXT, dest TEXT, cabin TEXT, price REAL, airlines TEXT, fetched TEXT, nonstop INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS awards(day TEXT, orig TEXT, dest TEXT, miles INT, taxes REAL, PRIMARY KEY(day, orig, dest));
CREATE TABLE IF NOT EXISTS heat(hkey TEXT, day TEXT, tier TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS booking(hkey TEXT, day TEXT, nights INT, price REAL, fetched TEXT);
CREATE TABLE IF NOT EXISTS hotel(day TEXT, query TEXT, filt TEXT, nights INT, hotel TEXT, price REAL, fetched TEXT, adults INT DEFAULT 2);
"""


def cutoff():
    return (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()


def connect(path="tracker.db"):
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    try:  # databases created before `adults` existed
        db.execute("ALTER TABLE hotel ADD COLUMN adults INT DEFAULT 2")
    except sqlite3.OperationalError:
        pass
    try:  # databases from the fast-flights era: its prices were unreliable, so start clean
        db.execute("ALTER TABLE prices ADD COLUMN nonstop INT DEFAULT 1")
        db.execute("DELETE FROM prices")
    except sqlite3.OperationalError:
        pass
    for t in ("prices", "hotel", "heat", "booking"):
        db.execute(f"DELETE FROM {t} WHERE fetched < ?", (cutoff(),))
    db.commit()
    return db


if __name__ == "__main__":  # self-check
    import os, tempfile
    from datetime import datetime as dt
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    d = connect(p)
    old, new = (dt.now() - timedelta(days=6)).isoformat(), dt.now().isoformat()
    for f in (old, new):
        d.execute("INSERT INTO prices(day, orig, dest, cabin, price, airlines, fetched) VALUES('2026-11-01','ATH','FRA','economy',100,'Aegean',?)", (f,))
    d.commit()
    assert [r[0] for r in connect(p).execute("SELECT fetched FROM prices")] == [new]
    print("ok")
