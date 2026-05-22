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
- [x] **Wire `/` incremental search in the focused pane. DONE 2026-05-22 (later).** New `wtree/widgets/search_bar.py` — `SearchBar(Widget)` (subclassed Widget + render() instead of Static + update() to skip the Visual pipeline that bit us); reactive `query`, `match_total`, `match_idx`; custom `on_key` for letters/Backspace/Esc/Enter/Up/Down/Ctrl+G; posts QueryChanged/NextMatch/PrevMatch/Committed/Cancelled messages. New `SearchTarget` protocol on `ContentsPane` (yields `(row, basename)`) and `TreePane` (depth-first walk of visible nodes, yields `(line_index, label)`; collapsed subtrees skipped). `WTreeApp.action_search` captures focused pane + cursor, hides StatusLine, activates bar; the `on_search_bar_*` handlers compute substring-CI matches against `iter_searchable()`, jump cursor via `set_search_cursor`, step with wrap on Next/Prev, restore on Cancel, leave-in-place on Commit. 15 new tests. **268/268 green**.
- [x] **Bind View (`V` / F3) — built-in pager. DONE 2026-05-21.** New `wtree/widgets/viewer.py` (`ViewerScreen(ModalScreen[None])`). Loads via `asyncio.to_thread` so big-file reads don't block the UI; UTF-8 with latin-1 fallback (latin-1 has total decoding so the viewer never crashes); binary refusal via NUL-byte scan of first 8 KB; 10 MB size ceiling with `$PAGER` nudge. Esc / Q dismiss. Header shows path + size + encoding. Scroll handled by `VerticalScroll` (arrows / PgUp PgDn / Home End all just work). `action_view` is sync (push_screen, not push_screen_wait) and validates the cursor kind — DIR rejects with "press Enter" nudge, OTHER rejects with kind name. KeyBar `_WIRED` now `{2, 3, 5, 6, 8, 10}`. 17 new tests (viewer 11 + e2e 5 + keybar 1). **179/179 green**.
- [x] **Bind Edit (`E` / F4) — shell out to `$VISUAL` / `$EDITOR`. DONE 2026-05-22.** New top-level `wtree/editor.py` (not `ops/` — Edit is a UI shell-out, not a Plan-producing op) with `resolve_editor()` (env precedence + platform default, `shlex`-split) and `launch_editor_blocking(argv, path)` (subprocess.run passthrough, returns exit code, raises FileNotFoundError when binary is missing). `action_edit` in `app.py` is `@work` so it can `await asyncio.to_thread(...)` on the blocking spawner. Kind validation mirrors `action_view`: cursor-only (no Selection rule per the View precedent — multiple-file editor semantics vary too much for v0); DIR/OTHER rejected with notify. The `with self.suspend(): ...` block is factored into `_launch_editor_blocking` so tests can monkeypatch it (the headless test driver inherits `can_suspend=False` and would raise `SuspendNotSupported`). After the editor returns the action `await contents.show_path(contents.current_path)` to pick up any on-disk change. Non-zero exit and `FileNotFoundError` are both caught and surfaced as notify; nothing propagates to the action loop. KeyBar `_WIRED` is now `{2, 3, 4, 5, 6, 8, 10}`. 20 new tests (editor unit 12 + e2e 7 + keybar 1). **199/199 green**.
- [x] **Bind Make-new (`N` / F7) — dir or file sub-prompt then create. DONE 2026-05-22.** New `wtree/ops/make_new.py` planner (lenient on separators — `foo/bar/baz` allowed, creates intermediates; rejects empty / absolute / `..` / pre-existing leaf), new `wtree/widgets/kind_chooser.py` modal (D/F/Esc), new executor branch in `wtree/ops/execute.py` (`_native_make_new` + `_make_new_blocking` — `os.makedirs(exist_ok=False)` for DIR, `os.makedirs(parent, exist_ok=True)` + `open(leaf, "x")` for FILE). `action_make_new` is `@work` (two `push_screen_wait` modals in sequence). Parent dir is `ContentsPane.current_path`; tagged set silently ignored (mirrors View/Edit). KeyBar `_WIRED` now `{2, 3, 4, 5, 6, 7, 8, 10}`. 46 new tests (planner 20 + executor 10 + e2e 7 + keybar 1 + chooser+prompt integration 8). **245/245 green**.
- [x] **Left-on-root ascend — re-root the tree at the parent dir. DONE 2026-05-22 (late).** XTree "widen the logged window" idiom. New `TreePane.AscendRequested` message, `TreePane.on_key` intercepts Left only when cursor is on the root (Textual's default Left collapses on every other node), new `TreePane.re_root(path)` (wipes children, resets root data + label, clears `_loaded` memo, repopulates) and `TreePane.focus_child_of_root(path)` (lands cursor on the old-root row; yields once via `asyncio.sleep(0)` so Textual's line indexer rebuilds before the cursor-line read). New `WTreeApp.on_tree_pane_ascend_requested` handler: computes `os.path.dirname(root)`, no-ops with notify nudge at filesystem root (`new_root == old_root`), else re-roots and focuses old root row. Cursor-driven NodeHighlighted keeps the contents pane on the old root (working context stable). Tags survive (absolute paths). Notify says "Logged: NEW (ascended from OLD)". 8 new tests. **253/253 green**.
- [x] **`StatusLine.flash()` + pane auto-refresh after ops. DONE 2026-05-22 (last).** Two-tier feedback API split: `StatusLine.flash(msg, timeout=3.0)` for user-immediate nudges ("X rejected", "X cancelled", "Logged: NEW (ascended)"), `App.notify()` retained for queue-completion toasts that may fire async. `App.flash()` is the convenience wrapper. Flash replaces any active flash (cancel + restart timer), holds through `refresh_from()` so cursor moves don't clobber it. Pane auto-refresh: `_on_plan_complete` schedules `asyncio.create_task(self._refresh_panes_after_op())` which re-shows contents pane's `current_path` whether the op succeeded or partially failed. Tree-pane auto-refresh parked. 12 new tests (flash unit + flash integration via Rename/Ascend + auto-refresh e2e for Make-new/Delete + survival-after-current-path-deleted). **280/280 green**.
- [ ] Bind menu bar (`F9`) — MC-style top menu. **Last v0 item.**

