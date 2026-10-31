"""Price alerts for every visitor: say a route, a trip and a price, get an email or a phone notification when a fare drops below it. Opened
from the Overview's boarding pass ("Alert me if it gets cheaper") the form is already filled in with the pass: route, cabin, cash or miles,
return or one way, nights, the month and the pass's price as the limit (query params route, cabin, unit, trip, nights, depart, back, when,
price). The alert is saved by alert_store (needs GITHUB_TOKEN, or GITHUB, in the secrets) and delivered by the daily run (alerts.py). Also
opens the confirm and stop links that the alert's messages carry, and lists the visitor's own alerts, each with a Stop button."""
import html
import importlib
import re
import urllib.request
from datetime import date, timedelta

import streamlit as st

import alert_store as store
import alerts
import deals
import overview as ov
import trip_plan as tp
from db import connect
from ui import route

if getattr(ov, "VERSION", 0) < 4:  # Streamlit Cloud keeps an older copy of a module across redeploys
    ov = importlib.reload(ov)
if getattr(tp, "VERSION", 0) < 6:
    tp = importlib.reload(tp)
if getattr(deals, "VERSION", 0) < 5:
    deals = importlib.reload(deals)
if getattr(alerts, "VERSION", 0) < 1:
    alerts = importlib.reload(alerts)
if getattr(store, "VERSION", 0) < 1:
    store = importlib.reload(store)
db = connect()
today = date.today()
e = html.escape
route("Price alerts", None, "Tell us a route and a price. Every morning we check the fares; the day one drops below your price, you get an "
      "email or a phone notification. Free, no account.")


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


def iso(s):
    """A date from an iso string in a link, or None."""
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def when_text(start, end):
    """The departure window in words: "in December 2026", "between 12 Dec and 18 Dec", or "in the next 90 days"."""
    if not start or not end:
        return "in the next 90 days"
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    nxt = date(b.year + (b.month == 12), b.month % 12 + 1, 1)
    if (a.year, a.month) == (b.year, b.month) and a.day <= 2 and b == nxt - timedelta(1):
        return f"in {a:%B %Y}"
    return f"between {a:%-d %b} and {b:%-d %b}"


token = find_token()
proven = st.session_state.setdefault("al-proven", {})  # {("email", address) or ("ntfy", topic): True}: contacts this visitor showed are theirs

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
            else:  # the link's code proves the alert is this visitor's: the page can show every alert of that address or phone
                who = store.owner(token, q[action], q.get("code", ""))
                if who:
                    proven[tuple(who)] = True
        q.clear()

far = ov.legs(db, today, tp.HORIZON)  # every route with fares saved, as far ahead as any month was fetched
if not far:
    st.info("No fares are saved yet. The daily run fills them in every morning.")
    st.stop()
*_, awards = ov.collect(db, today)
last = max(date.fromisoformat(s.index.max()) for leg_ in far.values() for s in leg_.values() if len(s))
if "route" in q:  # from the Overview's boarding pass ("Alert me if it gets cheaper"): the form starts on that trip, its price as the limit
    leg_ = tuple(q["route"].upper().split("-"))
    if leg_ in far:
        st.session_state["al-leg"] = leg_
        cabin_ = q.get("cabin") if q.get("cabin") in ("economy", "business") else "economy"
        unit_ = q.get("unit") if q.get("unit") in ("eur", "miles") else "eur"
        st.session_state["al-cabin"], st.session_state["al-unit"] = cabin_, unit_
        depart_, back_ = iso(q.get("depart")), iso(q.get("back"))
        trip_ = q.get("trip") if q.get("trip") in ("return", "oneway") else "return" if back_ else "oneway" if depart_ else "return"
        st.session_state["al-trip"] = trip_
        span = [int(n) for n in re.findall(r"\d+", q.get("nights", ""))]
        if not span and depart_ and back_:
            span = [(back_ - depart_).days]  # only the pass's own dates: a stay as long as that one
        if span:
            st.session_state["al-nights"] = (max(1, min(60, min(span))), max(1, min(60, max(span))))
        when_ = [iso(s) for s in q.get("when", "").split(",")]
        if len(when_) == 2 and all(when_) and when_[0] <= when_[1]:  # the pass's own days to leave on
            want = (max(when_[0], today + timedelta(1)).isoformat(), when_[1].isoformat())
        elif depart_:  # only the pass's dates: the month it leaves in
            want = next(((p.start.isoformat(), p.end.isoformat()) for p in tp.months(today, last)
                         if p.label != "Next 30 days" and p.start <= depart_ <= p.end), None)
        else:
            want = None
        if want:
            st.session_state["al-want"] = st.session_state["al-extra"] = want
        try:
            st.session_state[f"al-limit-{leg_[0]}{leg_[1]}-{cabin_}-{unit_}-{trip_}"] = max(1, int(round(float(q["price"]))))
        except (KeyError, ValueError):
            pass
    q.clear()
