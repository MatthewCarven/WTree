# WTree TODO

## Implementation skeleton — DONE 2026-05-20

Scaffold a runnable Python project shell. Bones to grow on.

- [x] Decide project layout: `wtree/` package under a top-level repo with `pyproject.toml`, `README.md`, `tests/`.
- [x] Write `pyproject.toml` — Python ≥3.10, Textual >=0.85, `console_scripts` entry, pytest+pytest-asyncio.
- [x] `wtree/sources/base.py` — `EntrySource` ABC, `Entry`, `Kind`, `ScanError`, `ScanResult`, `SourceCapability`.
- [x] `wtree/sources/native.py` — `NativeSource` via `os.scandir`.
- [x] `wtree/sources/mock.py` — `MockSource` for tests.
- [x] `wtree/app.py` — minimal Textual `App` skeleton.
- [x] `tests/test_native_source.py` — five tests.
- [x] Verify install + launch + smoke test.

## After the skeleton runs

- [x] Wire the tree pane to a real path via NativeSource. **DONE 2026-05-20** — `TreePane`.
- [x] Wire the contents pane to follow the tree pane's cursor. **DONE 2026-05-20** — `ContentsPane`.
- [x] Implement tagged-set state. **DONE 2026-05-20** — `wtree/tagged_set.py`.
- [x] Bind navigation keys. **DONE 2026-05-20** — 10 pilot tests.
- [x] **Bind file ops one at a time, starting with Copy (`C` / F5). DONE 2026-05-21.** Full chain: Selection rule → modal prompt → planner → queue → execute → bytes land.
- [x] **Status line and F-key bar at the bottom of the screen (MC-style). DONE 2026-05-21.** `wtree/widgets/status_line.py` + `wtree/widgets/keybar.py`. `_WIRED` is now `{2, 5, 6, 8, 10}` after Rename landed.
- [x] **Bind Move (`M` / F6). DONE 2026-05-21.** `wtree/ops/move.py`. One PlanItem per top-level tag. `shutil.move` (rename fast-path; copy+delete cross-fs).
- [x] **Bind Delete (`D` / Del / F8). DONE 2026-05-21.** `wtree/widgets/confirm.py` (new ConfirmDialog), `wtree/ops/delete.py`. `_native_delete` uses `shutil.rmtree` for dirs, `os.unlink` for files/symlinks.
- [x] **Bind Rename (`R` / F2). DONE 2026-05-21.** `wtree/ops/rename.py` — single-entry only per design.md Selection rule; rejects when tagged set non-empty with notify nudge. Reuses `PromptDialog` (default initial = current basename). Planner rejects path separators in new name (`InvalidName`), empty / whitespace names, and no-change (same-as-current). `_native_rename` uses `os.rename` with pre-check on `lexists` to refuse silent clobber. KeyBar `_WIRED` now `{2, 5, 6, 8, 10}`. 31 new tests (planner 13 + executor 6 + e2e 5 + pilot 7). **159/159 green**.
- [ ] Wire `/` incremental search in the focused pane.
- [ ] Bind View (`V` / F3) — built-in pager. New widget.
- [ ] Bind Edit (`E` / F4) — shell out to `$VISUAL` / `$EDITOR`. Need to suspend Textual rendering during the subprocess.
- [ ] Bind Make-new (`N` / F7) — dir or file sub-prompt then create.
- [ ] Bind menu bar (`F9`) — MC-style top menu.

## Open design questions to revisit before code

None blocking. If anything surfaces during implementation that contradicts `design.md`, update the doc first, then code.

## Skeleton-era follow-ups (small, not blocking next steps)

- [ ] Decide on cross-platform owner lookup story.
- [ ] Re-export `NativeSource`/`MockSource` from `wtree.sources.__init__`.
- [ ] Consider a `wtree.__main__` so `python -m wtree` works.

## Tree+Contents-era follow-ups

- [ ] Symlinks in the tree (currently dir-only expansion).
- [ ] Visual styling for error-leaves.
- [ ] Re-collapse + re-expand doesn't re-scan; needs `Ctrl+R` refresh story.

