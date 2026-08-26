#!/usr/bin/env bash
set -Eeuo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
update_script="$repository_root/deploy/mudx-docs/update-mudx-docs.sh"
image_repository="ghcr.io/mudxtra/mudx/mudxdocwebsite"
old_digest="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
previous_digest="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
new_digest="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
secret_marker="must-not-appear-in-output"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_file_equals() {
    local file=$1
    local expected=$2
    [[ -f "$file" ]] || fail "missing file: $file"
    [[ "$(cat "$file")" == "$expected" ]] || fail "unexpected content in $file"
}

assert_state_equals() {
    local fixture=$1
    local current=$2
    local previous=$3
    assert_file_equals "$fixture/state/digests.env" "CURRENT_DIGEST=$current
PREVIOUS_DIGEST=$previous"
}

assert_failure_state_preserved() {
    local fixture=$1
    assert_file_equals "$fixture/mudx-docs.env" "MUDX_DOCS_IMAGE=$image_repository@$old_digest"
    assert_state_equals "$fixture" "$old_digest" "$previous_digest"
    if find "$fixture" -type f -name '*.tmp.*' -print -quit | grep -q .; then
        fail "temporary files remained after failure"
    fi
}

create_fixture() {
    local fixture
    fixture=$(mktemp -d)
    mkdir -p "$fixture/bin" "$fixture/state"
    : >"$fixture/compose.yml"
    printf 'MUDX_DOCS_IMAGE=%s@%s\n' "$image_repository" "$old_digest" >"$fixture/mudx-docs.env"
    printf 'CURRENT_DIGEST=%s\nPREVIOUS_DIGEST=%s\n' \
        "$old_digest" \
        "$previous_digest" >"$fixture/state/digests.env"

    cat >"$fixture/bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -Eeuo pipefail

echo "$*" >>"$FAKE_DOCKER_LOG"

if [[ "$1" == "pull" ]]; then
    [[ "$FAKE_DOCKER_SCENARIO" != "pull-failure" ]] || exit 42
    exit 0
fi

if [[ "$1" == "image" && "$2" == "inspect" ]]; then
    case "$FAKE_DOCKER_SCENARIO" in
        missing-digest)
            exit 0
            ;;
        invalid-digest)
            printf '%s\n' "$FAKE_IMAGE_REPOSITORY:stable"
            ;;
        *)
            printf '%s@%s\n' "$FAKE_IMAGE_REPOSITORY" "$FAKE_NEW_DIGEST"
            ;;
    esac
    exit 0
fi

if [[ "$1" == "compose" ]]; then
    env_file=""
    shift
    while (($#)); do
        if [[ "$1" == "--env-file" ]]; then
            env_file=$2
            shift 2
            continue
        fi
        shift
    done

    [[ -n "$env_file" ]] || exit 43
    deployed_image=$(sed -n 's/^MUDX_DOCS_IMAGE=//p' "$env_file")
    printf 'deployed=%s\n' "$deployed_image" >>"$FAKE_DOCKER_LOG"

    if [[ "$FAKE_DOCKER_SCENARIO" == "health-failure" && "$deployed_image" == *"@$FAKE_NEW_DIGEST" && ! -f "$FAKE_HEALTH_FAILED" ]]; then
        : >"$FAKE_HEALTH_FAILED"
        exit 44
    fi
    exit 0
fi

exit 45
FAKE_DOCKER
    chmod +x "$fixture/bin/docker"
    printf '%s\n' "$fixture"
}

run_update() {
    local fixture=$1
    local scenario=$2
    local candidate_digest=$3
    local output_file=$4

    FAKE_DOCKER_SCENARIO="$scenario" \
    FAKE_DOCKER_LOG="$fixture/docker.log" \
    FAKE_IMAGE_REPOSITORY="$image_repository" \
    FAKE_NEW_DIGEST="$candidate_digest" \
    FAKE_HEALTH_FAILED="$fixture/health-failed" \
    FAKE_SECRET_VALUE="$secret_marker" \
    DOCKER_BIN="$fixture/bin/docker" \
    COMPOSE_FILE="$fixture/compose.yml" \
    ENV_FILE="$fixture/mudx-docs.env" \
    STATE_DIR="$fixture/state" \
    IMAGE_REPOSITORY="$image_repository" \
    "$update_script" >"$output_file" 2>&1
}

test_same_digest_no_op() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    run_update "$fixture" same-digest "$old_digest" "$output" || fail "same digest should succeed"
    assert_failure_state_preserved "$fixture"
    ! grep -q '^compose ' "$fixture/docker.log" || fail "same digest invoked compose"
    ! grep -q "$secret_marker" "$output" || fail "output leaked secret marker"
    rm -rf "$fixture"
}

test_successful_promotion() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    run_update "$fixture" success "$new_digest" "$output" || fail "promotion should succeed"
    assert_file_equals "$fixture/mudx-docs.env" "MUDX_DOCS_IMAGE=$image_repository@$new_digest"
    assert_state_equals "$fixture" "$new_digest" "$old_digest"
    grep -q "deployed=$image_repository@$new_digest" "$fixture/docker.log" || fail "new digest was not deployed"
    ! grep -q "$secret_marker" "$output" || fail "output leaked secret marker"
    rm -rf "$fixture"
}

test_pull_failure() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    if run_update "$fixture" pull-failure "$new_digest" "$output"; then
        fail "pull failure unexpectedly succeeded"
    fi
    assert_failure_state_preserved "$fixture"
    ! grep -q '^compose ' "$fixture/docker.log" || fail "pull failure invoked compose"
    rm -rf "$fixture"
}

test_health_failure_rolls_back() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    if run_update "$fixture" health-failure "$new_digest" "$output"; then
        fail "unhealthy promotion unexpectedly succeeded"
    fi
    assert_failure_state_preserved "$fixture"
    grep -q "deployed=$image_repository@$new_digest" "$fixture/docker.log" || fail "candidate was not attempted"
    grep -q "deployed=$image_repository@$old_digest" "$fixture/docker.log" || fail "previous digest was not restored"
    rm -rf "$fixture"
}

test_missing_digest() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    if run_update "$fixture" missing-digest "$new_digest" "$output"; then
        fail "missing digest unexpectedly succeeded"
    fi
    assert_failure_state_preserved "$fixture"
    rm -rf "$fixture"
}

test_invalid_digest() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    if run_update "$fixture" invalid-digest "$new_digest" "$output"; then
        fail "invalid digest unexpectedly succeeded"
    fi
    assert_failure_state_preserved "$fixture"
    rm -rf "$fixture"
}

test_invalid_current_repository() {
    local fixture output
    fixture=$(create_fixture)
    output="$fixture/output.log"
    sed -i 's/^MUDX_DOCS_IMAGE=ghcr\.io/MUDX_DOCS_IMAGE=ghcrXio/' "$fixture/mudx-docs.env"
    if run_update "$fixture" success "$new_digest" "$output"; then
        fail "invalid current repository unexpectedly succeeded"
    fi
    assert_state_equals "$fixture" "$old_digest" "$previous_digest"
    rm -rf "$fixture"
}

[[ -x "$update_script" ]] || fail "missing executable update script: $update_script"

test_same_digest_no_op
test_successful_promotion
test_pull_failure
test_health_failure_rolls_back
test_missing_digest
test_invalid_digest
test_invalid_current_repository

echo "PASS update script contract"