day = lambda d: f"{d:%a %d %b}"

with st.container(border=True, key="search"):
    c = st.columns([2, 1, 1])
    leg = c[0].selectbox("Route", list(far), format_func=lambda k: f"{tp.city(k[0])} → {tp.city(k[1])}", key="al-leg")
    cabin = c[1].pills("Cabin", ["economy", "business"], default="economy", format_func=str.capitalize, required=True, key="al-cabin")
    unit = c[2].pills("Pay with", ["eur", "miles"], default="eur", format_func={"eur": "Cash €", "miles": "Miles"}.get, required=True, key="al-unit")
    c = st.columns([1, 1.5, 1.5])
    trip = c[0].pills("Trip", ["return", "oneway"], default="return", format_func={"return": "Return", "oneway": "One way"}.get, required=True,
                      key="al-trip")
    nights = (2, 7)
    if trip == "return":
        nights = c[1].slider("Nights", 1, 60, (2, 7), key="al-nights",
                             help="How long you would stay, shortest to longest. The alert watches every return trip of that length.")
    periods = [("Any date in the next 90 days", "", "")] + [(p.title, p.start.isoformat(), p.end.isoformat()) for p in tp.months(today, last)
                                                             if p.label != "Next 30 days"]  # a month not fetched yet says so
    extra = st.session_state.get("al-extra")  # the pass's own days to leave on, an option of their own when they are not a whole month
    if extra and extra not in [p[1:] for p in periods]:
        a, b = date.fromisoformat(extra[0]), date.fromisoformat(extra[1])
        periods.insert(1, (tp.period(a, b, last, today).title, *extra))
    if want := st.session_state.pop("al-want", None):  # from the pass: start on its days, once
        st.session_state["al-when"] = next((i for i, p in enumerate(periods) if p[1:] == want), 0)
    when = c[2].selectbox("Leaving", range(len(periods)), format_func=lambda i: periods[i][0], key="al-when")
    _, start, end = periods[when]
    first = date.fromisoformat(start) if start else today + timedelta(1)
    final = date.fromisoformat(end) if end else today + timedelta(alerts.DAYS)
    # what the trip costs now, priced as the Overview's boarding pass (deals.best_trip): the limit starts just under it
    src = far if unit == "eur" else awards
    out = src.get(leg, {})
    out = out[cabin] if cabin in out else None
    back = src.get((leg[1], leg[0]), {}) if trip == "return" else {}
    back = back[cabin] if cabin in back else None
    now = deals.best_trip(out, back, first, final, range(nights[0], nights[1] + 1)) if out is not None and len(out) else None
    if trip == "return" and now is not None and now["back"] is None:
        now = None  # one way is not what a return alert asks for
    money = (lambda v: f"€{v:,.0f}") if unit == "eur" else (lambda v: f"{v:,.0f} miles")
    c = st.columns([1, 2])
    step, floor = (10, 10) if unit == "eur" else (500, 500)
    value = int(now["price"] // floor * floor) if now else (100 if unit == "eur" else 15000)
    limit = c[0].number_input("Alert me below", min_value=1, step=step, value=max(value, 1), key=f"al-limit-{leg[0]}{leg[1]}-{cabin}-{unit}-{trip}")
    kind = "return" if trip == "return" else "one way"
    if now:
        c[1].caption(f"Cheapest now: {money(now['price'])} {kind}, leave {day(now['depart'])}"
                     + (f", back {day(now['back'])} ({now['nights']} nights)" if now["back"] else "") + ".")
    elif trip == "return":
        c[1].caption("No return trips saved for those dates and nights yet, so there is nothing to compare with. "
                     "The alert still runs every morning.")
    else:
        c[1].caption(f"No {cabin} {'fares' if unit == 'eur' else 'award seats'} saved for those dates yet. The alert still runs every morning.")
    alert = dict(orig=leg[0], dest=leg[1], cabin=cabin, unit=unit, limit=float(limit), trip=trip, nights=nights)
    what = alerts.describe(alert, when_text(start, end))
    st.markdown(f'<p class="group">Tell me when {e(what)}.</p>', unsafe_allow_html=True)
    how = st.pills("Send it to", ["email", "phone"], default="email", required=True, key="al-how",
                   format_func={"email": "My email", "phone": "My phone (free ntfy app)"}.get)
    st.caption("Email: you confirm your address once, from the first email; every email has a link that stops the alert. "
               "Phone: the free ntfy app shows the notifications, no account needed; you subscribe to a private topic shown here.")
    email, topic = "", ""
    if how == "email":
        email = st.text_input("Email address", placeholder="you@example.com", key="al-email",
                              help="Used only for this alert, never shown anywhere.")
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
            "Until then the owner can still create this alert as a GitHub issue:")
    try:  # names only, never values: tells the owner whether the app sees the secret at all
        names = ", ".join(sorted(st.secrets.keys())) or "none"
    except Exception:
        names = "none (no secrets saved)"
    st.caption(f"Secrets this app can see: {names}. The token goes in the app's Streamlit secrets (not GitHub's), as GITHUB = \"github_pat_...\".")
    st.link_button("Create the alert on GitHub", alerts.new_issue_link(leg[0], leg[1], cabin, unit, limit, start, end, trip, nights))
