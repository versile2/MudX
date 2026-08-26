# Releasing MudX

MudX releases are prepared through a reviewed pull request and published from the exact merge commit. The release pipeline does not approve or merge its own changes.

## Workflow order

1. Merge the release/deployment infrastructure pull request into `dev`.
2. For the current release candidate, keep MudXtra/MudX PR #65 green and reviewed. Apply the `release` label only after the infrastructure workflow is present on `dev` and PR #65 is ready to publish.
3. For later releases, run **Prepare MudX release** with a stable SemVer value such as `9.9.0`. Do not include a `v`, prerelease suffix, or build metadata.
4. Review the generated `release/<version>` pull request. It updates the MudX project version, regenerates tracked assets, runs focused checks, and applies the `release` label. A human still owns approval and merge.
5. Merge the labeled pull request into `dev`. **Release MudX** runs only for a merged pull request into `dev` that carries the `release` label.

Do not run the four `old-*` workflows. They remain in source only as disabled historical references.

## Release identity and ordering

`release.yml` checks out `github.event.pull_request.merge_commit_sha` and derives the package version from `src/MudX/MudX.csproj`. The release tag is `v<version>`. An existing tag must already point to that exact merge commit or the workflow stops.

The workflow regenerates tracked assets and requires a clean diff before it builds release artifacts. It then:

1. builds and tests the focused .NET projects;
2. packs the NuGet package and symbols once;
3. validates package static assets;
4. creates or resumes a draft GitHub release and uploads the packages;
5. builds the docs image from the same checkout and publishes `:<version>` and `:sha-<full-merge-sha>` as immutable references;
6. publishes to NuGet with `--skip-duplicate`;
7. promotes the verified image digest to the mutable `:stable` pointer;
8. records the merge commit and image digest in the release notes;
9. finalizes the GitHub release last.

The version and commit image tags must resolve to the same digest. Old versioned images are never deleted by this pipeline.

## Retry behavior

A failed job may be rerun from the same merged-pull-request event.

- An existing Git tag is accepted only when it points to the same merge commit.
- An existing draft release is resumed.
- Release assets are uploaded with replacement enabled so a partial draft can be repaired.
- NuGet publication uses `--skip-duplicate`.
- An existing `sha-<full-merge-sha>` image is reused after its digest is validated.
- An existing version image is accepted only when its digest matches the commit image.
- `:stable` is not moved until NuGet publication succeeds.

If the GitHub release is already public, confirm its assets, commit, and recorded digest before rerunning. Never delete or retag an immutable version to work around a mismatch; investigate the release inputs instead.

## Host templates

The Debian host templates are under `deploy/mudx-docs/`:

- `compose.yml` keeps the existing `4560:8080` mapping, bridge networking, `restart: unless-stopped`, and health contract;
- `mudx-docs.env.example` documents the required immutable image reference;
- `update-mudx-docs.sh` pulls `:stable`, resolves its digest, performs a health-gated update, and restores the prior digest on failure;
- `mudx-docs-update.service` and `.timer` provide optional systemd scheduling.

Tracked files contain no registry credentials or mutable deployment state. Keep `mudx-docs.env` and the `state/` directory outside version control.

### Private GHCR authentication

GHCR remains private in this release slice. Before installation or update, authenticate Docker on the host with an account/token that has package read access. Store that credential only in the host's Docker credential store. Do not place a PAT in the env file, Compose file, systemd unit, update script, shell history, or repository.

### Install gate

Installation is an operator-approved host change and is not performed by repository workflows.

1. Create `/opt/mudx-docs` with ownership appropriate for the Docker operator.
2. Copy `compose.yml` and `update-mudx-docs.sh` there.
3. Copy `mudx-docs.env.example` to `mudx-docs.env` and replace the placeholder with a verified `repository@sha256:<64-hex>` reference.
4. Create the local `state/` directory. Record the selected digest in `state/digests.env` as `CURRENT_DIGEST=<digest>` and leave `PREVIOUS_DIGEST=` empty until the first successful update.
5. Run `docker compose --env-file mudx-docs.env -f compose.yml config` and inspect the rendered configuration.

Do not alter the live container during this gate.

### Cutover gate

Cutover requires explicit production approval.

1. Pull the selected immutable digest while the existing `/MudX` container remains running.
2. At the approved cutover window, stop and rename the legacy `/MudX` container so Compose can claim the preserved `MudX` name. Keep the renamed container for rollback until the cutover is accepted.
3. Run `docker compose --env-file mudx-docs.env -f compose.yml up -d --wait` from `/opt/mudx-docs`.
4. Confirm the container is healthy and `http://127.0.0.1:4560/healthz` returns HTTP 200 with `Healthy`.
5. Confirm the public docs route before accepting the cutover. If validation fails, remove the failed Compose container and rename/start the legacy container.

The Compose service retains the existing container name `MudX`, host port, bridge network, and restart policy.

### Timer enable gate

Copy the service and timer templates to `/etc/systemd/system/` only after the install and cutover are accepted. Review their paths, run `systemd-analyze verify`, and reload systemd. Enabling or starting `mudx-docs-update.timer` is a separate operator-approved action; source changes do not enable it.

## Troubleshooting

- **Stable pull fails:** verify host network access and private-GHCR authentication. The running container and state files remain unchanged.
- **Stable does not resolve to a digest:** inspect `docker image inspect ghcr.io/mudxtra/mudx/mudxdocwebsite:stable`. Do not deploy a mutable-only reference.
- **Compose health fails:** inspect `docker compose logs docs`. The updater restores the prior env reference and runs Compose again with that digest.
- **Version tag mismatch:** stop. Confirm the merged PR, project version, Git tag target, and immutable image digest. Do not overwrite the existing tag.
- **NuGet already exists:** a rerun should report the duplicate as skipped. Confirm the package version and checksum before continuing.
- **Draft release remains:** fix the failed stage and rerun the same workflow event. The draft is intentionally finalized last.

## Manual rollback

The updater atomically records both accepted digests in `state/digests.env`; use its `PREVIOUS_DIGEST` value to roll back manually after approval:

1. construct the full image reference `ghcr.io/mudxtra/mudx/mudxdocwebsite@<previous-digest>`;
2. write that value to a temporary env file in `/opt/mudx-docs`;
3. validate it with `docker compose --env-file <temporary-file> -f compose.yml config`;
4. run `docker compose --env-file <temporary-file> -f compose.yml up -d --wait`;
5. after health is confirmed, atomically replace `mudx-docs.env` and update the local current/previous state records.

Never roll back by moving an immutable version tag. Roll back by selecting a previously verified digest.
