"""budget.py: the 85% guard on the month's free GitHub Actions minutes."""
import io
import json
import os

import budget

HERE = os.path.dirname(os.path.abspath(__file__))


def test_usage_reads_the_billing_api_and_the_line_says_it_plainly(monkeypatch, tmp_path):
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass
    monkeypatch.setattr(budget.urllib.request, "urlopen", lambda req, timeout=15: R(json.dumps({"total_minutes_used": 1720, "included_minutes": 2000}).encode()))
    u = budget.usage("tok")
    assert u == dict(used=1720.0, included=2000.0, fraction=0.86)
    assert budget.line(u) == "GitHub Actions: 1,720 of 2,000 free minutes used this month (86%). Past 85%: searches and the morning run are paused until the month resets."
    assert budget.line(dict(used=612, included=2000, fraction=0.306)) == "GitHub Actions: 612 of 2,000 free minutes used this month (31%)."
    assert budget.usage(None) is None and budget.line(None).startswith("GitHub Actions usage unknown")
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out)); monkeypatch.setenv("BILLING_TOKEN", "tok")
    assert budget.main() == 0 and out.read_text() == "stop=true\n"  # the workflows read this and do nothing


def test_the_workflows_check_the_budget_first():
    daily = open(os.path.join(HERE, ".github", "workflows", "daily.yml")).read()
    fetch = open(os.path.join(HERE, ".github", "workflows", "fetch-route.yml")).read()
    assert "run: python3 budget.py" in daily and "if: steps.budget.outputs.stop != 'true'" in daily and "needs.collect.outputs.stop != 'true'" in daily
    assert "run: python3 budget.py" in fetch and "if: needs.plan.outputs.stop != 'true'" in fetch
    assert daily.index("id: budget") < daily.index("python collect_daily.py") and fetch.index("id: budget") < fetch.index("id: split")
