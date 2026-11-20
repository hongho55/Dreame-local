# Shared upstream bugfix candidates

Checked against freshly fetched `dwyschka/Dreame-local` `main` at
`df9160cee933626f08c112d9159f9a66cea38ccc` (0.9.1). These are candidates,
not submitted PRs. Model-independent code defects do not imply that every robot
or every execution path exhibits the symptom.

## Confirmed defects in original source

### Stored-map live robot angle (standalone PR)

- `app/api.py:252` copies `raw[9] = lraw[9]` while both upstream Web and HACS decode a signed 16-bit little-endian angle at bytes 9–10.
- Examples reproduced on original code: live 300 becomes 44 with a zero saved high byte; live 90 over a saved negative angle becomes -166.
- Fix only `raw[9:11] = lraw[9:11]`; no model check, map-coordinate changes or device command.
- `tests/test_api_map_angle.py`: six tests with signed boundaries, large positives, stale high-byte clearing, position/frame/dimension invariants, missing/truncated live frames and malformed saved data.
- Applying just this one-line change to a clean upstream source snapshot passes all six tests. This does not establish repaired live trajectory collection or new physical movement.

### Property-result synchronization and error-code filtering (one PR)

- Original `Device._handle_recv` updates only its own cache, not the status topic used by HACS subscribers. A successful repeated value also cannot resync a stale subscriber.
- The same loop accepts values with failed or missing success codes, potentially replacing a fresh cache with a failed reply's value.
- Tests reproduce both defects on original source; keep pending command results intact and exclude map service 6 from ordinary status injection.
- Extract only the receive-handler hunk and `tests/test_state_sync.py`, not X40 web-control helpers or model defaults.

### Stored-map wrapper / restart-time missing UID (one PR or separate small PRs)

- Original `Web._decode_map_text` rejects any text starting with `{`, including a valid stored `mapstr` map-list wrapper. Original API reads that wrapper, but Web cannot render it.
- Original `Web._load_map` looks only under the current UID path. When UID is empty after reconnect, an existing same-device map under a known cache directory is not found.
- `tests/test_web_map.py` reproduces both on original code and checks malformed frames, URL-safe input, unrelated devices, ambiguous accounts and known-but-different UIDs.
- UID fallback is a narrowly scoped reconnect usability change: only with absent UID and exactly one exact-DID cache candidate; never select another device, cross a known UID or write identity/map data. Maintainer may prefer a different reconnect policy.

### Shared web-control correctness (small independent hunks)

- **Return button:** upstream HTML sends `return_to_base`, but `Device.action` only looks up uppercased mapping names and there is `CHARGE`, not `RETURN_TO_BASE`. Original handler returns HTTP 500; regression uses an intercepted send and a synthetic non-X40 model.
- **Quiet zero:** original room cleaning uses `get("SUCTION_LEVEL") or 2`, turning valid 0 into 2. The non-X40 regression records 2 instead of 0; preserve explicit 0 and handle missing values separately.
- **Rejected action:** original `Web.command` returns `ok:true` even for a robot result `{"code":-1}`. The non-X40 regression reproduces this. Original room-edit paths already check some result codes; do not claim the entire original API ignores all errors.
- **Time units:** original Web `cleaning_time+' s'` conflicts with HACS v2.0.2's CLEANING_TIME unit MINUTES. Change the original-language unit only in an upstream bugfix, not the entire page language. Compatibility UI has a minute-label regression.
- `tests/test_common_web_controls.py` establishes these control defects independently of the r2401 helpers (except the separate source/unit comparison).

## Compatibility improvement, not a proven universal failure

Original zone cleaning sends four coordinates only. HACS sends coordinates plus
repeat/fan/water. The non-X40 intercepted-payload regression demonstrates the
format difference, and the compatibility branch now sends all seven fields.
However, we have not shown that every original firmware rejects the shorter
form. Propose as explicit-setting/HACS compatibility work, not as a universally
broken-zone-cleaning claim. Keep X40 fine-wetness decoding out of that common PR.

## Exclude from shared-bugfix PRs

- X40 packed mode encoding, wetness level/ranges and wash-interval constraints: model/capability-scoped support; separate minimal model-profile work and disclose missing new physical-sequence validation.
- Korean localization, default root status page, personal sidebar/ports/region, deployment wiring and receipts.
- Our earlier Ingress `//rooms` mistake: introduced by our sidebar configuration, not demonstrated as an original upstream defect.
- Live trajectory/history completeness, all-zero consumables and later HACS certificate validation: unresolved or separate investigations, not proven repaired by these changes.

## Reproduction and evidence

```sh
# Only synthetic source snapshots/fixtures; no certificate, credential,
# robot identity, real map, or operational device connection is loaded.
uv run --python 3.12 --with aiohttp python analysis/verify_upstream_common_bugs.py
uv run --python 3.12 --with aiohttp python analysis/verify_upstream_common_bugs.py --angle-fix-only
uv run --python 3.12 --with aiohttp python -m unittest discover -s tests -v
```

The original-source report intentionally records failing fixed-behavior
assertions; it is not a passing upstream suite. Its 28 test methods produce
22 failed assertions including angle subtests, zero execution errors and zero
skips. The one-line-only upstream angle snapshot passes six tests. Full
compatibility source passes 54 tests locally. Raw synthetic reports are
`analysis/upstream-common-evidence.json` and
`analysis/upstream-angle-fix-evidence.json`.

Recommended submission order: the isolated angle fix; receive/status sync;
stored-map parsing/reconnect; independent common Web corrections. Build each
from fresh upstream main, preserve the original UI language/defaults, and run
its reproduction on the exact proposed PR head. Do not submit the compatibility
branch wholesale. No upstream PR/issue/comment or release has been published
by this analysis.
