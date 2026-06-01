"""Unit tests for every rule plus the diff parser and CLI exit code.

These tests are dependency-free (stdlib unittest + pytest discovery).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pr_job_scan.diff import parse_diff
from pr_job_scan.finding import Severity
from pr_job_scan.rules import (
    rule_SEC001, rule_SEC002, rule_SEC003, rule_SEC004,
    rule_REL001, rule_REL002, rule_REL003, rule_REL004, rule_REL005,
    rule_QUA001, rule_QUA002, rule_QUA003, rule_QUA004, rule_QUA005, rule_QUA006,
    run_all,
)
from pr_job_scan.sources import scan_diff_text


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIFF = (REPO_ROOT / "examples" / "sample.diff").read_text()


def _diff(text: str):
    return parse_diff(text)


# ────────────────────────── Diff parser ──────────────────────────


def test_parse_diff_basic():
    text = (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,3 @@\n"
        " a\n"
        "+b\n"
        " c\n"
    )
    files = _diff(text)
    assert len(files) == 1
    assert files[0].path == "foo.py"
    assert len(files[0].added) == 1
    assert files[0].added[0].text == "b"
    assert files[0].added[0].line == 2


def test_parse_diff_new_file():
    text = (
        "diff --git a/new.py b/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+line1\n"
        "+line2\n"
    )
    files = _diff(text)
    assert files[0].is_new is True
    assert [a.line for a in files[0].added] == [1, 2]


# ────────────────────────── SEC001 ──────────────────────────


def test_sec001_flags_logger_exception_in_api():
    text = (
        "diff --git a/api/x.py b/api/x.py\n"
        "--- a/api/x.py\n"
        "+++ b/api/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+    logger.exception('boom')\n"
    )
    findings = rule_SEC001(_diff(text))
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


def test_sec001_ignores_agents_module():
    text = (
        "diff --git a/agents/x.py b/agents/x.py\n"
        "--- a/agents/x.py\n"
        "+++ b/agents/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+    logger.exception('boom')\n"
    )
    assert rule_SEC001(_diff(text)) == []


def test_sec001_respects_explicit_override():
    text = (
        "diff --git a/api/x.py b/api/x.py\n"
        "--- a/api/x.py\n"
        "+++ b/api/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+    logger.exception('boom')  # SEC001-ok: traceback is intentional here\n"
    )
    assert rule_SEC001(_diff(text)) == []


# ────────────────────────── SEC002 ──────────────────────────


def test_sec002_flags_cors_wildcard_with_credentials():
    text = (
        "diff --git a/api/server.py b/api/server.py\n"
        "--- a/api/server.py\n"
        "+++ b/api/server.py\n"
        "@@ -1,1 +1,4 @@\n"
        " pass\n"
        "+    allow_origins=[\"*\"],\n"
        "+    allow_credentials=True,\n"
        "+    allow_methods=[\"*\"],\n"
    )
    findings = rule_SEC002(_diff(text))
    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


def test_sec002_clean_when_no_credentials():
    text = (
        "diff --git a/api/server.py b/api/server.py\n"
        "--- a/api/server.py\n"
        "+++ b/api/server.py\n"
        "@@ -1,1 +1,3 @@\n"
        " pass\n"
        "+    allow_origins=[\"*\"],\n"
        "+    allow_credentials=False,\n"
    )
    assert rule_SEC002(_diff(text)) == []


# ────────────────────────── SEC003 ──────────────────────────


def test_sec003_flags_placeholder_secret():
    text = (
        "diff --git a/config/settings.py b/config/settings.py\n"
        "--- a/config/settings.py\n"
        "+++ b/config/settings.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+    secret_key: str = "change-me-in-production"\n'
    )
    findings = rule_SEC003(_diff(text))
    assert findings and findings[0].severity == Severity.MED


def test_sec003_ignores_test_files():
    text = (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "--- a/tests/test_x.py\n"
        "+++ b/tests/test_x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+    key = "changeme"\n'
    )
    assert rule_SEC003(_diff(text)) == []


# ────────────────────────── SEC004 ──────────────────────────


def test_sec004_flags_verify_false():
    text = (
        "diff --git a/api/x.py b/api/x.py\n"
        "--- a/api/x.py\n"
        "+++ b/api/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+    requests.get(url, verify=False)\n"
    )
    findings = rule_SEC004(_diff(text))
    assert findings and findings[0].severity == Severity.HIGH


# ────────────────────────── REL001 ──────────────────────────


def test_rel001_flags_on_event():
    text = (
        "diff --git a/api/server.py b/api/server.py\n"
        "--- a/api/server.py\n"
        "+++ b/api/server.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+@app.on_event("startup")\n'
    )
    findings = rule_REL001(_diff(text))
    assert findings and findings[0].rule_id == "REL001"


# ────────────────────────── REL002 ──────────────────────────


def test_rel002_flags_error_message_on_resume_builds():
    text = (
        "diff --git a/api/orphan_reaper.py b/api/orphan_reaper.py\n"
        "--- a/api/orphan_reaper.py\n"
        "+++ b/api/orphan_reaper.py\n"
        "@@ -1,1 +1,5 @@\n"
        " pass\n"
        '+    db.table("resume_builds").update({\n'
        '+        "status": "failed",\n'
        '+        "error_message": "stuck",\n'
        '+    }).execute()\n'
    )
    findings = rule_REL002(_diff(text))
    assert findings and findings[0].severity == Severity.HIGH


def test_rel002_ok_with_correct_column():
    text = (
        "diff --git a/api/orphan_reaper.py b/api/orphan_reaper.py\n"
        "--- a/api/orphan_reaper.py\n"
        "+++ b/api/orphan_reaper.py\n"
        "@@ -1,1 +1,5 @@\n"
        " pass\n"
        '+    db.table("resume_builds").update({\n'
        '+        "status": "failed",\n'
        '+        "error": "stuck",\n'
        '+    }).execute()\n'
    )
    assert rule_REL002(_diff(text)) == []


# ────────────────────────── REL003 ──────────────────────────


def test_rel003_flags_raw_router_in_agent():
    text = (
        "diff --git a/agents/g5_graph.py b/agents/g5_graph.py\n"
        "--- a/agents/g5_graph.py\n"
        "+++ b/agents/g5_graph.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+    r = await get_router().ask(provider='anthropic')\n"
    )
    findings = rule_REL003(_diff(text))
    assert findings and findings[0].rule_id == "REL003"


def test_rel003_skips_llm_hardening_module():
    text = (
        "diff --git a/agents/llm_hardening.py b/agents/llm_hardening.py\n"
        "--- a/agents/llm_hardening.py\n"
        "+++ b/agents/llm_hardening.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+    r = await get_router().ask(provider='anthropic')\n"
    )
    assert rule_REL003(_diff(text)) == []


# ────────────────────────── REL004 ──────────────────────────


def test_rel004_flags_workspace_collision():
    text = (
        "diff --git a/api/follow_ups.py b/api/follow_ups.py\n"
        "--- a/api/follow_ups.py\n"
        "+++ b/api/follow_ups.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+router = APIRouter(prefix="/workspace/follow-ups")\n'
    )
    findings = rule_REL004(_diff(text))
    assert findings and findings[0].severity == Severity.HIGH


# ────────────────────────── REL005 ──────────────────────────


def test_rel005_flags_loop_in_background_task():
    text = (
        "diff --git a/api/x.py b/api/x.py\n"
        "--- a/api/x.py\n"
        "+++ b/api/x.py\n"
        "@@ -1,1 +1,5 @@\n"
        " pass\n"
        "+async def _run():\n"
        "+    for item in items:\n"
        "+        await do(item)\n"
        "+background_tasks.add_task(_run)\n"
    )
    findings = rule_REL005(_diff(text))
    assert findings and findings[0].rule_id == "REL005"


# ────────────────────────── QUA001 ──────────────────────────


def test_qua001_flags_mock_in_catch():
    text = (
        "diff --git a/dashboard/src/app/today/page.tsx b/dashboard/src/app/today/page.tsx\n"
        "--- a/dashboard/src/app/today/page.tsx\n"
        "+++ b/dashboard/src/app/today/page.tsx\n"
        "@@ -1,1 +1,4 @@\n"
        " pass\n"
        "+  } catch (err) {\n"
        "+    return { actions: MOCK_TODAY_ACTIONS, isMock: true }\n"
        "+  }\n"
    )
    findings = rule_QUA001(_diff(text))
    assert findings and findings[0].severity == Severity.HIGH


# ────────────────────────── QUA002 ──────────────────────────


def test_qua002_flags_bare_todo():
    text = (
        "diff --git a/agents/x.py b/agents/x.py\n"
        "--- a/agents/x.py\n"
        "+++ b/agents/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+# TODO: switch back later\n"
    )
    findings = rule_QUA002(_diff(text))
    assert findings, "expected QUA002 to fire on bare TODO"


def test_qua002_accepts_owner_and_date():
    text = (
        "diff --git a/agents/x.py b/agents/x.py\n"
        "--- a/agents/x.py\n"
        "+++ b/agents/x.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        "+# TODO(rizwan): 2026-06-01 — refactor when feature flag lands\n"
    )
    assert rule_QUA002(_diff(text)) == []


# ────────────────────────── QUA003 ──────────────────────────


def test_qua003_flags_finder_duplicate():
    text = (
        "diff --git a/dashboard/src/lib/network 2.ts b/dashboard/src/lib/network 2.ts\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/dashboard/src/lib/network 2.ts\n"
        "@@ -0,0 +1,1 @@\n"
        "+export const x = 1\n"
    )
    findings = rule_QUA003(_diff(text))
    assert findings and findings[0].rule_id == "QUA003"


# ────────────────────────── QUA004 ──────────────────────────


def test_qua004_flags_endpoint_without_test():
    text = (
        "diff --git a/api/server.py b/api/server.py\n"
        "--- a/api/server.py\n"
        "+++ b/api/server.py\n"
        "@@ -1,1 +1,4 @@\n"
        " pass\n"
        '+@app.post("/applications/{id}/retire")\n'
        "+async def retire(id: int):\n"
        "+    pass\n"
    )
    findings = rule_QUA004(_diff(text))
    assert findings and findings[0].severity == Severity.MED


def test_qua004_silent_when_test_in_diff():
    text = (
        "diff --git a/api/server.py b/api/server.py\n"
        "--- a/api/server.py\n"
        "+++ b/api/server.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+@app.post("/applications/{id}/retire")\n'
        "diff --git a/tests/test_server.py b/tests/test_server.py\n"
        "--- a/tests/test_server.py\n"
        "+++ b/tests/test_server.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+def test_retire(): client.post("/applications/1/retire")\n'
    )
    assert rule_QUA004(_diff(text)) == []


# ────────────────────────── QUA005 ──────────────────────────


def test_qua005_flags_invalid_model_id():
    text = (
        "diff --git a/agents/g5_graph.py b/agents/g5_graph.py\n"
        "--- a/agents/g5_graph.py\n"
        "+++ b/agents/g5_graph.py\n"
        "@@ -1,1 +1,2 @@\n"
        " pass\n"
        '+    model = "claude-opus-4-7"\n'
    )
    findings = rule_QUA005(_diff(text))
    assert findings and findings[0].rule_id == "QUA005"


# ────────────────────────── QUA006 ──────────────────────────


def test_qua006_flags_basemodel_without_extra_forbid():
    text = (
        "diff --git a/api/network.py b/api/network.py\n"
        "--- a/api/network.py\n"
        "+++ b/api/network.py\n"
        "@@ -1,1 +1,4 @@\n"
        " pass\n"
        "+class DraftIntroBody(BaseModel):\n"
        "+    contact_id: int\n"
        "+    angle: str\n"
    )
    findings = rule_QUA006(_diff(text))
    assert findings and findings[0].rule_id == "QUA006"


def test_qua006_quiet_when_extra_forbid_set():
    text = (
        "diff --git a/api/network.py b/api/network.py\n"
        "--- a/api/network.py\n"
        "+++ b/api/network.py\n"
        "@@ -1,1 +1,5 @@\n"
        " pass\n"
        "+class DraftIntroBody(BaseModel):\n"
        '+    model_config = {"extra": "forbid"}\n'
        "+    contact_id: int\n"
        "+    angle: str\n"
    )
    assert rule_QUA006(_diff(text)) == []


# ────────────────────────── End-to-end ──────────────────────────


def test_sample_diff_fires_each_rule_class():
    result = scan_diff_text(SAMPLE_DIFF)
    rule_ids = {f.rule_id for f in result.findings}
    # The sample diff is constructed to exercise every rule
    for required in (
        "SEC001", "SEC002", "SEC003",
        "REL001", "REL002", "REL003", "REL004",
        "QUA001", "QUA003", "QUA004", "QUA005", "QUA006",
    ):
        assert required in rule_ids, f"Sample diff missed rule {required}: got {rule_ids}"


def test_run_all_select_filter():
    findings = run_all(parse_diff(SAMPLE_DIFF), select={"SEC001"})
    assert findings, "expected at least one SEC001 finding in sample"
    assert all(f.rule_id == "SEC001" for f in findings)


def test_scan_result_max_severity():
    result = scan_diff_text(SAMPLE_DIFF)
    assert result.max_severity() == Severity.HIGH


# ────────────────────────── CLI exit code ──────────────────────────


def test_cli_exit_clean(tmp_path, capsys):
    from pr_job_scan.cli import main
    empty = tmp_path / "empty.diff"
    empty.write_text("")
    rc = main(["scan-diff", str(empty), "--no-color"])
    assert rc == 0


def test_cli_exit_fails_on_high(tmp_path):
    from pr_job_scan.cli import main
    sample = tmp_path / "s.diff"
    sample.write_text(SAMPLE_DIFF)
    rc = main(["scan-diff", str(sample), "--fail-on", "high", "--no-color"])
    assert rc == 1


def test_cli_exit_ok_when_fail_on_none(tmp_path):
    from pr_job_scan.cli import main
    sample = tmp_path / "s.diff"
    sample.write_text(SAMPLE_DIFF)
    rc = main(["scan-diff", str(sample), "--fail-on", "none", "--no-color"])
    assert rc == 0
