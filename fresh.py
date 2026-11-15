"""Streamlit Cloud keeps the app's modules loaded across a redeploy, so a page can run new code against an old module and fail
(ImportError on 15 Nov: views/hotel.py asked xotelo for functions the old copy lacked). Each run, app.py calls reload_changed():
when any of the app's own .py files changed on disk since the last run, every loaded app module is reloaded. Standard library only."""
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
_seen = {}  # module file -> mtime at the last run; lives in this module, which never reloads itself


def _ours():
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if f.startswith(ROOT) and f.endswith(".py") and name not in ("__main__", __name__) and os.sep + "views" + os.sep not in f:
            yield name, m, f


def reload_changed():
    """Reload every loaded app module when any of them changed on disk; returns the names reloaded (empty on most runs)."""
    mods = list(_ours())
    changed = False
    for _, _, f in mods:
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        changed |= f in _seen and _seen[f] != mt
        _seen[f] = mt
    if not changed:
        return []
    done = []
    for _ in range(2):  # twice, so a module that does "from other import x" picks up the reloaded other
        for name, m, _ in mods:
            try:
                importlib.reload(m)
                done.append(name)
            except Exception:  # a module that cannot reload keeps its old copy; the page then reports any real error
                pass
    return sorted(set(done))
