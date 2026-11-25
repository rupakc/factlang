"""The release gate's decision logic, against fake ``gh`` JSON (spec 3.5 C1)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "verify_tag_ci", Path(__file__).resolve().parents[2] / "scripts" / "verify_tag_ci.py"
)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

TAG, SHA = "v0.1.3", "abc123"


def run(**kw: Any) -> dict[str, Any]:
    base = {
        "databaseId": 10,
        "event": "push",
        "headBranch": TAG,
        "headSha": SHA,
        "status": "completed",
        "conclusion": "success",
    }
    return {**base, **kw}


def legs(n: int = 12, bad: dict[int, str] | None = None) -> list[dict[str, Any]]:
    jobs = [{"name": "lint", "conclusion": "success"}]
    for i in range(n):
        jobs.append(
            {"name": f"test-full (3.1{i % 4}, os-{i})", "conclusion": (bad or {}).get(i, "success")}
        )
    return jobs


class FakeGh:
    """Serves ``run list`` from a queue of snapshots and ``run view`` with fixed jobs."""

    def __init__(self, snapshots: list[list[dict[str, Any]]], jobs: list[dict[str, Any]]):
        self.snapshots = snapshots
        self.jobs = jobs
        self.lists = 0

    def __call__(self, args: Sequence[str]) -> str:
        if args[:2] == ["run", "list"]:
            snap = self.snapshots[min(self.lists, len(self.snapshots) - 1)]
            self.lists += 1
            return json.dumps(snap)
        assert args[:2] == ["run", "view"]
        return json.dumps({"jobs": self.jobs})


def verify(gh: FakeGh, **kw: Any) -> tuple[bool, str]:
    clock = iter(range(0, 10**6, 30))
    return gate.verify(
        "o/r", TAG, SHA, gh=gh, sleep=lambda s: None, clock=lambda: float(next(clock)), **kw
    )


def test_green_tag_run_with_every_leg_green_passes() -> None:
    ok, why = verify(FakeGh([[run()]], legs()))
    assert ok, why
    assert "12 test-full legs" in why


def test_one_failed_leg_fails_even_if_the_run_is_green() -> None:
    # negative control: fail-fast false + a leg that did not succeed
    ok, why = verify(FakeGh([[run()]], legs(bad={7: "cancelled"})))
    assert not ok and "cancelled" in why


def test_run_without_test_full_legs_fails() -> None:
    # a branch-style run (test-full skipped) must not release
    ok, why = verify(FakeGh([[run()]], [{"name": "lint", "conclusion": "success"}]))
    assert not ok and "no test-full" in why


def test_failed_run_fails_at_once() -> None:
    ok, why = verify(FakeGh([[run(conclusion="failure")]], legs()))
    assert not ok and "failure" in why


@pytest.mark.parametrize(
    "other",
    [
        run(event="schedule"),
        run(event="workflow_dispatch"),
        run(headBranch="main"),
        run(headSha="deadbeef"),
        run(headBranch="v0.1.2"),
    ],
)
def test_no_other_run_may_substitute(other: dict[str, Any]) -> None:
    assert gate.pick_run([other], TAG, SHA) is None
    ok, why = verify(FakeGh([[other]], legs()), budget_s=120, poll_s=30)
    assert not ok and "timed out" in why


def test_waits_through_queueing_and_progress() -> None:
    snaps = [[], [run(status="queued", conclusion=None)], [run(status="in_progress")], [run()]]
    gh = FakeGh(snaps, legs())
    ok, why = verify(gh)
    assert ok, why
    assert gh.lists == 4


def test_times_out_while_still_running() -> None:
    ok, why = verify(
        FakeGh([[run(status="in_progress", conclusion=None)]], legs()), budget_s=100, poll_s=30
    )
    assert not ok and "timed out" in why and "in_progress" in why


def test_the_newest_attempt_is_the_one_judged() -> None:
    old = run(databaseId=5, conclusion="failure")
    new = run(databaseId=9)
    assert gate.pick_run([old, new], TAG, SHA) == new
    ok, _ = verify(FakeGh([[old, new]], legs()))
    assert ok


def test_release_workflow_uses_the_gate_and_the_wider_timeout() -> None:
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/release.yml"
    if not workflow.exists():  # an unpacked sdist carries the script, not the workflow
        pytest.skip("release.yml is not part of the sdist")
    text = workflow.read_text()
    assert "scripts/verify_tag_ci.py" in text
    assert "timeout-minutes: 120" in text
    assert gate.WAIT_BUDGET_S == 100 * 60


class Flaky(FakeGh):
    """Fails the first ``failures`` calls with a CalledProcessError, then behaves."""

    def __init__(self, failures: int | None, *a: Any, hang: bool = False) -> None:
        super().__init__(*a)
        self.failures = failures  # None: always fails
        self.hang = hang  # fail by timing out instead of exiting non-zero
        self.calls = 0

    def __call__(self, args: Sequence[str]) -> str:
        self.calls += 1
        if self.failures is None or self.calls <= self.failures:
            if self.hang:
                raise subprocess.TimeoutExpired(["gh", *args], gate.GH_TIMEOUT_S)
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502")
        return super().__call__(args)


def test_a_transient_gh_failure_is_retried() -> None:
    gh = Flaky(1, [[run()]], legs())
    ok, why = verify(gh)
    assert ok, why
    assert gh.calls == 3  # failed list, list, view


def test_a_persistent_gh_failure_is_fatal() -> None:
    gh = Flaky(None, [[run()]], legs())
    with pytest.raises(subprocess.CalledProcessError):
        verify(gh)
    assert gh.calls == gate.GH_ATTEMPTS


def test_a_timed_out_gh_call_is_retried_and_a_persistent_one_is_fatal() -> None:
    gh = Flaky(1, [[run()]], legs(), hang=True)
    ok, why = verify(gh)
    assert ok, why
    assert gh.calls == 3
    stuck = Flaky(None, [[run()]], legs(), hang=True)
    with pytest.raises(subprocess.TimeoutExpired):
        verify(stuck)
    assert stuck.calls == gate.GH_ATTEMPTS


def test_each_real_gh_call_has_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: Sequence[str], **kw: Any) -> Any:
        seen.update(kw, cmd=list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    assert gate._run_gh(["run", "list"]) == "[]"
    assert seen["timeout"] == gate.GH_TIMEOUT_S == 60
    assert seen["cmd"] == ["gh", "run", "list"] and seen["check"] is True
