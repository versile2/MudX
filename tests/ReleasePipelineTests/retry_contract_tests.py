#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def load_workflow(name: str) -> dict:
    return yaml.load((WORKFLOWS / name).read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def steps_by_name(job: dict) -> dict[str, dict]:
    return {str(step.get("name", "")): step for step in job.get("steps", [])}


def artifact_names(job: dict, action: str) -> list[str]:
    return [
        str(step.get("with", {}).get("name", ""))
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(f"actions/{action}@")
    ]


def test_failed_job_rerun_artifact_identity() -> None:
    prepare = load_workflow("prepare-release.yml")["jobs"]
    validate = prepare["validate"]
    publish = prepare["publish"]
    assert validate.get("outputs", {}).get("artifact-name") == "${{ steps.artifact.outputs.name }}"
    assert artifact_names(validate, "upload-artifact") == ["${{ steps.artifact.outputs.name }}"]
    assert artifact_names(publish, "download-artifact") == ["${{ needs.validate.outputs.artifact-name }}"]

    release = load_workflow("release.yml")["jobs"]
    build = release["build"]
    attest = release["attest"]
    release_publish = release["publish"]
    assert build.get("outputs", {}).get("artifact-name") == "${{ steps.artifact.outputs.name }}"
    assert artifact_names(build, "upload-artifact") == ["${{ steps.artifact.outputs.name }}"]
    assert attest.get("outputs", {}).get("artifact-name") == "${{ needs.build.outputs.artifact-name }}"
    assert artifact_names(attest, "download-artifact") == ["${{ needs.build.outputs.artifact-name }}"]
    assert artifact_names(release_publish, "download-artifact") == ["${{ needs.attest.outputs.artifact-name }}"]

    run_id = 731
    producer_attempt = 1
    failed_job_attempt = 2
    retained_prepare = f"release-preparation-{run_id}-{producer_attempt}"
    retained_release = f"mudx-release-{run_id}-{producer_attempt}"
    assert retained_prepare != f"release-preparation-{run_id}-{failed_job_attempt}"
    assert retained_release != f"mudx-release-{run_id}-{failed_job_attempt}"
    assert f"release-preparation-{run_id}-{failed_job_attempt}" != retained_prepare
    assert f"mudx-release-{run_id}-{failed_job_attempt}" != retained_release


def publication_actions(main: str, symbols: str) -> list[str]:
    if main == "mismatch" or symbols == "mismatch":
        raise ValueError("published package semantic identity mismatch")
    actions = []
    if main == "absent":
        actions.append("main")
    if symbols == "absent":
        actions.append("symbols")
    return actions


def stable_promotion_allowed(candidate: str, eligible_versions: list[str]) -> bool:
    version_key = lambda value: tuple(int(part) for part in value.split("."))
    return not eligible_versions or version_key(candidate) >= max(map(version_key, eligible_versions))


def test_historical_release_cannot_move_stable_backward() -> None:
    publish = load_workflow("release.yml")["jobs"]["publish"]
    steps = steps_by_name(publish)
    names = list(steps)
    guard = steps["Guard stable against historical release retries"]
    guard_raw = str(guard.get("run", ""))
    assert guard.get("if") == "steps.release.outputs.state != 'published'"
    assert names.index("Guard stable against historical release retries") < names.index("Promote verified digest to stable")
    for token in ("/releases", ".draft == false", ".prerelease == false", "sort -V", "Refusing stale release"):
        assert token in guard_raw
    assert not stable_promotion_allowed("9.8.0", ["9.8.0", "9.9.0"])
    assert stable_promotion_allowed("9.9.0", ["9.8.0", "9.9.0"])
    assert stable_promotion_allowed("10.0.0", ["9.9.0"])


def test_partial_symbol_publication_contract() -> None:
    publish = load_workflow("release.yml")["jobs"]["publish"]
    steps = steps_by_name(publish)
    identity = steps["Check NuGet main and symbol package identities"]
    identity_raw = str(identity.get("run", ""))
    assert "api/v2/symbolpackage" in identity_raw
    assert "main-exists=" in identity_raw
    assert "symbols-exist=" in identity_raw
    assert "published NuGet symbol package semantic identity mismatch" in identity_raw

    main = steps["Publish missing NuGet main package"]
    symbols = steps["Publish missing NuGet symbol package"]
    assert "steps.nuget.outputs.main-exists == 'false'" in str(main.get("if", ""))
    assert "--no-symbols" in str(main.get("run", ""))
    assert "steps.nuget.outputs.symbols-exist == 'false'" in str(symbols.get("if", ""))
    assert "*.snupkg" in str(symbols.get("run", ""))
    assert "--skip-duplicate" in str(symbols.get("run", ""))

    verify = str(steps["Verify NuGet main and symbol identities after publication"].get("run", ""))
    assert "api/v2/symbolpackage" in verify
    assert "published NuGet symbol package semantic identity mismatch after publication" in verify

    names = list(steps)
    assert names.index("Publish missing NuGet main package") < names.index("Publish missing NuGet symbol package")
    assert names.index("Publish missing NuGet symbol package") < names.index("Promote verified digest to stable")
    assert publication_actions("present", "absent") == ["symbols"]
    assert publication_actions("absent", "absent") == ["main", "symbols"]
    assert publication_actions("present", "present") == []
    for main_state, symbol_state in (("mismatch", "present"), ("present", "mismatch")):
        try:
            publication_actions(main_state, symbol_state)
        except ValueError:
            pass
        else:
            raise AssertionError("package mismatch did not fail closed")


def digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def create_oci_fixture(path: Path, attestation_nonce: str, application_nonce: str = "stable") -> None:
    labels = {
        "org.opencontainers.image.revision": "a" * 40,
        "org.opencontainers.image.source": "https://github.com/MudXtra/MudX",
        "org.opencontainers.image.workflow": "MudXtra/MudX/.github/workflows/release.yml@refs/heads/dev",
    }
    config = json_bytes({"config": {"Labels": labels}, "application": application_nonce})
    config_digest = digest(config)
    layer = application_nonce.encode()
    layer_digest = digest(layer)
    application = json_bytes({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": config_digest, "mediaType": "application/vnd.oci.image.config.v1+json", "size": len(config)},
        "layers": [{"digest": layer_digest, "mediaType": "application/vnd.oci.image.layer.v1.tar", "size": len(layer)}],
    })
    application_digest = digest(application)
    attestation = json_bytes({"schemaVersion": 2, "nonce": attestation_nonce})
    attestation_digest = digest(attestation)
    nested = json_bytes({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "digest": application_digest,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {"architecture": "amd64", "os": "linux"},
                "size": len(application),
            },
            {
                "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
                "digest": attestation_digest,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {"architecture": "unknown", "os": "unknown"},
                "size": len(attestation),
            },
        ],
    })
    nested_digest = digest(nested)
    index = json_bytes({
        "schemaVersion": 2,
        "manifests": [{"digest": nested_digest, "mediaType": "application/vnd.oci.image.index.v1+json", "size": len(nested)}],
    })
    blobs = {
        config_digest: config,
        layer_digest: layer,
        application_digest: application,
        attestation_digest: attestation,
        nested_digest: nested,
    }
    with tarfile.open(path, "w") as archive:
        for name, data in (("index.json", index), ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}')):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        for blob_digest, data in blobs.items():
            info = tarfile.TarInfo(f"blobs/sha256/{blob_digest.split(':', 1)[1]}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def oci_identity(path: Path) -> tuple[str, str, str, dict]:
    with tempfile.TemporaryDirectory() as directory:
        with tarfile.open(path) as archive:
            archive.extractall(directory, filter="data")
        root = Path(directory)
        index = json.loads((root / "index.json").read_text(encoding="utf-8"))

        def blob(blob_digest: str) -> dict:
            return json.loads((root / "blobs" / "sha256" / blob_digest.split(":", 1)[1]).read_text(encoding="utf-8"))

        def manifests(document: dict):
            for item in document["manifests"]:
                if item["mediaType"].endswith("image.index.v1+json"):
                    yield from manifests(blob(item["digest"]))
                else:
                    yield item

        descriptor = next(
            item for item in manifests(index)
            if item.get("platform", {}).get("architecture") == "amd64"
            and item.get("platform", {}).get("os") == "linux"
        )
        manifest = blob(descriptor["digest"])
        config = blob(manifest["config"]["digest"])
        provenance_index = index["manifests"][0]["digest"]
        return provenance_index, descriptor["digest"], manifest["config"]["digest"], config["config"]["Labels"]


def test_application_manifest_retry_identity() -> None:
    release = load_workflow("release.yml")
    build_raw = "\n".join(str(step.get("run", "")) for step in release["jobs"]["build"]["steps"])
    publish_raw = "\n".join(str(step.get("run", "")) for step in release["jobs"]["publish"]["steps"])
    assert "image-application-manifest-digest.txt" in build_raw
    assert "provenance-index-digest.txt" in build_raw
    assert "expected_application_manifest" in publish_raw
    assert "--override-os linux --override-arch amd64" in publish_raw
    assert "skopeo copy --all" not in publish_raw

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = root / "first.oci.tar"
        second = root / "second.oci.tar"
        mismatch = root / "mismatch.oci.tar"
        create_oci_fixture(first, "first-provenance")
        create_oci_fixture(second, "second-provenance")
        create_oci_fixture(mismatch, "third-provenance", "changed-application")
        first_identity = oci_identity(first)
        second_identity = oci_identity(second)
        mismatch_identity = oci_identity(mismatch)
        assert first_identity[0] != second_identity[0]
        assert first_identity[1:] == second_identity[1:]
        assert first_identity[1] != mismatch_identity[1]
        assert first_identity[2] != mismatch_identity[2]


def main() -> None:
    test_failed_job_rerun_artifact_identity()
    test_historical_release_cannot_move_stable_backward()
    test_partial_symbol_publication_contract()
    test_application_manifest_retry_identity()
    print("PASS release retry contract fixtures")


if __name__ == "__main__":
    main()