## Open design questions to revisit before code

None blocking. If anything surfaces during implementation that contradicts `design.md`, update the doc first, then code.

## Skeleton-era follow-ups (small, not blocking next steps)

- [ ] Decide on cross-platform owner lookup story.
- [x] Re-export `NativeSource`/`MockSource` from `wtree.sources.__init__`. **DONE 2026-05-21** — both classes plus the existing base types listed in `__all__`.
- [x] Consider a `wtree.__main__` so `python -m wtree` works. **DONE 2026-05-21** — one-line module delegating to `wtree.app.main`; mirror of the `wtree` console-script entry. New `tests/test_packaging.py` (3 tests) covers both re-exports and the `__main__` shim.

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

- [x] **Pane auto-refresh after a move. DONE 2026-05-22 (last).** `_on_plan_complete` fires `asyncio.create_task(self._refresh_panes_after_op())`; re-shows contents pane's current_path. Wrapped in try/except so a refresh failure doesn't crash the queue worker. Tree-pane refresh parked. Until FS-watching lands (parking lot), an explicit "refresh after my own writes" hook on `OperationResult` completion would feel snappier. `test_e2e_two_moves_serialize` and the equivalent delete test work around this with explicit Down presses.
- [ ] **Move summary undercounts for big dirs.** `plan_move` emits one item per top-level tag. UI says "1 dir, 4 KB" when the actual move is "1 dir, 12 GB". Optional: walk for accounting (size only), still emit one PlanItem per tag for execute.
- [ ] **Cross-fs symlink moves dereference.** `shutil.move`'s copy-fallback uses `copy2` which follows symlinks. Same-fs `os.rename` preserves the link itself.
- [ ] **Overwrite policy.** Copy clobbers (via `shutil.copy2`); Move and Rename pre-check and fail. Unify under a plan-time overwrite/skip/rename prompt once the modal infra grows.

