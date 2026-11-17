"""MC-1179 TL22: a daily build of the month still in progress is not a month-end pin."""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "addons-pin.py"
_spec = importlib.util.spec_from_file_location("addons_pin_under_test", _SCRIPT)
pin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pin)

TAGS = ["autobuild-2026-10-31-13-00", "autobuild-2026-10-30-13-00", "autobuild-2026-11-16-13-00"]
TODAY = datetime.date(2026, 11, 17)


def test_last_build_of_a_finished_month_passes():
    assert pin.month_end_problem("autobuild-2026-10-31-13-00", TAGS, TODAY) is None


def test_non_last_build_of_a_finished_month_is_refused():
    assert "not the last build" in pin.month_end_problem("autobuild-2026-10-30-13-00", TAGS, TODAY)


def test_todays_daily_build_of_the_current_month_is_refused():
    problem = pin.month_end_problem("autobuild-2026-11-16-13-00", TAGS, TODAY)
    assert problem and "not over yet" in problem


def test_non_autobuild_tag_is_refused():
    assert "not an autobuild" in pin.month_end_problem("v1.2", TAGS, TODAY)
