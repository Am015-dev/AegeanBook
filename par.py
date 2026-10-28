"""Run lookups in a few threads, retry, never raise. Measured: 1 worker 0.75 s/lookup and no failures, 4 workers ~1.5x faster but ~1 in 16 fail, 8 fail more. 3 + retries is the compromise."""
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


def parallel(items, fn, workers=3, retries=2):
    """Yield (item, result, error) as lookups finish, in completion order. error is None on success."""
    def run(item):
        time.sleep(random.random() * 0.3)  # stagger the burst
        err = None
        for _ in range(retries + 1):
            try:
                return item, fn(item), None
            except Exception as e:
                err = repr(e)
                time.sleep(0.5 + random.random())
        return item, None, err

    ex = ThreadPoolExecutor(workers)
    try:
        for f in as_completed([ex.submit(run, i) for i in items]):
            yield f.result()
    finally:  # a Streamlit rerun closes the generator early: don't wait for the queue
        ex.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":  # self-check
    calls = {"n": 0}

    def flaky(x):
        if x == 1 and calls["n"] == 0:
            calls["n"] += 1
            raise ValueError("once")
        if x == 2:
            raise ValueError("always")
        return x * 10

    got = {i: (r, e) for i, r, e in parallel([0, 1, 2, 3], flaky)}
    assert got[0] == (0, None) and got[1] == (10, None) and got[3] == (30, None)
    assert got[2][0] is None and "always" in got[2][1]
    print("ok")
