"""Miles+Bonus award helper, runs on YOUR machine with YOUR account (may breach AEGEAN's terms; keep it slow).

  python award_collector.py login    # log in once in the opened browser; profile is kept in .aegean_profile/
  python award_collector.py record   # do ONE manual award search; JSON responses are saved to award_recording.json

No password is stored by this script. award_recording.json may contain personal data (name, miles balance):
review it before sharing. ponytail: `fetch` (automatic daily search) comes after we see a real recording.
"""
import argparse
import json
import os

from playwright.sync_api import sync_playwright

PROFILE, OUT = ".aegean_profile", "award_recording.json"


def run(mode, host="aegeanair.com", url="https://en.aegeanair.com/", headless=False, wait=input):
    seen = []

    def on_response(r):
        if mode == "record" and host in r.url and "json" in r.headers.get("content-type", ""):
            try:
                seen.append({"method": r.request.method, "url": r.url, "status": r.status,
                             "post": r.request.post_data, "body": r.json()})
            except Exception:
                pass  # body gone (redirect/stream); skip

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE, headless=headless, executable_path=os.environ.get("CHROME_PATH"))
        ctx.on("response", on_response)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        wait("Log in in the browser, then press Enter here..." if mode == "login"
             else "Run one Business award search for one date, wait for results, then press Enter here...")
        ctx.close()
    if mode == "record":
        json.dump(seen, open(OUT, "w"), indent=1, ensure_ascii=False)
    return seen


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("mode", choices=["login", "record"])
    run(a.parse_args().mode)
