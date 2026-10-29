"""Background fetch jobs: they survive reruns and closed phones; the page polls progress, shows failures and can stop a job."""
import threading
import time

import streamlit as st

JOBS = {}  # key -> job dict
_lock = threading.Lock()
STALL_SECONDS = 45


def start_job(key, total, work, empty_note="{n} lookups found nothing to match."):
    """Run work(job) in a thread unless the same key is already running."""
    with _lock:
        if JOBS.get(key, {}).get("running"):
            return
        j = JOBS[key] = dict(done=0, total=total, ok=0, failed=0, empty=0, running=True, error=None, note=empty_note,
                             errors=[], cancel=False, started=time.time(), beat=time.time(), ended=None)

    def run():
        try:
            work(j)
        except Exception as e:  # never die silently: the page shows this
            j["error"] = f"{type(e).__name__}: {e}"
        finally:
            j["running"], j["ended"] = False, time.time()

    threading.Thread(target=run, daemon=True).start()


def record(j, status, err=None):
    """Count one finished lookup (status ok / none / error) and remember up to 5 distinct error messages."""
    j["done"] += 1
    j["beat"] = time.time()
    if status == "ok":
        j["ok"] += 1
    elif status == "none":
        j["empty"] += 1
    else:
        j["failed"] += 1
        if err and err not in j["errors"] and len(j["errors"]) < 5:
            j["errors"].append(err)


@st.fragment(run_every=2)
def _live(key):
    j = JOBS[key]
    if not j["running"]:
        st.rerun()  # full rerun so cards and charts pick up the finished data
    st.progress(min(1.0, j["done"] / max(1, j["total"])),
                text=f"Checked {j['done']} of {j['total']} · {j['ok']} priced · {j['failed']} failed. "
                     "It keeps running if you close this page.")
    if time.time() - j["beat"] > STALL_SECONDS:
        st.warning(f"No progress for {int(time.time() - j['beat'])} seconds. Google may be slow or blocking requests. "
                   "You can stop and try again later.")
    if st.button("Stop", key=f"stop-{key}"):
        j["cancel"] = True


def track(key):
    """Show this key's job, or any job still running (a reload resets the dates, so the key may differ)."""
    key = key if key in JOBS else next((k for k, v in JOBS.items() if v["running"]), key)
    j = JOBS.get(key)
    if not j:
        return
    if j["running"]:
        _live(key)
        return
    took = int((j["ended"] or time.time()) - j["started"])
    if j["error"]:
        st.error(f"The update stopped because of an error: {j['error']}. Results saved so far are shown below.")
    elif j["cancel"]:
        st.info(f"Stopped after {j['done']} of {j['total']} lookups. Press Update prices to continue where it left off.")
    elif any("429" in e for e in j["errors"]):
        st.error(f"Google is rate-limiting this server (HTTP 429, too many requests), so the update stopped after {j['ok']} priced and "
                 f"{j['failed']} failed. This is Google's limit, not a bug in your search. Results saved so far are shown. "
                 "Wait about an hour and press Update prices to continue where it left off, or run the app on your own computer.")
    elif j["failed"] and not j["ok"] and not j["empty"]:
        st.error(f"Nothing could be fetched: all {j['failed']} lookups failed in {took} s. Google or the network is not answering. "
                 "Open the Health page to see which source is down, then press Update prices to try again.")
    elif j["failed"]:
        st.warning(f"Finished in {took} s with problems: {j['ok']} priced, {j['empty']} with no flight, {j['failed']} failed. "
                   "Press Update prices again to retry just the failed ones.")
    else:
        st.success(f"Finished in {took} s: {j['ok']} priced, {j['empty']} with no flight.")
    if j["empty"]:
        st.caption(j["note"].format(n=j["empty"]))
    if j["errors"]:
        with st.expander("Technical details"):
            st.code("\n".join(j["errors"]))


if __name__ == "__main__":  # self-check
    def work(j):
        record(j, "ok")
        record(j, "error", "Boom")
        record(j, "none")
        time.sleep(0.2)
    start_job("k", 3, work)
    assert JOBS["k"]["running"]
    start_job("k", 9, lambda j: None)  # ignored while running
    assert JOBS["k"]["total"] == 3
    time.sleep(0.5)
    j = JOBS["k"]
    assert not j["running"] and (j["ok"], j["failed"], j["empty"], j["errors"]) == (1, 1, 1, ["Boom"])
    start_job("bad", 1, lambda j: 1 / 0)
    time.sleep(0.2)
    assert "ZeroDivisionError" in JOBS["bad"]["error"] and not JOBS["bad"]["running"]
    print("ok")