elif go:
    try:
        n, code = store.create(token, leg[0], leg[1], cabin, unit, limit, start, end, email=email, ntfy=topic, trip=trip, nights=nights)
    except store.AlertError as err:
        st.error(str(err))
    else:
        if email:
            proven[("email", email.strip().lower())] = True
            try:  # with SMTP_USER / SMTP_PASSWORD in the app's secrets the confirmation goes now; otherwise the daily run sends it
                alerts.mail(email, f"Confirm your price alert: {tp.city(leg[0])} → {tp.city(leg[1])}",
                            f"Someone (hopefully you) asked the AEGEAN fare tracker to email this address when {what}.\n\n"
                            f"Turn the alert on: {alerts.link('confirm', n, code)}\n\nNot you? Ignore this email and nothing more will be sent.")
                store.mark_confirmation_sent(token, n, alerts.SENT)
                st.success(f"Almost done: we emailed {email}. Open the link in that email to turn on the alert ({what}). "
                           "Nothing else is sent until you do. Not there? Check the spam folder.")
            except Exception:
                st.success(f"Almost done: a confirmation email goes to {email} with tomorrow morning's check. Open its link to turn on "
                           f"the alert ({what}); nothing else is sent until you do.")
        else:
            proven[("ntfy", topic)] = True
            try:  # a first notification now, so the visitor sees the phone setup works
                urllib.request.urlopen(urllib.request.Request(
                    f"https://ntfy.sh/{topic}", method="POST", headers={"Title": "Price alert created", "Tags": "airplane"},
                    data=f"You will be notified here when {what}. Stop it: {alerts.link('stop', n, code)}".encode()), timeout=15).read()
                st.success(f"Done: you will be told when {what}. A first notification was just sent to your phone; the next one comes "
                           "when a fare meets the alert (checked every morning).")
            except Exception:
                st.success(f"Done: you will be told when {what}, in the ntfy app (checked every morning).")
            st.session_state.pop("al-topic", None)  # a new topic for the next alert from this browser
        st.caption(f"Stop it any time with the link in its messages, or here: {alerts.link('stop', n, code)}")

# --- the visitor's own alerts: only for an address or phone this visitor proved theirs (made an alert here, or opened one of its links) -----
if token and proven:
    st.markdown('<p class="group">Your alerts</p>', unsafe_allow_html=True)
    for kind_, contact in list(proven):
        try:
            mine = store.mine(token, kind_, contact)
        except store.AlertError as err:
            st.error(str(err))
            continue
        where = contact if kind_ == "email" else f"your phone (topic {contact})"
        if not mine:
            st.caption(f"No alerts are on for {where}.")
            continue
        for a in mine:
            parsed = alerts.parse(a["body"])
            text = alerts.describe(parsed, when_text(parsed["start"], parsed["end"])) if parsed else a["title"]
            note = f"To {where}." if a["confirmed"] else f"To {where}, once you open the link in the confirmation email."
            c = st.columns([5, 1], vertical_alignment="center")
            c[0].markdown(f"**{text}**  \n{note}")
            if c[1].button("Stop", key=f"al-stop-{a['number']}", width="stretch"):
                try:
                    store.stop(token, a["number"], a["code"])
                except store.AlertError as err:
                    st.error(str(err))
                else:
                    st.rerun()
elif token:
    st.caption("Made alerts before? Open the link in any of their messages: this page then lists them all, each with a Stop button.")