## Delete-era follow-ups

- [x] **Pane auto-refresh after a delete. DONE 2026-05-22 (last).** Shared `_refresh_panes_after_op` hook on `_on_plan_complete`. Same situation as Move — share the post-op refresh hook when it lands.
- [ ] **Delete summary undercount.** Same as Move.
- [ ] **ConfirmDialog "show all paths" toggle** for huge tagged sets (v0 caps preview at 5 lines + ellipsis).
- [ ] **`shutil.rmtree(ignore_errors=False)` partial-failure attribution.** No per-file detail when rmtree fails mid-tree; surfacing requires a future progress dialog with streaming.
- [ ] **Soft-delete (trash).** XTree had Y for trash vs D for delete; v0 only does hard delete. `send2trash` integration is parking-lot.
- [ ] **Suppress confirm when tagged set is empty + cursor is a file?** Norton/XTree muscle memory; v0 always confirms.

## Rename-era follow-ups

- [x] **Status-line nudge instead of notify toast for the tagged-set rejection. DONE 2026-05-22 (last).** Routed through `app.flash()` -> `StatusLine.flash()`. design.md spec: "rejected with a status-line nudge". v0 uses `notify()` because the StatusLine doesn't yet have a transient-message API. Add a `StatusLine.flash(message, timeout=2.0)` API and route the Rename rejection (and any future "this op rejected because…") through it instead of the notify toast.
- [ ] **Smart cursor placement in the rename modal.** Default is the current basename; cursor is at end. Better default: select the basename-without-extension portion, so on `report.txt` the user can type a replacement name and Enter keeps `.txt`. Mirrors Finder / Explorer behaviour.
- [ ] **Batch rename — design.md parking lot.** Multiple tagged entries, glob/regex/numbering rules. Once the planner generalises, the action layer's "single-entry only" guard goes away.
- [ ] **Case-only renames on case-insensitive filesystems** (macOS HFS+ default, Windows NTFS by default). `report.txt` -> `Report.txt` may need an intermediate step (`os.rename` to a temp name then to the target) on those systems. Not tested today; document as known-soft-spot.
- [ ] **Reserved names on Windows.** `CON`, `NUL`, `PRN`, `LPT1`, etc. would silently fail at the OS layer. Add a planner-level check so the user sees an `InvalidName` error with a clear message instead of a cryptic Windows error.

## View-era follow-ups

- [ ] **In-viewer incremental search** (`/`). Local to the viewer; reuses the modal-input pattern from `PromptDialog` or a non-modal inline form. Highlight matches; `n` / `N` to step through.
- [ ] **Syntax highlighting.** Would require switching the body widget from `Static` to Textual's `TextArea` (read-only mode) or a `Syntax` rich renderable. Adds a dependency on Pygments and noticeable load latency for big files. Worth a dedicated session.
- [ ] **Line-number gutter.** Trivial visual win once `Static` is swapped for `TextArea`.
- [ ] **Streamed / paged read for huge files.** v0 refuses anything over 10 MB; a friendlier behaviour would be "load the first N lines, lazy-load on scroll". Needs Textual's `LazyList` or equivalent.
- [ ] **Hex mode for binary files.** Today's binary detection refuses with a polite message; an opt-in hex dump (16 bytes per row, ASCII gutter) would be more useful than the refusal.
- [ ] **Detect ``BOM`` / shebang encoding hints.** UTF-8 with BOM decodes fine via `utf-8-sig`; we could try that before falling back to latin-1.
- [ ] **Configurable size ceiling.** `MAX_BYTES` is a module-level constant; should be a runtime setting once a config layer exists.
- [ ] **Symlink loop / dangling target.** `os.stat` follows the symlink; a dangling link surfaces as "could not stat". Worth a friendlier message ("symlink target missing: ...").

## Edit-era follow-ups

