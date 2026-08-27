#!/usr/bin/env bash
set -Eeuo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
release_workflow="$repository_root/.github/workflows/release.yml"
build_workflow="$repository_root/.github/workflows/Build_And_Test.yml"
docs_dockerfile="$repository_root/src/MudX.Docs.Hybrid/MudX.Docs.Hybrid/Dockerfile"

fail() {
    echo "FAIL $current_test: $1" >&2
    return 1
}

step_line() {
    local name=$1
    grep -nFx "      - name: $name" "$release_workflow" | cut -d: -f1
}

step_body() {
    local name=$1
    awk -v marker="      - name: $name" '
        $0 == marker { found = 1; next }
        found && /^      - name:/ { exit }
        found { print }
    ' "$release_workflow"
}

require_contains() {
    local text=$1 expected=$2 message=$3
    grep -Fq -- "$expected" <<<"$text" || fail "$message"
}

require_not_contains() {
    local text=$1 rejected=$2 message=$3
    if grep -Fq -- "$rejected" <<<"$text"; then
        fail "$message"
    fi
}

test_guard_order() {
    local guard create image assets main symbols stable notes finalize
    guard=$(step_line "Guard stable against historical release retries")
    create=$(step_line "Create missing draft GitHub release")
    image=$(step_line "Verify or publish provenance-bound image")
    assets=$(step_line "Upload or repair draft assets only")
    main=$(step_line "Publish missing NuGet main package")
    symbols=$(step_line "Publish missing NuGet symbol package")
    stable=$(step_line "Promote verified digest to stable")
    notes=$(step_line "Record idempotent checksums and provenance in draft notes")
    finalize=$(step_line "Finalize draft GitHub release last")
    for mutation in "$create" "$image" "$assets" "$main" "$symbols" "$stable" "$notes" "$finalize"; do
        (( guard < mutation )) || fail "historical-release rejection must precede every publication mutation"
    done
}

test_guard_drafts() {
    local body
    body=$(step_body "Guard stable against historical release retries")
    require_contains "$body" '.prerelease == false' "guard must exclude prereleases"
    require_not_contains "$body" '.draft == false' "guard must include stable-SemVer draft releases"
}

test_no_op_stable() {
    local body
    body=$(step_body "Verify published release and no-op")
    require_contains "$body" 'docker://$IMAGE_REPOSITORY:sha-$MERGE_SHA' "published no-op must verify the immutable commit tag"
    require_contains "$body" 'docker://$IMAGE_REPOSITORY:$VERSION' "published no-op must verify the immutable version tag"
    require_not_contains "$body" 'docker://$IMAGE_REPOSITORY:stable' "historical published no-op must not require mutable stable"
}

test_reproducible_oci() {
    local body dockerfile
    body=$(step_body "Build exact-SHA OCI image with provenance and SBOM")
    dockerfile=$(<"$docs_dockerfile")
    require_contains "$body" 'source_date_epoch=' "OCI build must derive a stable source timestamp"
    require_contains "$body" 'SOURCE_DATE_EPOCH' "OCI build must pass SOURCE_DATE_EPOCH"
    require_contains "$body" 'rewrite-timestamp=true' "OCI exporter must normalize layer timestamps"
    require_contains "$dockerfile" 'ARG SOURCE_DATE_EPOCH=0' "local Docker builds need a deterministic timestamp default"
    require_contains "$dockerfile" 'staticwebassets.endpoints.json' "docs publish must normalize generated endpoint metadata"
    require_contains "$dockerfile" 'Last-Modified' "docs publish must replace volatile endpoint timestamps"
}

test_final_head_approval() {
    local body
    body=$(step_body "Fail closed unless final review decision is approved")
    require_contains "$body" 'PR_HEAD_SHA' "approval gate must receive the final PR head SHA"
    require_contains "$body" '/reviews' "approval gate must inspect individual reviews"
    require_contains "$body" '.commit_id' "approval gate must bind approval to a commit"
}

test_nuget_content_types() {
    local initial after
    initial=$(step_body "Check NuGet main and symbol package identities")
    after=$(step_body "Verify NuGet main and symbol identities after publication")
    for body in "$initial" "$after"; do
        require_contains "$body" 'xml.etree.ElementTree' "NuGet comparison must parse content-type declarations"
        require_contains "$body" "'[Content_Types].xml'" "NuGet comparison must retain normalized content types"
        require_not_contains "$body" "n not in {'.signature.p7s', '[Content_Types].xml'}" "NuGet comparison must not discard the entire content-types document"
    done
}

