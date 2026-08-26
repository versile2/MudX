#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def fail(message: str) -> None:
    raise AssertionError(message)


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        fail(f"missing file: {path.relative_to(ROOT)}")
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def raw_steps(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job.get("steps", []))


def artifact_names(job: dict, action: str) -> list[str]:
    return [
        str(step.get("with", {}).get("name", ""))
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(f"actions/{action}@")
    ]


def assert_sha_pinned(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)(?:\s+#\s*(.+))?$", raw, re.MULTILINE)
    if not uses:
        fail(f"{path.name} contains no actions")
    for action, comment in uses:
        if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", action):
            fail(f"{path.name} action is not full-SHA pinned: {action}")
        if not comment or not re.search(r"\bv\d+", comment):
            fail(f"{path.name} pin lacks a version comment: {action}")


def assert_checkout_safe(job: dict, label: str) -> None:
    checkouts = [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith("actions/checkout@")]
    if not checkouts or any(step.get("with", {}).get("persist-credentials") != "false" for step in checkouts):
        fail(f"{label} checkout does not disable persisted credentials")


def validate_legacy() -> None:
    for name in ("Update_MudX_Version.yml", "deploy-mudx-nuget.yml", "Build_And_Deploy.yml", "Deploy.yml"):
        if (WORKFLOWS / name).exists():
            fail(f"legacy workflow was not renamed: {name}")
        data = load_yaml(WORKFLOWS / f"old-{name}")
        if not str(data.get("name", "")).startswith("[OLD]"):
            fail(f"legacy workflow is not marked OLD: {name}")
        if any(job.get("if") != "${{ false }}" for job in data.get("jobs", {}).values()):
            fail(f"legacy workflow contains a runnable job: {name}")


def validate_prepare() -> None:
    path = WORKFLOWS / "prepare-release.yml"
    data = load_yaml(path)
    assert_sha_pinned(path)
    if data.get("permissions") != {"contents": "read"}:
        fail("prepare workflow default permissions are not read-only")
    jobs = data.get("jobs", {})
    validate = jobs.get("validate", {})
    publish = jobs.get("publish", {})
    if not validate or not publish:
        fail("prepare workflow must split validate and publish jobs")
    if validate.get("permissions") != {"contents": "read"}:
        fail("prepare validation job is not contents-read-only")
    assert_checkout_safe(validate, "prepare validation")
    validate_raw = raw_steps(validate)
    for token in ("LoadOptions]::PreserveWhitespace", "-getProperty:Version", "git bundle create"):
        if token not in validate_raw:
            fail(f"prepare validation missing {token}")
    if publish.get("permissions") != {"contents": "write", "issues": "write", "pull-requests": "write"}:
        fail("prepare publish permissions are not narrowly scoped")
    if publish.get("needs") != "validate":
        fail("prepare publish does not consume validation output")
    publish_raw = raw_steps(publish)
    for token in ("gh pr create", '"release"', "prepared branch already exists"):
        if token not in publish_raw:
            fail(f"prepare retry-safe publication missing {token}")
    if re.search(r"dotnet\s+(restore|build|test|run|pack)|docker\s+build", publish_raw):
        fail("prepare publish job executes repository build code")
    if re.search(r"\bsource\s+release-preparation", publish_raw):
        fail("prepare publish executes artifact text as shell code")
    if "No release preparation changes were produced" in path.read_text(encoding="utf-8"):
        fail("prepare retry path still aborts when the prepared branch has no new diff")
    if validate.get("outputs", {}).get("artifact-name") != "${{ steps.artifact.outputs.name }}":
        fail("prepare producer does not expose its exact artifact name")
    if artifact_names(validate, "upload-artifact") != ["${{ steps.artifact.outputs.name }}"]:
        fail("prepare upload does not use the producer-owned artifact name")
    if artifact_names(publish, "download-artifact") != ["${{ needs.validate.outputs.artifact-name }}"]:
        fail("prepare failed-job retry does not consume the retained producer artifact")


def validate_release() -> None:
    path = WORKFLOWS / "release.yml"
    data = load_yaml(path)
    assert_sha_pinned(path)
    if data.get("permissions") != {"contents": "read"}:
        fail("release workflow default permissions are not read-only")
    trigger = data.get("on", {}).get("pull_request", {})
    if trigger.get("branches") != ["dev"] or trigger.get("types") != ["closed"]:
        fail("release trigger is not a closed PR into dev")
    jobs = data.get("jobs", {})
    if set(jobs) != {"gate", "build", "attest", "publish"}:
        fail("release workflow must split gate, build, attest, and publish jobs")
    gate = jobs["gate"]
    condition = str(gate.get("if", ""))
    if not all(token in condition for token in ("merged == true", "release", "head.repo.full_name", "github.repository")):
        fail("release gate lacks merged, label, or same-repository provenance")
    gate_raw = raw_steps(gate)
    if "reviewDecision" not in gate_raw or "APPROVED" not in gate_raw:
        fail("release gate does not fail closed on final approval")
    build = jobs["build"]
    if build.get("permissions") != {"contents": "read"}:
        fail("release build job is not contents-read-only")
    assert_checkout_safe(build, "release build")
    build_raw = raw_steps(build)
    for token in ("dotnet pack", "type=oci", "provenance=mode=max", "sbom=true", "sha256sum", "image-application-manifest-digest.txt", "provenance-index-digest.txt"):
        if token not in build_raw:
            fail(f"release build missing {token}")
    attest = jobs["attest"]
    if attest.get("needs") != "build" or attest.get("permissions", {}).get("id-token") != "write":
        fail("release attestation job is not isolated from build")
    publish = jobs["publish"]
    if publish.get("environment") != "release" or set(publish.get("needs", [])) != {"gate", "build", "attest"}:
        fail("release publication lacks protected environment or verified prerequisites")
    publish_raw = raw_steps(publish)
    if any(str(step.get("uses", "")).startswith("actions/checkout@") for step in publish.get("steps", [])):
        fail("release publication checks out repository code")
    if re.search(r"dotnet\s+(restore|build|test|run|pack)|docker\s+build", publish_raw):
        fail("release publication executes repository build code")
    if re.search(r"\bsource\s+release-artifacts", publish_raw):
        fail("release publication executes artifact text as shell code")
    for token in (
        "gh attestation verify", ".draft", "published", "--clobber", "semantic package identity",
        "image-config-digest", "org.opencontainers.image.revision", "version tag digest verification",
        "stable tag digest verification", "mudx-release-metadata:start", "SHA256SUMS",
    ):
        if token not in publish_raw:
            fail(f"release publication missing retry/provenance contract: {token}")
    names = [step.get("name", "") for step in publish.get("steps", [])]
    for required in ("Publish missing NuGet main package", "Publish missing NuGet symbol package", "Promote verified digest to stable", "Finalize draft GitHub release last"):
        if required not in names:
            fail(f"release publication missing ordered step: {required}")
    if not names.index("Publish missing NuGet main package") < names.index("Publish missing NuGet symbol package") < names.index("Promote verified digest to stable") < names.index("Finalize draft GitHub release last"):
        fail("release publication order is unsafe")
    steps = {step.get("name", ""): step for step in publish.get("steps", [])}
    draft_upload = steps.get("Upload or repair draft assets only", {})
    if draft_upload.get("if") != "steps.release.outputs.state != 'published'" or "--clobber" not in draft_upload.get("run", ""):
        fail("asset replacement is not restricted to drafts")
    image_step = steps.get("Verify or publish provenance-bound image", {})
    image_raw = str(image_step.get("run", ""))
    for mismatch_guard in (
        'existing_application_manifest" == "$expected_application_manifest',
        'existing_config" == "$expected_config',
        "existing commit image provenance mismatch",
        "version tag provenance mismatch",
        "org.opencontainers.image.workflow",
    ):
        if mismatch_guard not in image_raw:
            fail(f"image provenance mismatch is not fail-closed: {mismatch_guard}")
    public = steps.get("Verify published release and no-op", {})
    if public.get("if") != "steps.release.outputs.state == 'published'" or "no publication mutation required" not in public.get("run", ""):
        fail("published release retry is not a verified no-op")
    public_raw = str(public.get("run", ""))
    for anchor in (
        "cmp --silent release-artifacts/release-assets/SHA256SUMS /tmp/public-assets/SHA256SUMS",
        "expected_nuget_checksum",
        "expected_image_digest",
        "expected_config",
        "steps.nuget.outputs.symbols-exist",
        "image-application-manifest-digest.txt",
        "recorded_commit",
        "recorded_version",
        "recorded_nuget_checksum",
        '"$recorded_commit" == "$MERGE_SHA"',
        '"$recorded_version" == "$VERSION"',
        '"$recorded_nuget_checksum" == "$expected_nuget_checksum"',
        '"$recorded_digest" == "$expected_image_digest"',
        '"$recorded_config" == "$expected_config"',
    ):
        if anchor not in public_raw:
            fail(f"published release is not anchored to same-run identity: {anchor}")
    notes_raw = str(steps.get("Record idempotent checksums and provenance in draft notes", {}).get("run", ""))
    for token in ("Version:", "Docs application manifest:", "deployment identity is the application manifest"):
        if token not in notes_raw:
            fail(f"release metadata block does not distinguish application identity: {token}")
    release_lookup_raw = str(steps.get("Detect draft versus published GitHub release", {}).get("run", ""))
    for target_guard in ("target_commitish", '"$release_target" == "$MERGE_SHA"', "draft target does not match merge SHA"):
        if target_guard not in release_lookup_raw:
            fail(f"existing draft target is not fail-closed: {target_guard}")
    finalize_raw = str(steps.get("Finalize draft GitHub release last", {}).get("run", ""))
    for tag_guard in ("git/ref/tags/$TAG", "tagged_sha", '"$tagged_sha" == "$MERGE_SHA"'):
        if tag_guard not in finalize_raw:
            fail(f"finalized Git tag is not verified against merge SHA: {tag_guard}")
    if build.get("outputs", {}).get("artifact-name") != "${{ steps.artifact.outputs.name }}":
        fail("release producer does not expose its exact artifact name")
    if artifact_names(build, "upload-artifact") != ["${{ steps.artifact.outputs.name }}"]:
        fail("release upload does not use the producer-owned artifact name")
    if attest.get("outputs", {}).get("artifact-name") != "${{ needs.build.outputs.artifact-name }}":
        fail("attestation job does not preserve the producing artifact identity")
    if artifact_names(attest, "download-artifact") != ["${{ needs.build.outputs.artifact-name }}"]:
        fail("attestation retry does not consume the retained build artifact")
    if artifact_names(publish, "download-artifact") != ["${{ needs.attest.outputs.artifact-name }}"]:
        fail("publication retry does not consume the retained attested artifact")


def validate_host() -> None:
    compose = load_yaml(ROOT / "deploy/mudx-docs/compose.yml")
    service = compose.get("services", {}).get("docs", {})
    if service.get("ports") != ["127.0.0.1:4560:8080"]:
        fail("Compose port is not loopback-bound")
    if service.get("restart") != "unless-stopped" or "healthcheck" not in service:
        fail("Compose runtime contract regressed")
    updater = (ROOT / "deploy/mudx-docs/update-mudx-docs.sh").read_text(encoding="utf-8")
    for token in ("flock -n", "CURRENT_DIGEST", "PREVIOUS_DIGEST", 'mv "$next_env" "$env_file"', "MUDX_UPDATE_FAILPOINT"):
        if token not in updater:
            fail(f"updater missing atomic/serialization contract: {token}")
    if "state/digests.env" in updater:
        fail("updater still has a second deployment authority")
    unit = (ROOT / "deploy/mudx-docs/mudx-docs-update.service").read_text(encoding="utf-8")
    for token in ("NoNewPrivileges=true", "ProtectSystem=strict", "ReadWritePaths=", "UMask=0077"):
        if token not in unit:
            fail(f"systemd unit lacks hardening: {token}")
    dockerfile = (ROOT / "src/MudX.Docs.Hybrid/MudX.Docs.Hybrid/Dockerfile").read_text(encoding="utf-8")
    if len(re.findall(r"^FROM\s+\S+@sha256:[0-9a-f]{64}", dockerfile, re.MULTILINE)) < 2:
        fail("Docker base images are not pinned by digest")


def main() -> None:
    validate_legacy()
    validate_prepare()
    validate_release()
    validate_host()
    print("PASS release pipeline structural contract")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print(f"FAIL: {error}")
        raise SystemExit(1)