- [x] **Status-line nudge instead of notify toast for the "editor not found" / "non-zero exit" / "spawn errored" cases. DONE 2026-05-22 (last).** All three routed through `app.flash()`. Same shape as the Rename-era follow-up; route through a future `StatusLine.flash(message, timeout)` API rather than the intrusive notify-toast layer.
- [ ] **Focus restoration after the editor returns.** `action_edit` does `show_path(current_path)` to refresh the pane but doesn't explicitly re-focus the pane that had focus before. Spot-check whether Textual restores focus implicitly through `app.suspend()`; if not, capture `self.focused` before suspend and restore after.
- [ ] **Tagged-set Edit semantics.** v0 silently ignores the tagged set (mirrors View). Design.md Selection rule says it should apply. Revisit post-v0 once `$EDITOR` policy is configurable — likely allow batch invocation for editors that handle multiple file args sensibly (vim tabs, emacs frames) and reject for the rest.
- [ ] **Don't fall back to `vi` if the user explicitly cleared `$EDITOR`.** Today `EDITOR=""` falls through to the platform default. Some users set `EDITOR=""` deliberately to mean "I'd rather see an error than be dropped into a stranger editor". Add a sentinel: explicit empty env -> error.
- [ ] **`$VISUAL`/`$EDITOR` change detection.** Resolved once per action call from `os.environ`. If the user changes `$EDITOR` from a `!` shell-out mid-session, we honour it on the next press. That's correct today but worth a test once `!` lands.
- [ ] **Honor `$PAGER`-style overrides for sub-actions.** Right now `View` mentions `$PAGER` in its size-limit refusal but doesn't actually invoke it. Once Edit's shell-out machinery is reusable, wire View's "too big" refusal to a "press something to invoke `$PAGER`" path that re-uses `_launch_editor_blocking` shape.
- [ ] **Windows `notepad.exe` blocking semantics.** Default notepad opens and returns the subprocess exit code only when the user closes the window. Confirm this matches user expectation; if not, document `notepad++` / `code --wait` as recommended.
- [ ] **`code --wait` ergonomics.** VS Code as `$EDITOR` is common but trips up users who forget `--wait`; without it the subprocess returns instantly and our pane refreshes before any edit happens. Either detect bare `code` and prepend `--wait`, or document loudly. (Documentation is the lighter touch.)

## Make-new-era follow-ups

- [x] **Status-line nudge instead of notify toast for the "Exists" / "InvalidName" / "InvalidKind" rejections. DONE 2026-05-22 (last).** Routed through `app.flash()`. Same shape as the Rename/Edit-era follow-ups; route through the future `StatusLine.flash(message, timeout)` API.
- [x] **Pane auto-refresh after a make. DONE 2026-05-22 (last).** Shared post-op refresh hook -- see below. Same situation as Copy/Move/Delete — the new leaf doesn't appear in the contents pane until something else triggers `show_path`. Share the post-op refresh hook when it lands.
- [ ] **Pre-position the cursor on the new entry** after the pane refresh. UX win — make-new-then-cursor-on-it lets the user immediately rename or open the freshly-made entry.
- [ ] **Initial name suggestion** in the PromptDialog (e.g. `New Folder` / `untitled.txt`). Currently the prompt opens empty. Mirror Finder / Explorer's affordance.
- [ ] **Symlink creation.** v0 rejects `Kind.SYMLINK` with `InvalidKind`. A future variant could prompt for the link target too — distinct UX shape, parking-lot.
- [ ] **Tagged-set "copy template" semantics.** If a single file is tagged, Make-new could optionally seed the new file with that file's contents (an XTree-ish "duplicate as" idiom). Today the tagged set is silently ignored.
- [ ] **Honour the parent's umask vs explicit mode.** `open(path, "x")` lands with the process umask; `os.makedirs` ditto. Not a v0 issue but worth flagging — XTree had no concept of POSIX modes.
- [ ] **Case-only collisions on case-insensitive FS.** Like Rename's known soft spot — making `Readme.md` next to `readme.md` on case-insensitive NTFS / HFS+ may succeed silently or fail confusingly depending on the filesystem.

## Ascend-era follow-ups

