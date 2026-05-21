# v3 Rewrite Smoke Baseline (ASE)

Date: 2026-05-21
Branch: `python-parser-rewrite`
Save under test: `references/map_dumps/evolved/theisland/TheIsland.ark` (ASE)

## Counts

| Dataset | Rows |
|---|---|
| tamed | 764 |
| wild | 27,893 |
| players | 1,972 |
| tribes | 306 |
| tribelogs | 306 |
| structures | 31,829 |
| mapstructures | 33 |
| cluster | 0 (no cluster_dir configured) |

## Timing

- WorldSave + 1972 profiles + 306 tribes + `export_all`: ~44s.
- Profile/tribe sidecar parse dominates (~21s sequential).
- DB write phase (staging swap × 7 + meta): < 2s.

## Memory (RSS, in-process)

| Phase | RSS |
|---|---|
| Idle (interpreter + imports) | 26 MB |
| Post-ingest peak | 1,068 MB |
| After 20× /data/tamed scrapes | 988 MB (delta +34 MB during scrapes) |

RSS does not grow per request after the initial parse. The ~1 GB ceiling is the WorldSave graph + export dict held during ingest; Python's allocator keeps freed pages but the live set is bounded by save size, not by request count.

## HTTP semantics verified

- DB empty → `/data/tamed` returns `503` + `Retry-After: 30`. ✓
- Fresh ingest → `/data/tamed` returns `200`, no `X-Arkviewer-Stale` header. ✓
- `meta.last_parse_at` forced to 7h ago → `200` + `X-Arkviewer-Stale: true` + ISO `X-Arkviewer-Last-Parse`. ✓
- `/data/cluster` returns `200` even when empty (router precedence fixed). ✓
- All filter routes return `200` with expected filtering. ✓

## Deferred to user manual verification

These checks were not automated; run on the production ASA host when convenient.

1. **ASA smoke**: Repeat against an ASA SQLite save (`/home/pokuser/asa/Instance_pve-island/...`). Confirm cryo entries land in `tamed` with `cryo=1`.
2. **Restart-resilience**: Run process, ingest succeeds, hard-kill. Restart with same config. Confirm `/data/tamed` returns the previous-parse rows before the next reparse completes.
3. **Cooldown effectiveness**: Touch a `.arkprofile` file 30 times in a minute (`(Get-Item profile.arkprofile).LastWriteTime = Get-Date` loop). Confirm the log shows ≤ 2 `ingest_profile` events (30s cooldown floor).
4. **Stability gate**: Truncate-write a `.ark` file in chunks while watcher is running. Confirm no `truncated read` / `CorruptDataError` lines in the log — `wait_for_stable` should hold off until writes settle.
5. **Long-run RSS**: 30-min wall-clock with periodic touch of the `.ark` file. RSS should oscillate within ~50% of steady-state, no upward trend.

## Known follow-up

- Profile/tribe sidecar loading is sequential (~21s for 1972 files on TheIsland). Legacy v3 parallelised this via `asyncio.to_thread` fan-out for a ~6× speedup. Worth porting if production ASA shows ingest > 60s, otherwise YAGNI for now.
- ASA cryopod blocks are partially decoded by arkparser (see arkparser README "Known limitation: ASA cryopod property blocks are partially decoded"). ASA cryo records will have `tribeid=0`, `tamer=""`, `dinoid="0"`, `imprint=0.0`. Not a v3 regression — same in legacy parity exports.
