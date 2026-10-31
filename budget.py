"""The GitHub Actions budget guard: the free plan of a private repository has 2,000 minutes a month, and the app's fetches and its
morning run spend them. Past LIMIT (85%) everything that starts a GitHub run stops until the month resets, so the account never pays.

    python budget.py        # prints the usage; writes stop=true to $GITHUB_OUTPUT past the limit (the workflows read it); exit 0 always

Reads BILLING_TOKEN (a fine-grained token with the account permission Plan: read, or a classic one with the user scope) from the
environment, or any GitHub token that can read the billing API. Without one, or without the permission, nothing is known and nothing
stops: the usage line says so. Standard library only: it runs before anything is installed."""
import json
import os
import sys
import urllib.error
import urllib.request

OWNER = "Am015-dev"
LIMIT = 0.85  # of the included minutes


def usage(token, owner=OWNER):
    """dict(used, included, fraction) from GitHub's billing API, or None when the token cannot read it (403/404) or there is none."""
    if not token:
        return None
    req = urllib.request.Request(f"https://api.github.com/users/{owner}/settings/billing/actions",
                                 headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28",
                                          "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
    except (urllib.error.URLError, ValueError, OSError):
        return None
    used, included = float(d.get("total_minutes_used", 0)), float(d.get("included_minutes") or 0)
    return dict(used=used, included=included, fraction=used / included if included else 0.0)


def line(u):
    """One sentence a person can read: "GitHub Actions: 612 of 2,000 free minutes used this month (31%)."."""
    if not u:
        return "GitHub Actions usage unknown: no token with the Plan: read permission (see the README, 'GitHub minutes')."
    pct = round(u["fraction"] * 100)
    text = f"GitHub Actions: {u['used']:,.0f} of {u['included']:,.0f} free minutes used this month ({pct}%)."
    return text + (f" Past {LIMIT:.0%}: searches and the morning run are paused until the month resets." if u["fraction"] >= LIMIT else "")


def main():
    u = usage(os.environ.get("BILLING_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    stop = bool(u and u["fraction"] >= LIMIT)
    print(line(u))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"stop={'true' if stop else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
