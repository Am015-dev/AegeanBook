"""Price alerts for every visitor: choose a route and a limit, get an email or a phone notification when a fare drops below it. The alert is
saved by alert_store (needs GITHUB_TOKEN, or GITHUB, in the secrets) and delivered by the daily run (alerts.py). Also opens the confirm and stop
links that the alert's messages carry."""
import importlib
import urllib.request
from datetime import date

import streamlit as st

import alert_store as store
import alerts
import overview as ov
import trip_plan as tp
from db import connect
from ui import route

if getattr(ov, "VERSION", 0) < 2:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ov = importlib.reload(ov)
db = connect()
today = date.today()
route("Price alerts", None, "Get an email or a phone notification when a fare drops below your limit. Checked every morning, free.")
def find_token():
    """The GitHub token, under any name with "github" in it (any case), at the top level or inside a [section]."""
    try:
        items = list(st.secrets.items())
    except Exception:  # no secrets file at all
        return None
    for name, value in sorted(items, key=lambda kv: "token" not in kv[0].lower()):
        if "github" in name.lower():
            values = value.values() if hasattr(value, "values") else [value]
            token = next((v.strip() for v in values if isinstance(v, str) and v.strip()), None)
            if token:
                return token


token = find_token()

q = st.query_params
for action, done in (("confirm", "is on: you will get an email when it is met"), ("stop", "is stopped: no more messages")):
    if action in q:
        if not token:
            st.error("Alerts are not set up on this app yet.")
        else:
            try:
                what = getattr(store, action)(token, q[action], q.get("code", ""))
                st.success(f"Your alert {what} {done}.")
            except store.AlertError as err:
                st.error(str(err))
        q.clear()

fares, summary, *_ = ov.collect(db, today)
if not summary:
    st.info("No fares are saved yet. The daily run fills them in every morning.")
    st.stop()
day = lambda d: f"{date.fromisoformat(d):%a %d %b}"

with st.container(border=True, key="search"):
    c = st.columns([2, 1, 1])
    leg = c[0].selectbox("Route", list(fares), format_func=lambda k: f"{k[0]} → {k[1]}", key="al-leg")
    cabin = c[1].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="al-cabin")
    unit = c[2].pills("In", ["eur", "miles"], default="eur", format_func={"eur": "Cash €", "miles": "Miles"}.get, required=True, key="al-unit")
    now = summary.get(leg, {}).get(cabin)
    c = st.columns([1, 2])
    limit = c[0].number_input("Alert me below", min_value=1, step=10 if unit == "eur" else 500,
                              value=int(now["min"] // 10 * 10) if unit == "eur" and now else 15000, key=f"al-limit-{leg[0]}{leg[1]}-{cabin}-{unit}",
                              help=f"Cheapest now: €{now['min']:,.0f} on {day(now['day'])}." if now else None)
    last = date.fromisoformat(max(s.index.max() for leg_ in fares.values() for s in leg_.values() if len(s)))
    periods = [("Any date in the next 90 days", "", "")] + [(l, a.isoformat(), b.isoformat()) for l, a, b in tp.months(today, last)
                                                             if l != "Next 30 days"]
    when = c[1].selectbox("Departing", range(len(periods)), format_func=lambda i: periods[i][0], key="al-when")
    how = st.pills("Send it to", ["email", "phone"], default="email", required=True, key="al-how",
                   format_func={"email": "My email", "phone": "My phone (free ntfy app)"}.get)
    email, topic = "", ""
    if how == "email":
        email = st.text_input("Email address", placeholder="you@example.com", key="al-email",
                              help="Used only for this alert. You confirm it once from the first email; every email has a link to stop it.")
    else:
        topic = st.session_state.setdefault("al-topic", store.topic())
        st.markdown("1. Install the free **ntfy** app: [iPhone](https://apps.apple.com/app/ntfy/id1625396347) · "
                    "[Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) (no account needed).\n"
                    "2. In the app, tap **+** (subscribe to topic) and enter this topic exactly:")
        st.code(topic, language=None)
        st.caption("Keep it to yourself: anyone who knows the topic can read its notifications. "
                   f"On a computer you can also open ntfy.sh/{topic} in the browser.")
    go = st.button("Create the alert", type="primary", icon=":material/notifications:", disabled=not token)

if not token:
    st.info("Alerts for visitors are not switched on yet. The app's owner adds a GitHub token once (see the README, “Price alerts”). "
            "Until then the owner can still create an alert as a GitHub issue:")
    try:  # names only, never values: tells the owner whether the app sees the secret at all
        names = ", ".join(sorted(st.secrets.keys())) or "none"
    except Exception:
        names = "none (no secrets saved)"
    st.caption(f"Secrets this app can see: {names}. The token goes in the app's Streamlit secrets (not GitHub's), as GITHUB = \"github_pat_...\".")
    _, start, end = periods[when]
    st.link_button("Create the alert on GitHub", alerts.new_issue_link(leg[0], leg[1], cabin, unit, limit, start, end))
elif go:
    _, start, end = periods[when]
    try:
        n, code = store.create(token, leg[0], leg[1], cabin, unit, limit, start, end, email=email, ntfy=topic)
    except store.AlertError as err:
        st.error(str(err))
    else:
        what = f"{leg[0]} → {leg[1]} {cabin} below " + (f"{limit:,.0f} miles" if unit == "miles" else f"€{limit:,.0f}")
        if email:
            try:  # with SMTP_USER / SMTP_PASSWORD in the app's secrets the confirmation goes now; otherwise the daily run sends it
                alerts.mail(email, f"Confirm your price alert: {what}",
                            f"Someone (hopefully you) asked the AEGEAN fare tracker to email this address when {what}.\n\n"
                            f"Turn the alert on: {alerts.link('confirm', n, code)}\n\nNot you? Ignore this email and nothing more will be sent.")
                store.mark_confirmation_sent(token, n, alerts.SENT)
                st.success(f"Almost done: we emailed {email}. Open the link in that email to turn on the alert ({what}). "
                           "Nothing else is sent until you do. Not there? Check the spam folder.")
            except Exception:
                st.success(f"Almost done: a confirmation email goes to {email} with tomorrow morning's check. Open its link to turn on "
                           f"the alert ({what}); nothing else is sent until you do.")
        else:
            try:  # a first notification now, so the visitor sees the phone setup works
                urllib.request.urlopen(urllib.request.Request(
                    f"https://ntfy.sh/{topic}", method="POST", headers={"Title": "Price alert created", "Tags": "airplane"},
                    data=f"You will be notified here when {what}. Stop it: {alerts.link('stop', n, code)}".encode()), timeout=15).read()
                st.success(f"Done: {what}. A first notification was just sent to your phone; the next one comes when a fare meets "
                           "the alert (checked every morning).")
            except Exception:
                st.success(f"Done: {what}. You will be notified in the ntfy app when a fare meets the alert (checked every morning).")
            st.session_state.pop("al-topic", None)  # a new topic for the next alert from this browser
        st.caption(f"Stop it any time with the link in its messages, or here: {alerts.link('stop', n, code)}")