- [ ] **Backspace on the tree pane = ascend.** Parallel binding for the Left-on-root gesture, mirroring the contents pane's Backspace ("go to parent dir"). Matthew flagged this as wanted in the 2026-05-22 design conversation but parked for now. Distinct from the existing `action_focus_parent` (cursor-to-parent inside the tree) — would need to disambiguate: if cursor on root, ascend; otherwise cursor-to-parent.
- [ ] **Blank-Enter in the `L` "Log new source" prompt = ascend.** Discoverability layer: when the user is already in the log-a-path modal and just presses Enter without typing anything, treat that as "log the parent of the current root". Also flagged 2026-05-22 as wanted but parked. Depends on `L` itself being wired (it's still in the unbound part of `_WIRED`).
- [ ] **Preserve expansion state across ascend.** v0 wipes the tree and repopulates — any subtrees the user had expanded under the previous root are collapsed after re-root. A pricier "graft old tree state under the new root node" pass would keep the user's drilled-down context. Implementation sketch: snapshot `(path, is_expanded)` tuples by walking the old tree, then on re-root replay the expansions where paths still exist as children.
- [x] **`StatusLine.flash(message, timeout=3.0)` API for ascend feedback. DONE 2026-05-22 (last).** Ascend's "Logged: NEW (ascended from OLD)" and "Already at filesystem root" both go through `app.flash()`. Currently the "Logged: X (ascended from Y)" message goes through `notify()` (toast). A status-line flash would be a less intrusive fit and matches the same follow-up flagged for Rename / Edit / Make-new rejections.
- [ ] **Passive folder-change detection with idle debounce.** Matthew's idea 2026-05-22: when the user hasn't pressed a key in N seconds (e.g. 5-10), the app does a cheap comparison of the displayed directory's current contents vs the cached list and surfaces a status nudge if they've drifted ("contents changed on disk — Ctrl+R to refresh"). Bound the overhead: bail without doing anything for directories above a size threshold (say 1000 entries), wait at least 10 seconds between checks, only check the contents pane's current_path (not the whole tree). Predecessor of full FS-watching (parking lot) but doesn't require `inotify`/`FSEvents`/`ReadDirectoryChangesW`. Wire to the existing idle-detection that Textual offers, or a simple `asyncio.sleep` loop.
- [ ] **UNC path ascend.** `os.path.dirname("\\\\server\\share")` returns `\\\\server`, which on Windows is a server-level enumeration that requires SMB browsing (parking lot). For v0 the no-parent check (`new_root == old_root`) catches the share root naturally on most paths; needs a spot-check on real Windows.
- [ ] **Symlink at root: realpath or as-is?** Currently the planner uses the *literal* parent (no realpath resolve). If the user logged a symlink (e.g. `/home/me/current` → `/data/projects/foo`), ascending lands at `/home/me/`, not `/data/projects/`. Document this as the intended behaviour and confirm it feels right; if not, add an option.
- [ ] **Cursor position after ascend when source can't enumerate old root.** If the new parent's scan raises a `ScanError` (permission denied, races, etc.) the old-root child node doesn't exist, so `focus_child_of_root` returns False silently and the cursor stays on the new root. Worth a status nudge ("ascended but couldn't find previous root row") in that case.

## Search-era follow-ups

- [ ] **Remembered query for Ctrl+G outside search mode.** Today Ctrl+G only works *inside* an active search session. The keymap reserves Ctrl+G as "Next match" globally — meaning, after committing a search the user should be able to press Ctrl+G later to jump to the next match of the same query. Needs a small `_last_query` slot on the app + a global Ctrl+G binding that re-runs the matcher.
- [ ] **Regex / prefix toggles via syntax prefix.** Current spec parks regex: a future variant where `/^foo` is prefix-only and `/\foo` is regex would layer cleanly on top of the substring default. Inspect the query in `on_search_bar_query_changed` and pick the matcher accordingly.
- [ ] **Auto-expand tree subtrees during search.** v0 only walks visible nodes. A future variant could expand subtrees on-demand when the query has no visible matches. Has to be bounded (don't walk a giant subtree per keystroke) and respect sources that refuse `LogAll`.
- [x] **Status-line flash for "search exited / N matches". DONE 2026-05-22 (last).** `app.flash()` API is now available; routed the immediate-feedback ones, though search itself uses the SearchBar widget rather than the StatusLine so this is mainly about the rejection path ("Search: focus a pane first"). Same `StatusLine.flash` API the Rename / Edit / Make-new / Ascend follow-ups all want — would replace the StatusLine.display toggle we do today with a cleaner "show this transient message, then return".
- [ ] **Highlight matched substrings inside row labels.** Currently the cursor jumps but the row text is unchanged. Bold-highlighting the matched substring (like ranger / fzf) is a UX win.
- [ ] **Search across the tagged set, not just the focused pane.** A power-user mode where `/` finds matches anywhere in the tagged set — different from "find across tree" (`Ctrl+F`) which scans the filesystem.
- [ ] **Find across tree (`Ctrl+F`).** Already in the keymap; the search infrastructure built here (matcher, message bus) is reusable. Distinct from `/` because it walks the full tree rather than just visible nodes.
- [ ] **Empty query restore.** Today, backspacing the query to empty leaves the cursor wherever the last match was. Some users would expect that to restore the cursor to the pre-search position (mid-search "give up" gesture). Worth A/B'ing.
- [ ] **"Other keys cancel and pass through" exit.** Today any non-recognised key is swallowed during search. Vim-style would be: pressing e.g. `j` cancels search AND moves the cursor down. Useful but breaks the "letters are query text" contract; needs design.

## Flash + auto-refresh follow-ups

- [ ] **Severity-styled flash.** Today flash() takes only `message` and `timeout`; warnings and errors render the same. Add a `severity` kwarg that maps to Rich markup wrappers (`[yellow]...[/yellow]` for warning, `[red bold]...[/red bold]` for error). The existing markup-enabled Static should support this without further changes.
- [ ] **Tree-pane auto-refresh.** Contents pane refreshes automatically after ops; tree pane doesn't. The lazy-loaded `_loaded` memo is the obstacle - invalidating per-node requires knowing which paths changed. Cleanest implementation: have planners emit a "touched paths" set on `OperationResult`; the refresh hook walks the tree and invalidates the `_loaded` entries that overlap. Until that lands, the user has to collapse + re-expand a tree node to see its new contents.
- [ ] **Preserve cursor position across auto-refresh.** `show_path()` resets the cursor to row 0. If the user just deleted row 5, they probably want the cursor at row 5 (or thereabouts) after the refresh, not row 0. Need to snapshot cursor position before refresh and try to restore - ideally to the same path if it still exists, else to a sibling.
- [ ] **Flash queue for rapid-fire messages.** If two flashes fire in quick succession (e.g. "Cancelled" + "Cancelled" from a double-tap Esc), the second replaces the first. For some flows it might be nicer to briefly queue: show A for 500ms, then B for the full timeout. Probably YAGNI.
- [ ] **Flash from inside async ops.** The current `_refresh_panes_after_op` swallows exceptions silently. Could flash a status when it fails ("Contents pane refresh failed: <reason>"). Low priority.

## Backlog — parking lot

See `design.md` parking lot section. Summary: inline editor, archive sources, remote/SFTP, SMB discovery, FS watching, bookmarks/history, batch rename, themes.

## Notes for the next session

- Read `design.md` and this `todo.md` before doing anything.
- Project folder write rules still apply — stage in sandbox, verify size, atomic-rename on mismatch.
- **Cowork `Write` tool truncates at ~3-4 KB.** Use heredoc.
- **Mount bit-rot is real between bash and file tools.** Heredoc-rewrite is the default protocol after any non-trivial Edit, not a recovery step. Edit-session 2026-05-22 hit it twice: an app.py mid-statement truncation that Edit reported as success, and a keybar.py change where bash saw the update but Python saw stale bytes. Verify with Python imports or test runs after every non-trivial Edit.
- **`@work` on async actions** for `push_screen_wait` and for `await asyncio.to_thread(...)` (Edit's pattern).
- **Pilot `press()` is slow.** Set `Input.value` directly for typing long strings.
- **`OperationQueue` callbacks run inline on the worker task.** UI mutation works since Textual is single-loop.
- **`app.query_one(Widget)` searches the current screen.** Inside a modal, this misses widgets on the main screen — cache the reference before pushing the modal.
- **`App.suspend()` raises `SuspendNotSupported` under the headless test driver.** Factor any `with self.suspend(): ...` block into a method seam that tests can monkeypatch.
- **Two `push_screen_wait` in sequence works fine.** Make-new chains the chooser modal and the prompt modal back-to-back inside one `@work` action — each `await push_screen_wait(...)` resumes cleanly when the inner modal dismisses.
- **`open(path, "x")` is the right primitive for "make new file".** It's open-for-exclusive-create — raises `FileExistsError` instead of clobbering. Pair with `os.makedirs(parent, exist_ok=True)` for the lenient-intermediate-dirs UX.
- **Make-new's PlanItem mirrors src and dst.** No "from" path exists, but the executor's dispatch table is keyed on `(src_source_id, dst_source_id)`. Setting both to the same id keeps the dispatch logic uniform without adding a "destinationless" sentinel concept.
- **Re-rooting an in-place Textual Tree needs a yield before reading `child.line`.** Textual's line indexer rebuilds lazily on next render — without `await asyncio.sleep(0)` after `expand()` + repopulate, freshly-added child nodes report `line == -1`, and assigning `cursor_line = -1` deselects rather than moves. The `focus_child_of_root` pattern uses one `asyncio.sleep(0)` to yield once, which is enough.
- **Cursor-driven NodeHighlighted is the right path to update the contents pane.** Don't `show_path(new_root)` explicitly after re-root — it races with the NodeHighlighted handler triggered by `cursor_line = child.line`. Let the cursor move drive the pane refresh; the result is "contents follow the tree cursor", which is also what the existing tree↔contents coupling does for every other navigation.
- **Tree-pane `on_key` lets you intercept individual keys without overriding the whole Tree default.** Calling `event.stop() + event.prevent_default()` only in the case you care about (e.g. Left-on-root) lets the rest of the keys keep Textual's default semantics. Cleaner than overriding the binding outright and trying to reimplement the default behaviour.
- **Static + update() called from `__init__` blows up the Visual pipeline.** Textual 8.x's render path expects `_renderable` to be valid before mount; if you call `self.update(...)` too early you get `'NoneType' object has no attribute 'render_strips'` when the widget first renders. For widgets that compute their own content dynamically, subclass `Widget` and override `render()` returning a Rich renderable — bypasses Static's Visual indirection entirely.
- **Reactive auto-refresh covers the rendering side.** A widget with reactive attributes (e.g. `query: reactive[str]`) re-renders automatically when those attributes mutate. No need to call `self.refresh()` from your own setters; just assign `self.query = new_value` and Textual schedules a re-render.
- **Static's `renderable` attribute is private** on Textual 8.x. Tests that want to inspect what a Static-derived widget is showing should call `str(widget.render())` rather than `widget.renderable`. `render()` is the public surface that returns the current Rich renderable.
- **`asyncio.create_task` is the right pattern for firing async work from a sync queue callback.** `_on_plan_complete` is sync (called from the queue worker); kicking off the pane refresh via `asyncio.create_task(self._refresh_panes_after_op())` lets the worker move on to the next plan immediately while the refresh happens on the event loop. Wrap the refresh body in try/except so a failure doesn't propagate back to the worker.
- **`set_timer(timeout, callback)` for one-shot deferred work.** Textual's `set_timer` returns a `Timer` reference; keep the reference so a subsequent flash can `timer.stop()` and replace it with a fresh timer. The callback fires on the event loop, so it's safe to mutate widget state inside.
- **The full pytest suite takes ~45s now and hits the bash timeout.** Split runs by file when verifying after a non-trivial change. Faster long-term fix is parallelizing pytest with `pytest-xdist`, but it's not on a critical path.
