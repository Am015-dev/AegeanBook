"""When to pay with award miles: the day where an AEGEAN Miles+Bonus seat replaces the most cash, per cabin. One small module the Overview
and the Flights page share (new, so Streamlit Cloud loads it fresh)."""
import pandas as pd

WORTH = 10.0  # € a mile is worth per 1,000, the Flights page's default ("My miles are worth"): above it, miles beat cash


def best_use(cash, miles, taxes=None):
    """The day where miles replace the most cash: dict(day, miles, taxes, cash, value = € saved per 1,000 miles), or None when no day has
    both a cash fare and an award seat. cash, miles, taxes: Series by iso day."""
    both = pd.concat([pd.to_numeric(cash, errors="coerce").rename("cash"), pd.to_numeric(miles, errors="coerce").rename("miles")],
                     axis=1, join="inner").dropna()
    both = both[both.miles > 0]
    if both.empty:
        return None
    tax = pd.to_numeric(taxes, errors="coerce").reindex(both.index).fillna(0) if taxes is not None else pd.Series(0.0, index=both.index)
    value = (both.cash - tax) / both.miles * 1000
    d = value.idxmax()
    return dict(day=d, miles=int(both.miles[d]), taxes=float(tax[d]), cash=float(both.cash[d]), value=float(value[d]))


def sentence(cabin, b, worth=WORTH):
    """'Business: use miles on Tue 24 Nov: 16,935 miles + €30 instead of €524 cash, €29 for each 1,000 miles.' or the advice to pay cash."""
    if b is None:
        return f"{cabin}: no award seat saved on a day with a cash fare."
    day = f"{pd.Timestamp(b['day']):%a %-d %b}"
    taxes = f" + €{b['taxes']:.0f}" if b["taxes"] else ""
    if b["value"] >= worth:
        return (f"{cabin}: use miles on {day}: {b['miles']:,} miles{taxes} instead of €{b['cash']:.0f} cash, "
                f"€{b['value']:.0f} for each 1,000 miles.")
    return (f"{cabin}: pay cash. The best day for miles, {day}, gets only €{b['value']:.0f} for each 1,000 miles "
            f"({b['miles']:,} miles{taxes} against €{b['cash']:.0f}), under the €{worth:.0f} a mile is worth.")


if __name__ == "__main__":  # self-check
    c = pd.Series({"2026-11-24": 524.0, "2026-11-25": 200.0})
    m = pd.Series({"2026-11-24": 16935, "2026-11-25": 16935})
    b = best_use(c, m, pd.Series({"2026-11-24": 30.0}))
    assert b["day"] == "2026-11-24" and round(b["value"]) == 29 and b["taxes"] == 30
    assert sentence("Business", b).startswith("Business: use miles on Tue 24 Nov: 16,935 miles + €30 instead of €524 cash")
    assert sentence("Economy", best_use(pd.Series({"2026-11-24": 75.0}), pd.Series({"2026-11-24": 12000}))).startswith("Economy: pay cash.")
    assert best_use(c, pd.Series(dtype=float)) is None
    print("ok")