## Tagged-set-era follow-ups

- [ ] Tag-by-pattern (`+` / `-`) and tag-all-in-dir (`Ctrl+A`) — reuse `PromptDialog`.
- [ ] Tagging from the tree pane.
- [ ] Visual style for tagged rows.
- [ ] `Ctrl+I` properties dialog. Reads tagged-set count if non-empty, else cursor entry, else `app.last_result.summary()`.

## Navigation-era follow-ups

- [ ] `ContentsPane._tree()` does `self.app.query_one(TreePane)` — needs explicit ref if dual-pane mode lands.
- [ ] Tree-pane tagging → revisit Backspace there.
- [ ] No status-line feedback yet for "→ on a file row is a no-op" — now that StatusLine exists, wire this.
- [ ] `focus_dir_under_cursor` returns False silently if child isn't found.

## Ops/queue-era follow-ups

- [ ] **Progress dialog.** StatusLine now shows per-item counts; for big copies/moves/deletes a full ModalScreen progress dialog with cancel button would be friendlier. Wire via `on_item_progress`.
- [ ] **Minimize / resume.** Queue is already independent of any UI; dialog-side concern only.
- [ ] **Conflict detection at plan time.** Pre-stat destinations; tag PlanItem with overwrite/skip/rename. Surface in modal. Move and Rename already do a runtime `lexists` pre-check; plan-time is friendlier.
- [ ] **Cross-platform `dst_path` normalisation.** Executor calls `os.path.normpath` on Windows; cross-source pairs will need explicit translation.
- [ ] **Cancel a running plan.** `OperationQueue.stop()` cancels the worker; mid-plan cancellation needs a token in `apply_plan`.
- [ ] **`Plan.apply` shorthand** vs free function.
- [ ] **Persist queue state** for crash recovery.

## Modal-era follow-ups

- [ ] **Validation on Enter** — pre-check parent dir exists/writable (Copy/Move).
- [ ] **Focus restoration** on dismiss — spot-check.
- [ ] **Path completion** inside `PromptDialog`.
- [ ] **History / MRU destinations** — Up-arrow to cycle.

## Status-line-era follow-ups

