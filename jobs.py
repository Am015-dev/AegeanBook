"""Background fetch jobs: they survive reruns and closed phones, and the page polls their progress."""
import threading

import streamlit as st

JOBS = {}  # key -> {done, total, failed, empty, running, error}
_lock = threading.Lock()


def start_job(key, total, work):
    """Run work(job) in a thread unless the same key is already running. work updates job done/failed/empty."""
    with _lock:
        if JOBS.get(key, {}).get("running"):
            return
        j = JOBS[key] = dict(done=0, total=total, failed=0, empty=0, running=True, error=None)

    def run():
        try:
            work(j)
        except Exception as e:
            j["error"] = repr(e)
        finally:
            j["running"] = False

    threading.Thread(target=run, daemon=True).start()


@st.fragment(run_every=2)
def _live(key):
    j = JOBS[key]
    if not j["running"]:
        st.rerun()  # full rerun so charts and cards pick up the finished data
    st.progress(min(1.0, j["done"] / max(1, j["total"])),
                text=f"Checked {j['done']} of {j['total']}. It keeps running if you close this page.")


def track(key):
    """Show this key's job, or any job still running (a reload resets the dates, so the key may differ)."""
    key = key if key in JOBS else next((k for k, v in JOBS.items() if v["running"]), key)
    j = JOBS.get(key)
    if not j:
        return
    if j["running"]:
        _live(key)
        return
    if j["error"]:
        st.error(f"The update stopped early: {j['error'][:120]}")
    if j["failed"]:
        st.warning(f"{j['failed']} lookups failed because Google did not answer. Press Update prices again to retry just those.")
    if j["empty"]:
        st.caption(f"{j['empty']} lookups found nothing to match (no AEGEAN flight, or no hotel with that name).")


if __name__ == "__main__":  # self-check
    import time
    start_job("k", 2, lambda j: (time.sleep(0.2), j.update(done=2, failed=1)))
    assert JOBS["k"]["running"]
    start_job("k", 9, lambda j: None)  # ignored while running
    assert JOBS["k"]["total"] == 2
    time.sleep(0.5)
    assert not JOBS["k"]["running"] and JOBS["k"]["failed"] == 1
    print("ok")
