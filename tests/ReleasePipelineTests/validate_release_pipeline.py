#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def fail(message: str) -> None:
    raise AssertionError(message)


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        fail(f"missing file: {path.relative_to(ROOT)}")
    with path.open(encoding="utf-8") as stream:
        return yaml.load(stream, Loader=yaml.BaseLoader)


def assert_sha_pinned(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    uses_lines = re.findall(r"^\s*uses:\s*([^\s#]+)(?:\s+#\s*(.+))?$", raw, re.MULTILINE)
    if not uses_lines:
        fail(f"{path.name} contains no pinned actions")
    for action, comment in uses_lines:
        if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", action):
            fail(f"{path.name} action is not full-SHA pinned: {action}")
        if not comment or not re.search(r"\bv\d+", comment):
            fail(f"{path.name} action pin lacks a version comment: {action}")


def validate_legacy_workflows() -> None:
    legacy_names = (
        "Update_MudX_Version.yml",
        "deploy-mudx-nuget.yml",
        "Build_And_Deploy.yml",
        "Deploy.yml",
    )
    for original_name in legacy_names:
        if (WORKFLOWS / original_name).exists():
            fail(f"legacy workflow was not renamed: {original_name}")
        old_path = WORKFLOWS / f"old-{original_name}"
        data = load_yaml(old_path)
        if not str(data.get("name", "")).startswith("[OLD]"):
            fail(f"legacy workflow name is not visibly marked OLD: {old_path.name}")
        jobs = data.get("jobs", {})
        if not jobs:
            fail(f"legacy workflow has no jobs: {old_path.name}")
        for job_name, job in jobs.items():
            if job.get("if") != "${{ false }}":
                fail(f"legacy job is runnable: {old_path.name}:{job_name}")


def validate_prepare_workflow() -> None:
    path = WORKFLOWS / "prepare-release.yml"
    data = load_yaml(path)
    assert_sha_pinned(path)
    trigger = data.get("on", {})
    version_input = trigger.get("workflow_dispatch", {}).get("inputs", {}).get("version", {})
    if version_input.get("required") != "true" or version_input.get("type") != "string":
        fail("prepare-release version input is not a required string")
    permissions = data.get("permissions", {})
    if permissions != {"contents": "write", "issues": "write", "pull-requests": "write"}:
        fail(f"prepare-release permissions are not minimal/expected: {permissions}")
    raw = path.read_text(encoding="utf-8")
    required_tokens = (
        "release/${VERSION}",
        "LoadOptions]::PreserveWhitespace",
        "-getProperty:Version",
        "gh pr create",
        '"release"',
    )
    for token in required_tokens:
        if token not in raw:
            fail(f"prepare-release missing contract token: {token}")
    lowered = raw.lower()
    for forbidden in ("auto-approve", "automerge", "auto-merge"):
        if forbidden in lowered:
            fail(f"prepare-release contains forbidden behavior: {forbidden}")


def validate_release_workflow() -> None:
    path = WORKFLOWS / "release.yml"
    data = load_yaml(path)
    assert_sha_pinned(path)
    trigger = data.get("on", {}).get("pull_request", {})
    if trigger.get("branches") != ["dev"] or trigger.get("types") != ["closed"]:
        fail("release trigger must be pull_request closed into dev")
    if not data.get("concurrency"):
        fail("release workflow lacks concurrency")
    permissions = data.get("permissions", {})
    expected_permissions = {"contents": "write", "packages": "write", "pull-requests": "read"}
    if permissions != expected_permissions:
        fail(f"release permissions are not minimal/expected: {permissions}")
    jobs = data.get("jobs", {})
    release_job = jobs.get("release", {})
    job_condition = release_job.get("if", "")
    if "merged == true" not in job_condition or "release" not in job_condition:
        fail("release job does not require merged=true and the release label")

    raw = path.read_text(encoding="utf-8")
    required_tokens = (
        "github.event.pull_request.merge_commit_sha",
        "src/MudX/MudX.csproj",
        "--skip-duplicate",
        "sha-${MERGE_SHA}",
        "${VERSION}",
        "docker buildx imagetools create",
        "gh release edit",
    )
    for token in required_tokens:
        if token not in raw:
            fail(f"release workflow missing contract token: {token}")

    step_names = [step.get("name", "") for step in release_job.get("steps", [])]
    try:
        nuget_index = step_names.index("Publish NuGet idempotently")
        stable_index = step_names.index("Promote verified digest to stable")
        finalize_index = step_names.index("Finalize GitHub release")
    except ValueError as exc:
        fail(f"release workflow is missing a required ordered step: {exc}")
    if not nuget_index < stable_index < finalize_index:
        fail("release ordering must be NuGet, stable promotion, then final release")

    lowered = raw.lower()
    for forbidden in ("appleboy", "ssh_hostname", "ghcr_pat", "delete old", ":latest"):
        if forbidden in lowered:
            fail(f"new release workflow contains legacy behavior: {forbidden}")


def validate_compose_template() -> None:
    path = ROOT / "deploy" / "mudx-docs" / "compose.yml"
    data = load_yaml(path)
    service = data.get("services", {}).get("docs", {})
    if service.get("image") != "${MUDX_DOCS_IMAGE:?Set MUDX_DOCS_IMAGE to an immutable repository digest}":
        fail("compose image is not supplied as a required immutable env value")
    if service.get("ports") != ["4560:8080"]:
        fail("compose does not preserve host port 4560 -> 8080")
    if service.get("restart") != "unless-stopped":
        fail("compose restart policy changed")
    if "healthcheck" not in service:
        fail("compose service lacks a health check")
    network = data.get("networks", {}).get("mudx-docs", {})
    if network.get("driver") != "bridge":
        fail("compose network is not bridge")


def main() -> int:
    validate_legacy_workflows()
    validate_prepare_workflow()
    validate_release_workflow()
    validate_compose_template()
    print("PASS release pipeline structural contract")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