test_immutable_inspection_errors() {
    local body
    body=$(step_body "Verify or publish provenance-bound image")
    require_contains "$body" 'manifest unknown' "immutable tag inspection must recognize confirmed manifest absence"
    require_contains "$body" 'name unknown' "immutable tag inspection must recognize confirmed repository absence"
    require_not_contains "$body" '2>/dev/null || true' "immutable tag inspection must not convert arbitrary errors to absence"
}

test_reject_prerelease() {
    local body
    body=$(step_body "Detect draft versus published GitHub release")
    require_contains "$body" '.prerelease' "published release detection must inspect prerelease state"
    require_contains "$body" 'published release must not be marked prerelease' "published stable release must reject prerelease metadata"
}

test_portable_tar_reader() {
    local body
    body=$(step_body "Build exact-SHA OCI image with provenance and SBOM")
    require_not_contains "$body" 'extractall(' "OCI reader must not depend on version-sensitive extractall filters"
    require_contains "$body" 'extractfile(' "OCI reader must read validated archive members without extracting"
}

test_oci_reader_runtime() (
    set -Eeuo pipefail
    local fixture archive_root nested_digest application_digest config_digest
    fixture=$(mktemp -d)
    trap 'rm -rf -- "$fixture"' EXIT
    archive_root="$fixture/archive"
    mkdir -p "$archive_root/blobs/sha256" "$fixture/release-artifacts/release-assets"

    config_digest="sha256:$(printf 'c%.0s' {1..64})"
    printf '{"schemaVersion":2,"config":{"digest":"%s"},"layers":[]}' "$config_digest" \
        >"$fixture/application-manifest.json"
    application_digest=$(sha256sum "$fixture/application-manifest.json" | cut -d' ' -f1)
    cp "$fixture/application-manifest.json" "$archive_root/blobs/sha256/$application_digest"

    printf '{"schemaVersion":2,"manifests":[{"mediaType":"application/vnd.oci.image.manifest.v1+json","digest":"sha256:%s","platform":{"architecture":"amd64","os":"linux"}}]}' \
        "$application_digest" >"$fixture/nested-index.json"
    nested_digest=$(sha256sum "$fixture/nested-index.json" | cut -d' ' -f1)
    cp "$fixture/nested-index.json" "$archive_root/blobs/sha256/$nested_digest"

    printf '{"schemaVersion":2,"manifests":[{"mediaType":"application/vnd.oci.image.index.v1+json","digest":"sha256:%s"}]}' \
        "$nested_digest" >"$archive_root/index.json"
    tar -C "$archive_root" -cf "$fixture/release-artifacts/docs-image.oci.tar" index.json blobs

    if ! step_body "Build exact-SHA OCI image with provenance and SBOM" | awk '
        /^          python3 - <<'\''PY'\''$/ { found = 1; next }
        found && /^          PY$/ { exit }
        found { sub(/^          /, ""); print }
    ' | (cd "$fixture" && python3 -) >"$fixture/reader.out" 2>"$fixture/reader.err"; then
        fail "embedded OCI reader failed on a nested image index: $(tail -n 1 "$fixture/reader.err")"
    fi

    [[ "$(<"$fixture/release-artifacts/release-assets/image-application-manifest-digest.txt")" == "sha256:$application_digest" ]] ||
        fail "embedded OCI reader wrote the wrong application manifest identity"
    [[ "$(<"$fixture/release-artifacts/release-assets/image-config-digest.txt")" == "$config_digest" ]] ||
        fail "embedded OCI reader wrote the wrong config identity"
    [[ "$(<"$fixture/release-artifacts/provenance-index-digest.txt")" == "sha256:$nested_digest" ]] ||
        fail "embedded OCI reader wrote the wrong provenance index identity"
)

test_ci_contract_wiring() {
    require_contains "$(<"$build_workflow")" 'Validate release workflow contracts' "PR CI must run the release workflow contracts"
    require_contains "$(<"$build_workflow")" 'bash tests/ReleasePipelineTests/release-workflow-contracts.sh' "PR CI must execute the dependency-free contract"
}

tests=(
    guard_order
    guard_drafts
    no_op_stable
    reproducible_oci
    final_head_approval
    nuget_content_types
    immutable_inspection_errors
    reject_prerelease
    portable_tar_reader
    oci_reader_runtime
    ci_contract_wiring
)

requested=("${@:-${tests[@]}}")
for current_test in "${requested[@]}"; do
    if ! declare -F "test_$current_test" >/dev/null; then
        echo "Unknown test: $current_test" >&2
        exit 2
    fi
    "test_$current_test"
    echo "PASS $current_test"
done
