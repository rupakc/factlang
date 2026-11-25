"""Release gate: the CI run of the tag push must have succeeded, on every ``test-full`` leg.

``release.yml`` runs this before publishing. The only run that counts is the ``ci.yml`` run
triggered by the tag push itself (event ``push``, ``headBranch`` equal to the tag, ``headSha``
equal to the tagged commit); a green branch run, a nightly or a manual dispatch of the same
commit cannot substitute, because only the tag run executes the full matrix. It passes only
when that run concluded ``success`` and it has at least one ``test-full`` job, all ``success``.

Exit status: 0 verified, 1 not verified (failed run, failed leg, no such run, or timeout).

``gh`` is reached through one injectable callable so the decision logic is unit-tested
against fake JSON (``tests/unit/test_verify_tag_ci.py``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

WAIT = "wait"
OK = "ok"
FAIL = "fail"

WAIT_BUDGET_S = 100 * 60  # including queueing; the verify job's timeout is 120 minutes
POLL_S = 30

Gh = Callable[[Sequence[str]], str]


def pick_run(runs: Sequence[dict[str, Any]], tag: str, sha: str) -> dict[str, Any] | None:
    """The tag-push run of ``sha`` (the newest if re-run), or None; nothing else qualifies."""
    mine = [
        r
        for r in runs
        if r.get("event") == "push" and r.get("headBranch") == tag and r.get("headSha") == sha
    ]
    return max(mine, key=lambda r: r.get("databaseId", 0)) if mine else None


def judge_run(run: dict[str, Any] | None, jobs: Sequence[dict[str, Any]]) -> tuple[str, str]:
    """``(OK | WAIT | FAIL, reason)`` for the tag run and its jobs."""
    if run is None:
        return WAIT, "the CI run of the tag push does not exist yet"
    if run.get("status") != "completed":
        return WAIT, f"run {run.get('databaseId')} is {run.get('status')}"
    if run.get("conclusion") != "success":
        return FAIL, f"run {run.get('databaseId')} concluded {run.get('conclusion')}"
    legs = [j for j in jobs if str(j.get("name", "")).startswith("test-full")]
    if not legs:
        return FAIL, "the tag run has no test-full matrix legs"
    bad = [
        f"{j.get('name')}: {j.get('conclusion')}" for j in legs if j.get("conclusion") != "success"
    ]
    if bad:
        return FAIL, "test-full legs not successful: " + "; ".join(bad)
    return OK, f"run {run.get('databaseId')} succeeded with {len(legs)} test-full legs"


def _run_gh(args: Sequence[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


GH_ATTEMPTS = 3
GH_BACKOFF_S = 5.0


def call_with_retry(
    gh: Gh,
    args: Sequence[str],
    *,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = GH_ATTEMPTS,
    backoff_s: float = GH_BACKOFF_S,
) -> str:
    """``gh(args)``, retried on a transient ``CalledProcessError``; a persistent one is raised."""
    for attempt in range(1, attempts + 1):
        try:
            return gh(args)
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            sleep(backoff_s * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def verify(
    repo: str,
    tag: str,
    sha: str,
    *,
    gh: Gh = _run_gh,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    budget_s: float = WAIT_BUDGET_S,
    poll_s: float = POLL_S,
) -> tuple[bool, str]:
    """Poll until the tag run is decided or the budget runs out; ``(verified, reason)``."""
    deadline = clock() + budget_s
    reason = ""

    def call(args: Sequence[str]) -> str:
        return call_with_retry(gh, args, sleep=sleep, backoff_s=min(GH_BACKOFF_S, poll_s))

    while True:
        runs = json.loads(
            call(
                [
                    "run",
                    "list",
                    "--repo",
                    repo,
                    "--workflow",
                    "ci.yml",
                    "--event",
                    "push",
                    "--branch",
                    tag,
                    "--limit",
                    "50",
                    "--json",
                    "databaseId,event,headBranch,headSha,status,conclusion",
                ]
            )
        )
        run = pick_run(runs, tag, sha)
        jobs: list[dict[str, Any]] = []
        if (
            run is not None
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
        ):
            view = json.loads(
                call(["run", "view", str(run["databaseId"]), "--repo", repo, "--json", "jobs"])
            )
            jobs = view.get("jobs", [])
        state, reason = judge_run(run, jobs)
        if state == OK:
            return True, reason
        if state == FAIL:
            return False, reason
        if clock() >= deadline:
            return False, f"timed out after {budget_s:.0f}s: {reason}"
        sleep(poll_s)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        ok, reason = verify(args.repo, args.tag, args.sha)
    except subprocess.CalledProcessError as exc:
        print(f"::error::gh failed after {GH_ATTEMPTS} attempts: {exc.stderr or exc}")
        return 1
    if ok:
        print(f"CI verified for {args.tag} ({args.sha}): {reason}")
        return 0
    print(f"::error::{reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
