import importlib
import os
import sys
import time


def test_a_changed_module_is_reloaded_before_the_pages_use_it(tmp_path, monkeypatch):
    import fresh
    mod = tmp_path / "flycheap_tmpmod.py"
    mod.write_text("A = 1\n")
    monkeypatch.setattr(fresh, "ROOT", str(tmp_path))
    monkeypatch.syspath_prepend(str(tmp_path))
    m = importlib.import_module("flycheap_tmpmod")
    assert fresh.reload_changed() == []  # first sight: nothing to compare with
    mod.write_text("A = 1\nB = 2\n")  # a redeploy adds a name
    os.utime(mod, (time.time() + 5, time.time() + 5))
    assert "flycheap_tmpmod" in fresh.reload_changed()
    assert sys.modules["flycheap_tmpmod"].B == 2 and fresh.reload_changed() == []
