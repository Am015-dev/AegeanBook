"""Permanent history, kept as CSV files in the repo. The app's own database only keeps a few days (RETENTION_DAYS) and is rebuilt on every
restart; these files are never trimmed. To stay small they store changes: a row is added when its value differs from the last one stored
for the same key, so a price that stays the same for a month is one row, not thirty."""
import csv
import os


def append_changes(path, rows, key, fields):
    """Append to the CSV at `path` the rows (dicts) whose `fields` differ from the last stored row with the same `key`; returns how many.
    The header is taken from the file when it exists, otherwise from the first new row."""
    path = str(path)
    last, header = {}, None
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            for r in reader:
                last[tuple(r[k] for k in key)] = tuple(r[k] for k in fields)
    new = []
    for r in rows:
        k, v = tuple(str(r[c]) for c in key), tuple("" if r[c] is None else str(r[c]) for c in fields)
        if last.get(k) != v:
            new.append(r)
            last[k] = v
    if not new:
        return 0
    fresh = header is None
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, header or list(new[0]), extrasaction="ignore")
        if fresh:
            w.writeheader()
        w.writerows(new)
    return len(new)


def export_excel(sheets):
    """The whole history as one .xlsx (bytes): `sheets` is {sheet name: csv path}; every row, newest recording first."""
    import io

    import pandas as pd  # only here: the daily GitHub job imports this module without pandas
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for name, path in sheets.items():
            have = os.path.exists(path) and os.path.getsize(path) > 0
            df = pd.read_csv(path) if have else pd.DataFrame({"info": ["No history yet"]})
            if have and "fetched" in df:
                df = df.sort_values("fetched", ascending=False)
            df.to_excel(w, sheet_name=name, index=False)
    return buf.getvalue()


def export_zip(paths):
    """The raw CSV files, zipped (bytes); files that do not exist yet are skipped."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            if os.path.exists(p):
                z.write(p, arcname=os.path.basename(p))
    return buf.getvalue()


if __name__ == "__main__":  # self-check
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "h.csv")
    row = lambda day, price, fetched: dict(day=day, price=price, fetched=fetched)
    assert append_changes(p, [row("d1", 100, "t1"), row("d2", 50, "t1")], ("day",), ("price",)) == 2
    assert append_changes(p, [row("d1", 100, "t2"), row("d2", 55, "t2")], ("day",), ("price",)) == 1  # only d2 changed
    assert len(open(p).read().splitlines()) == 4
    print("ok")