- [ ] **KeyBar `_WIRED` lookup.** Currently a frozenset literal at module level. As bindings land, this needs to update — could read from `WTreeApp.BINDINGS` at runtime instead, dimming labels whose key isn't bound.
- [ ] **Status line on focus changes.** Currently refreshes on cursor move, tagging, queue events. Tab between panes also calls `_refresh_status`. Verify this feels right when more bindings have status messages.
- [ ] **Transient toasts vs persistent status.** Errors go through `self.notify()` (Textual's toast layer). Status line is reserved for persistent state. Could revisit if the notify toasts feel intrusive — Rename's rejection ("rename works on one entry; clear tags first") is a notify today; design.md called for a status-line nudge specifically.
- [ ] **StatusLine.refresh_from(app) reads `os.stat` on every cursor move.** Currently O(1) but on slow filesystems (network shares) this is per-keystroke I/O. Cache the stat on `ContentsPane._row_paths` and reuse.
- [ ] **F-key bar background.** Currently `$panel`; MC's classic is cyan. A future theme pass can offer the green-on-black XTree palette and the cyan MC palette as alternatives.

## Move-era follow-ups

- [ ] **Pane auto-refresh after a move.** Until FS-watching lands (parking lot), an explicit "refresh after my own writes" hook on `OperationResult` completion would feel snappier. `test_e2e_two_moves_serialize` and the equivalent delete test work around this with explicit Down presses.
- [ ] **Move summary undercounts for big dirs.** `plan_move` emits one item per top-level tag. UI says "1 dir, 4 KB" when the actual move is "1 dir, 12 GB". Optional: walk for accounting (size only), still emit one PlanItem per tag for execute.
- [ ] **Cross-fs symlink moves dereference.** `shutil.move`'s copy-fallback uses `copy2` which follows symlinks. Same-fs `os.rename` preserves the link itself.
- [ ] **Overwrite policy.** Copy clobbers (via `shutil.copy2`); Move and Rename pre-check and fail. Unify under a plan-time overwrite/skip/rename prompt once the modal infra grows.

## Delete-era follow-ups

- [ ] **Pane auto-refresh after a delete.** Same situation as Move — share the post-op refresh hook when it lands.
- [ ] **Delete summary undercount.** Same as Move.
- [ ] **ConfirmDialog "show all paths" toggle** for huge tagged sets (v0 caps preview at 5 lines + ellipsis).
- [ ] **`shutil.rmtree(ignore_errors=False)` partial-failure attribution.** No per-file detail when rmtree fails mid-tree; surfacing requires a future progress dialog with streaming.
- [ ] **Soft-delete (trash).** XTree had Y for trash vs D for delete; v0 only does hard delete. `send2trash` integration is parking-lot.
- [ ] **Suppress confirm when tagged set is empty + cursor is a file?** Norton/XTree muscle memory; v0 always confirms.

## Rename-era follow-ups

- [ ] **Status-line nudge instead of notify toast for the tagged-set rejection.** design.md spec: "rejected with a status-line nudge". v0 uses `notify()` because the StatusLine doesn't yet have a transient-message API. Add a `StatusLine.flash(message, timeout=2.0)` API and route the Rename rejection (and any future "this op rejected because…") through it instead of the notify toast.
- [ ] **Smart cursor placement in the rename modal.** Default is the current basename; cursor is at end. Better default: select the basename-without-extension portion, so on `report.txt` the user can type a replacement name and Enter keeps `.txt`. Mirrors Finder / Explorer behaviour.
- [ ] **Batch rename — design.md parking lot.** Multiple tagged entries, glob/regex/numbering rules. Once the planner generalises, the action layer's "single-entry only" guard goes away.
- [ ] **Case-only renames on case-insensitive filesystems** (macOS HFS+ default, Windows NTFS by default). `report.txt` -> `Report.txt` may need an intermediate step (`os.rename` to a temp name then to the target) on those systems. Not tested today; document as known-soft-spot.
- [ ] **Reserved names on Windows.** `CON`, `NUL`, `PRN`, `LPT1`, etc. would silently fail at the OS layer. Add a planner-level check so the user sees an `InvalidName` error with a clear message instead of a cryptic Windows error.

## Backlog — parking lot

See `design.md` parking lot section. Summary: inline editor, archive sources, remote/SFTP, SMB discovery, FS watching, bookmarks/history, batch rename, themes.

## Notes for the next session

- Read `design.md` and this `todo.md` before doing anything.
- Project folder write rules still apply — stage in sandbox, verify size, atomic-rename on mismatch.
- **Cowork `Write` tool truncates at ~3-4 KB.** Use heredoc.
- **Mount bit-rot is real between bash and file tools.** Heredoc-rewrite is the default protocol after any non-trivial Edit, not a recovery step.
- **`@work` on async actions** for `push_screen_wait`.
- **Pilot `press()` is slow.** Set `Input.value` directly for typing long strings.
- **`OperationQueue` callbacks run inline on the worker task.** UI mutation works since Textual is single-loop.
- **`app.query_one(Widget)` searches the current screen.** Inside a modal, this misses widgets on the main screen — cache widget references before pushing a screen.
- **Captures outside `async with app.run_test()` fail.** The app is unmounted at block exit. Snapshot anything you'll assert on while still inside the block.
- **Three action helpers now.** `_plan_modal_enqueue` (Copy/Move - typed destination), `_plan_confirm_enqueue` (Delete - yes/no), and `action_rename` (inline — single-entry, basename-only). All call into `_finalise_plan` for the post-planner tail.
- **Contents pane doesn't auto-refresh after operations write to its directory.** Tests around move/delete/rename either explicitly move cursor or `await contents.show_path(contents.current_path)`. FS-watching is parking-lot.
- **`_WIRED` set assertions in old keybar tests need updating** when new F-keys are wired. The "not in _WIRED" assertion is a tripwire that fires when a key joins the set — caught Rename adding F2, fix is to drop the stale negative assertion.
- Update `worklog.md` at the end of each session with a new dated section.
