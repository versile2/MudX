# SecurityCode visual evidence

Before: `500c309629fa8ea8495fb3e10eb41c7431d968ca` (upstream base).
After: `6d462302c1fdf4aa18b65ae4e565f61765e00081` (captured consolidated SecurityCode).

## Comparison boundary

The same net9 Blazor Server harness runs against both revisions, without production edits. Common public parameters are Pattern, Password, Code and CodeChanged. **Label, HelperText, Required, Error, ErrorText and Disabled do not exist upstream.** The identical harness uses public parameter reflection to omit unavailable parameters; it does not fake their behavior. Thus before panels show the upstream absence of form-state support, not working upstream error/disabled states. After panels show the new supported states. These captions are harness annotations, not component labels.

Synthetic complete codes are entered with trusted keyboard input. The harness then requests an explicit error, matching validation after submission. The disabled row is empty to make enabled/disabled rendering and browser semantics comparable. Password masks only editable characters, leaving the separator visible. Required is verified through aria-required (no claim of a visual required asterisk). This is component state rendering evidence, not outer MudForm integration or a full test matrix.

The final-only clip uses common keyboard APIs: Tab into code, type 1/2/3/4 with literal skipping and documented default completion advance, Shift+Tab back, ArrowLeft/ArrowRight (skip fixed separator), Backspace (clear and move left), restore, Tab out, Shift+Tab back. No OnCompleted handler is installed. Visible key cues are capture-only annotations. Focus is independently asserted in capture.cjs and recorded in manifest.json.

## Files for publication

Only `media/*.png`, `media/*.webm`, `manifest.json`, this README, `capture.cjs`, and `harness/` are public-intended. **Do not upload local/**: it holds local build outputs, diagnostic logs, and temporary reproduction files. TARGET.md is task-local.

Four PNGs are component-only crops from desktop 1280x900 and narrow 500x800 viewports. Crop bounds, revision, route, observed state and SHA256 are in manifest.json. Clip viewport is 720x420. Served JavaScript and CSS hashes were checked against each exact source worktree.

All four still images were visually inspected, with readable component states and no clipped content. The 15.92-second clip was inspected via an eight-frame contact sheet; trusted focus assertions passed (not a claim of full real-time playback review). Both builds completed with 0 warnings and 0 errors on the final run. Browser pageerror list is empty; the Blazor Server log emits JSDisconnectedException during browser-context teardown, not during the captured interaction.

No images have been published by this capture task. Embed immutable raw GitHub URLs after the parent publishes the selected files. Do not label any of these files as the pre-correction native-Tab race; that optional comparison was not captured.

## Reproduction

Build the identical harness twice with `dotnet build Harness.csproj -p:MudXReviewRoot=<exact leased worktree>`, using the cached shared .NET installation. Launch the before/after DLLs on loopback ports 5284/5285, then `node capture.cjs`. Installed Playwright Chromium is used; no packages are installed. Artifacts are generated only under this evidence directory; source worktrees remain clean.

## Final correction provenance

At final feature commit 2d697669e9f77cf285d0eba42f751d1c8cb641af, the two after PNGs were recaptured and verified byte-identical. The final correction makes externally owned errors persist through editing and validation until cleared; the same displayed after-submission states are unchanged. The clip records 6d462302; the keyboard module is unchanged by this final correction. Screenshot evidence is not a claim of a complete interaction or assistive-technology audit.
