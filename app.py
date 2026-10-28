import sqlite3
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from collector import fetch_range

db = sqlite3.connect("tracker.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS prices(day TEXT, orig TEXT, dest TEXT, cabin TEXT, price REAL, airlines TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS awards(day TEXT, orig TEXT, dest TEXT, miles INT, taxes REAL, PRIMARY KEY(day, orig, dest));
""")

st.title("AEGEAN fare & Miles+Bonus tracker")
c = st.sidebar
orig, dest = c.text_input("From", "ATH").upper(), c.text_input("To", "FRA").upper()
start = c.date_input("From date", date(2026, 11, 1))
end = c.date_input("To date", date(2026, 11, 30))

if c.button("Fetch cash prices (≈4s/day)"):
    bar, n = st.progress(0.0), (end - start).days + 1
    for i, (d, cabin, price, air) in enumerate(fetch_range(start, end, orig, dest)):
        if price is not None:
            db.execute("INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                       (d.isoformat(), orig, dest, cabin, price, air, datetime.now().isoformat()))
            db.commit()
        bar.progress((i + 1) / (2 * n))

q = "WHERE orig=? AND dest=? AND day BETWEEN ? AND ?"
args = (orig, dest, start.isoformat(), end.isoformat())
p = pd.read_sql(f"SELECT * FROM prices {q} ORDER BY fetched", db, params=args)
latest = p.groupby(["day", "cabin"]).price.last().unstack() if len(p) else pd.DataFrame(columns=["economy", "business"])
days = pd.DataFrame(index=pd.date_range(start, end).strftime("%Y-%m-%d").rename("day"))
a = pd.read_sql(f"SELECT day, miles, taxes FROM awards {q}", db, params=args).set_index("day")

st.subheader("Miles+Bonus business awards (enter manually from your AEGEAN login)")
ed = st.data_editor(days.join(a).reset_index(), hide_index=True, disabled=["day"], key=f"{orig}{dest}{start}{end}")
for r in ed.dropna(subset=["miles"]).itertuples():
    db.execute("INSERT OR REPLACE INTO awards VALUES(?,?,?,?,?)", (r.day, orig, dest, int(r.miles), r.taxes or 0))
db.commit()

out = days.join(latest).join(ed.set_index("day"))
out["miles value €/1000"] = ((out.business - out.taxes.fillna(0)) / out.miles * 1000).round(1)
st.subheader("Daily comparison")
st.dataframe(out)
if out[["economy", "business"]].notna().any().any():
    st.success("Cheapest — " + " · ".join(f"{k}: {out[k].idxmin()} (€{out[k].min():.0f})"
                                           for k in ("economy", "business") if out[k].notna().any()))
    st.plotly_chart(px.line(out.reset_index(), x="day", y=["economy", "business"], markers=True))
out.to_excel("export.xlsx")
st.download_button("Download Excel", open("export.xlsx", "rb"), f"aegean_{orig}_{dest}.xlsx")
