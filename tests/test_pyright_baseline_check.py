"""tools/pyright_baseline_check.py: CI fails on NEW pyright errors only."""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pyright_baseline_check", REPO / "tools" / "pyright_baseline_check.py")
assert _spec is not None and _spec.loader is not None
pbc = importlib.util.module_from_spec(_spec)
sys.modules["pyright_baseline_check"] = pbc
_spec.loader.exec_module(pbc)

ROOT = REPO


def diag(file, rule, message, line=10, severity="error"):
    return {
        "file": str(ROOT / file),
        "severity": severity,
        "message": message,
        "rule": rule,
        "range": {"start": {"line": line, "character": 0}, "end": {"line": line, "character": 3}},
    }


def report(*diags):
    return {"generalDiagnostics": list(diags), "summary": {}}


A = ("mc/a.py", "reportOptionalMemberAccess", '"get" is not a known attribute of "None"')


def write_report(tmp_path, *diags):
    p = tmp_path / "report.json"
    p.write_text(json.dumps(report(*diags)), encoding="utf-8")
    return p


def run(tmp_path, capsys, *diags, extra=()):
    rc = pbc.main(["--report", str(write_report(tmp_path, *diags)), "--baseline", str(tmp_path / "base.json"),
                   "--root", str(ROOT), *extra])
    return rc, capsys.readouterr()


def test_key_ignores_line_numbers_and_makes_path_relative():
    c1, l1 = pbc.collect(report(diag(A[0], A[1], A[2], line=5)), ROOT)
    c2, l2 = pbc.collect(report(diag(A[0], A[1], A[2], line=500)), ROOT)
    assert c1 == c2 == Counter({A: 1})
    assert l1[A] == [6] and l2[A] == [501]


def test_headline_drops_version_dependent_detail_lines():
    msg = 'Argument of type "int" cannot be assigned\n  Type "int" is not assignable'
    c, _ = pbc.collect(report(diag("mc/a.py", "reportArgumentType", msg)), ROOT)
    assert list(c) == [("mc/a.py", "reportArgumentType", 'Argument of type "int" cannot be assigned')]


def test_warnings_and_information_are_not_counted():
    c, _ = pbc.collect(report(diag(*A, severity="warning"), diag(*A, severity="information")), ROOT)
    assert not c


def test_baselined_error_passes_even_when_it_moves(tmp_path, capsys):
    assert run(tmp_path, capsys, diag(*A, line=1), extra=["--update"])[0] == 0
    rc, out = run(tmp_path, capsys, diag(*A, line=400))
    assert rc == 0 and "none new" in out.out and "NEW" not in out.out


def test_new_error_in_other_file_fails(tmp_path, capsys):
    run(tmp_path, capsys, diag(*A), extra=["--update"])
    rc, out = run(tmp_path, capsys, diag(*A), diag("mc/b.py", A[1], A[2], line=7))
    assert rc == 1
    assert "NEW    mc/b.py" in out.out and "lines [8]" in out.out


def test_extra_duplicate_of_a_baselined_error_fails(tmp_path, capsys):
    run(tmp_path, capsys, diag(*A), extra=["--update"])
    rc, out = run(tmp_path, capsys, diag(*A, line=3), diag(*A, line=30))
    assert rc == 1 and "(baseline 1, now 2;" in out.out


def test_new_message_in_same_file_and_rule_fails(tmp_path, capsys):
    run(tmp_path, capsys, diag(*A), extra=["--update"])
    rc, _ = run(tmp_path, capsys, diag(*A), diag(A[0], A[1], '"keys" is not a known attribute of "None"'))
    assert rc == 1


def test_fixed_entries_are_reported_but_do_not_fail(tmp_path, capsys):
    run(tmp_path, capsys, diag(*A), diag("mc/b.py", "reportCallIssue", "Expected 1 positional"), extra=["--update"])
    rc, out = run(tmp_path, capsys, diag(*A))
    assert rc == 0
    assert "FIXED  mc/b.py" in out.out and "ratchet" in out.out


def test_update_ratchets_baseline_down(tmp_path, capsys):
    run(tmp_path, capsys, diag(*A), diag("mc/b.py", "reportCallIssue", "x"), extra=["--update"])
    run(tmp_path, capsys, diag(*A), extra=["--update"])
    assert pbc.load_baseline(tmp_path / "base.json") == Counter({A: 1})
    assert "FIXED" not in run(tmp_path, capsys, diag(*A))[1].out


def test_baseline_file_is_deterministic_and_lf_only(tmp_path, capsys):
    run(tmp_path, capsys, diag("mc/z.py", "r", "m"), diag(*A), extra=["--update"])
    first = (tmp_path / "base.json").read_bytes()
    run(tmp_path, capsys, diag(*A), diag("mc/z.py", "r", "m"), extra=["--update"])
    assert (tmp_path / "base.json").read_bytes() == first
    assert b"\r" not in first and first.endswith(b"\n")


def test_unusable_report_exits_2_not_pass(tmp_path):
    (tmp_path / "bad.json").write_text("pyright crashed", encoding="utf-8")
    rc = pbc.main(["--report", str(tmp_path / "bad.json"), "--baseline", str(tmp_path / "base.json")])
    assert rc == 2
    (tmp_path / "nokey.json").write_text("{}", encoding="utf-8")
    assert pbc.main(["--report", str(tmp_path / "nokey.json"), "--baseline", str(tmp_path / "base.json")]) == 2


def test_missing_baseline_exits_2(tmp_path, capsys):
    rc, _ = run(tmp_path, capsys, diag(*A))
    assert rc == 2


def test_banner_before_json_is_tolerated():
    text = "WARNING: new pyright available\n" + json.dumps(report(diag(*A)))
    assert len(pbc.parse_report(text)["generalDiagnostics"]) == 1


def test_committed_baseline_is_well_formed():
    base = pbc.load_baseline(REPO / "tools" / "pyright_baseline.json")
    assert base and all(n >= 1 for n in base.values())
    for f, rule, msg in base:
        assert not Path(f).is_absolute() and "\\" not in f, f
        assert rule and msg and "\n" not in msg


def test_run_pyright_pins_platform_and_ignores_pyright_exit_code(monkeypatch):
    seen = {}

    class P:
        returncode = 1  # pyright exits 1 whenever it finds errors
        stdout = json.dumps(report(diag(*A)))
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return P()

    monkeypatch.setattr(pbc.subprocess, "run", fake_run)
    out = pbc.run_pyright(ROOT)
    assert len(out["generalDiagnostics"]) == 1
    i = seen["cmd"].index("--pythonplatform")
    assert seen["cmd"][i + 1] == "Linux" and "--outputjson" in seen["cmd"]


def test_run_pyright_with_no_json_raises(monkeypatch):
    class P:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(pbc.subprocess, "run", lambda cmd, **kw: P())
    try:
        pbc.run_pyright(ROOT)
    except RuntimeError as e:
        assert "boom" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
