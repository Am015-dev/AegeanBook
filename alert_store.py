"""Price alerts for every visitor, no account needed: the Alerts page saves an alert as an issue in this (private) repository through
GitHub's API, with the visitor's email or phone (ntfy) address in it, and the daily run (alerts.py) delivers it.

Needs GITHUB_TOKEN in the app's Streamlit secrets: a fine-grained token for this repository only, with "Issues: read and write".
Each alert carries a random code; the links in its emails and notifications (confirm, stop) must quote it, so nobody can stop or
confirm someone else's alert. An email alert sends nothing but one confirmation email until its link is opened, so the app cannot be
used to mail strangers. A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import json
import re
import secrets
import urllib.error
import urllib.request

import alerts

VERSION = 1  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
REPO = "Am015-dev/AegeanBook"
LABEL, CONFIRMED = "price-alert", "confirmed"
PER_CONTACT, TOTAL = 5, 300  # open alerts per email or phone address, and in all: a public form must not be able to flood the repository
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class AlertError(Exception):
    """A plain message for the visitor."""


def _api(token, method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r) if r.status != 204 else None
    except urllib.error.HTTPError as err:
        raise AlertError(f"GitHub refused the request (HTTP {err.code}). The app's GITHUB_TOKEN may be missing a permission.") from None


def topic():
    """A fresh, unguessable ntfy topic: only someone who knows it can read the notifications."""
    return "aegean-" + secrets.token_urlsafe(12).replace("_", "x").replace("-", "y").lower()


def _field(body, key):
    m = re.search(rf"^{key}:\s*(\S+)\s*$", body or "", re.M)
    return m[1] if m else None


def open_alerts(token):
    out, page = [], 1
    while True:
        batch = _api(token, "GET", f"/issues?labels={LABEL}&state=open&per_page=100&page={page}")
        out += [i for i in batch if "pull_request" not in i]
        if len(batch) < 100:
            return out
        page += 1


def create(token, orig, dest, cabin, unit, limit, start="", end="", email="", ntfy="", trip="oneway", nights=(2, 7)):
    """Save an alert; returns (issue number, code). trip "return" watches the return total for `nights` (shortest, longest), "oneway" the
    one-way fare. Raises AlertError with a message for the visitor."""
    email, ntfy = email.strip().lower(), ntfy.strip()
    if email and not EMAIL.match(email):
        raise AlertError("That email address does not look right.")
    if not email and not ntfy:
        raise AlertError("Choose where to send the alert: an email address or your phone.")
    existing = open_alerts(token)
    if len(existing) >= TOTAL:
        raise AlertError("The app has as many alerts as it can check right now. Please try again later.")
    contact = ("email", email) if email else ("ntfy", ntfy)
    if sum(_field(i["body"], contact[0]) == contact[1] for i in existing) >= PER_CONTACT:
        raise AlertError(f"You already have {PER_CONTACT} alerts. Stop one (the link is in its messages) to add another.")
    code = secrets.token_urlsafe(16)
    body = "\n".join(alerts.body_lines(orig, dest, cabin, unit, limit, trip, nights, start, end)
                     + [f"{contact[0]}: {contact[1]}", f"code: {code}", "",
                        "Created on the app's Alerts page. The daily run sends it; the code lets the visitor's own links confirm or stop it."])
    issue = _api(token, "POST", "/issues", {"title": f"{alerts.title(orig, dest, cabin, unit, limit, trip)} ({contact[0]})", "body": body,
                                            "labels": [LABEL] + ([CONFIRMED] if contact[0] == "ntfy" else [])})
    return issue["number"], code


def mine(token, kind, contact):
    """The open alerts of one visitor, newest first: [dict(number, title, body, code, confirmed)] whose body says `kind: contact`
    (kind "email" or "ntfy"). The page shows them, with a Stop button each, only to a visitor who proved the contact is theirs (made an
    alert for it in this browser, or opened the confirm or stop link of one of its alerts): the list carries the codes."""
    out = []
    for i in sorted(open_alerts(token), key=lambda i: -i["number"]):
        if _field(i["body"], kind) == contact:
            out.append(dict(number=i["number"], title=i["title"].removeprefix("Alert: "), body=i["body"], code=_field(i["body"], "code") or "",
                            confirmed=CONFIRMED in [l["name"] for l in i.get("labels", [])]))
    return out


def owner(token, number, code):
    """("email", address) or ("ntfy", topic) of alert `number`, for a visitor who holds its code; None when the link is not valid."""
    try:
        issue = _checked(token, number, code)
    except AlertError:
        return None
    for kind in ("email", "ntfy"):
        if _field(issue["body"], kind):
            return kind, _field(issue["body"], kind)
    return None


def _checked(token, number, code):
    issue = _api(token, "GET", f"/issues/{int(number)}")
    if LABEL not in [l["name"] for l in issue.get("labels", [])] or not code or _field(issue["body"], "code") != code:
        raise AlertError("This link is not valid any more.")
    return issue


def confirm(token, number, code):
    """Turn on an email alert from the link in its confirmation email."""
    issue = _checked(token, number, code)
    if issue["state"] != "open":
        raise AlertError("This alert was stopped.")
    _api(token, "POST", f"/issues/{int(number)}/labels", {"labels": [CONFIRMED]})
    return issue["title"].removeprefix("Alert: ")


def mark_confirmation_sent(token, number, marker):
    """Note in the alert that its confirmation email went out, so the daily run does not send a second one."""
    issue = _api(token, "GET", f"/issues/{int(number)}")
    _api(token, "PATCH", f"/issues/{int(number)}", {"body": issue["body"] + "\n" + marker})


def stop(token, number, code):
    """Stop an alert from the link in any of its messages."""
    issue = _checked(token, number, code)
    _api(token, "PATCH", f"/issues/{int(number)}", {"state": "closed", "state_reason": "completed"})
    return issue["title"].removeprefix("Alert: ")
