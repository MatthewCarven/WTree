# WTree Worklog

## 2026-05-19 — Design session

Designed WTree from scratch through architectural conversation. Outcomes:

- **`design.md` v0 created** — captures every locked decision, the lineage, core architecture, UI model, language/toolkit choice, parking lot, and a dated decision log. Living document; update as decisions evolve.
- **Core architecture settled.** `EntrySource` abstraction with NativeSource + ShellSource + MockSource; lazy per-directory traversal with composable `LogAll` / `LogOnDemand` / `LogDepth(n)` / `LogPersist` strategies on top; errors-as-data so damaged nodes are navigable and scans never abort; canonical ISO 8601 date storage (`YYYY-MM-DD HH:MM:SS`).
- **UI settled.** Explorer-style coupled panes (folder tree left, contents right); tagged set per-session and source-agnostic, holding `(source_id, path)` tuples that can span drive letters, UNC paths, and future sources; shell out to `$EDITOR` for editing (`$VISUAL` → `$EDITOR` → platform default); modular yield mode via `--pick` flag for shell-pipeline composition.
- **Keymap finalised.** XTree single-letter commands as primary, MC F-keys as aliases. Vim modal rejected. Full canonical bindings table written into `design.md`. Highlights: Space / `T` tag, `C` / F5 copy, `M` / F6 move, `R` / F2 rename (single-entry only — rejects with status-line nudge when tags exist), `D` / F8 / Del delete, `V` / F3 view, `E` / F4 edit, `N` / F7 make-new with dir/file sub-prompt, F9 menu bar (MC convention), `Alt+letter` as optional accelerator layer. Batch rename deferred to post-v0.
- **Cross-platform modifier strategy.** Win / Super / Cmd never bound (terminal apps never receive these on any major platform); Ctrl is the bedrock for system/meta operations; Alt is an optional accelerator layer with the macOS "Use Option as Meta key" terminal-preference caveat documented in `design.md`.
- **Language: Python + Textual.** Async-native event loop dovetails with `async`-generator `EntrySource.scan()`. Distribution strategy (pip vs PyInstaller vs Nuitka) deferred — not a v0 blocker.
- **Workspace write protocol established.** Project folder runs on a flaky mount that occasionally truncates writes. Saved as the `feedback_wtree_mount_rules` memory and used for the final `design.md` write this session. Protocol: stage in `/sessions/<id>/mnt/outputs/` first, whole-file write, `cp + sync` into project folder, `stat -c %s` to verify size, atomic-rename on mismatch.
- **Memory state committed.** Five memory files written: `user_matthew`, `project_wtree`, `ref_wtree_design`, `feedback_vim`, `feedback_wtree_mount_rules`, plus the `MEMORY.md` index.

Implementation has not started. Design phase ends here.

Next session pickup: see `todo.md`.

## 2026-05-20 — Implementation skeleton landed

Built the runnable Python project shell — bones only, no functionality yet. Everything that `todo.md` listed under "Tomorrow — implementation skeleton" is checked off.

Files added to the project folder:

- `pyproject.toml` — Python ≥3.10, depends on `textual>=0.85`, dev extras `pytest` + `pytest-asyncio`. Console script `wtree = wtree.app:main`. `pytest-asyncio` configured in `auto` mode so async tests don't need per-function decorators. Verified `textual` itself only requires `>=3.9,<4.0`; we chose 3.10 as the floor (3.9 hit EOL October 2025 and `match` statements are useful).
- `wtree/__init__.py` — exposes `__version__ = "0.0.1"`.
- `wtree/sources/__init__.py` — re-exports `Entry`, `EntrySource`, `Kind`, `ScanError`, `ScanResult`, `SourceCapability` from the base module.
- `wtree/sources/base.py` — the contract. `Kind` string-enum (file/dir/symlink/other) for stable wire format. Frozen `Entry` dataclass with required `name`/`kind`/`size`/`mtime` plus nullable `permissions`/`owner`/`link_target` and an `mtime_iso` helper that renders the canonical `YYYY-MM-DD HH:MM:SS` string. Frozen `ScanError` (path + message + cause-class-name). `ScanResult = Entry | ScanError` sum. `SourceCapability` declares which optional fields a source can supply and whether it allows `LogAll`. `EntrySource` ABC requires `source_id`, `capability`, and an async `scan(path)` that yields `ScanResult` and must never raise — every failure becomes a `ScanError` in the stream.
- `wtree/sources/native.py` — `NativeSource` reads the local filesystem via `os.scandir`. Symlink classification beats dir/file classification (a symlink to a directory reports as `SYMLINK`). Per-entry stat failures yield a `ScanError`, not a raise. Whole-directory open failures yield a single `ScanError` and stop. Permissions reported via `stat.filemode`; owner deferred (`pwd`/`grp` are Unix-only, cross-platform owner is a separate concern). Capability advertises permissions + link_target + supports_log_all.
- `wtree/sources/mock.py` — `MockSource` with scripted `contents` dict (path → list of `ScanResult`, so per-item errors are scriptable) and a separate `errors` dict for whole-directory failures that take precedence. Defensive copies on construction so test fixtures don't get mutated under them.
- `wtree/app.py` — `WTreeApp` Textual `App` with two empty bordered panes side-by-side (`Tree` left, `Contents` right) inside a `Horizontal`. Bindings stub: `q` and `F10` quit, `?` placeholder noop so the Footer cheat sheet stays honest. `main()` is the console-script entry point.
- `tests/test_native_source.py` — five tests: empty dir, dir with one file + one subdir (kind + size + mtime assertions), missing dir yields a `ScanError`, `source_id == "native"`, capability advertises permissions + link_target.
- `tests/__init__.py` — empty marker.
- `README.md` — install + run, points at `design.md` / `worklog.md` / `todo.md`, includes the macOS Option-as-Meta caveat from `design.md`.

Verification in the sandbox (before staging to the mount):

- `pip install -e .[dev]` clean; `wtree` script installed to `~/.local/bin`.
- `pytest -v` — 5 passed in 0.03s.
- Headless pilot run via `WTreeApp().run_test()` confirmed both panes render with the correct border titles (`Tree`, `Contents`), title is `WTree`, sub-title is `v0.0.1 skeleton`, and `q` quits cleanly.

Staging into the project folder used the mount-write protocol from `feedback_wtree_mount_rules`: `cp + sync` per file, `stat -c %s` against the source, atomic-rename retry on mismatch. All 10 files matched on the first pass — no retries needed.

Next session pickup: `todo.md` "After the skeleton runs" section. Top of the list is wiring the tree pane to a real path via `NativeSource`.

### Surfaced during implementation, worth flagging

- **`stat.filemode` on Windows** returns something sensible but not native-ACL-aware. Fine for v0; if/when a Windows user complains, we revisit. Logged here so it's not a surprise later.
- **Owner field is `None` everywhere in v0.** Cross-platform owner lookup is a small project on its own (`pwd`/`grp` are Unix-only; Windows wants pywin32 or ctypes). Defer until there's actual demand.
- **`asyncio_mode = "auto"`** in `pyproject.toml`'s pytest config means async tests don't need the `@pytest.mark.asyncio` decorator. Documenting because that's a foot-gun if someone later adds a sync test file and wonders why fixtures behave oddly.
- **The Textual entry point** is `wtree.app:main`, not `wtree:main`. If we ever move `main()`, the `[project.scripts]` line in `pyproject.toml` needs the same move or the `wtree` command will silently break on `pip install`.
- **Cowork Write tool silently no-op'd `worklog.md`** when asked to overwrite a previously-Read file. Detected via post-write `stat -c %s` showing the original size. Worked around by writing the file via a bash heredoc. Worth a thumbs-down report.

## 2026-05-20 — Tree + Contents panes wired up

Items 1 + 2 from the `todo.md` "After the skeleton runs" section are now done. The skeleton's two empty Static placeholders have been replaced with real widgets that load directory data through `NativeSource` and stay coupled (cursor moves on the left, contents refresh on the right).

Files added to the project folder:

- `wtree/widgets/__init__.py` — package marker that re-exports `TreePane` and `ContentsPane`.
- `wtree/widgets/tree_pane.py` — `TreePane(Tree[str])`. Node data is the absolute path string (or `None` for error placeholder leaves). Population is lazy per node: scan happens on `NodeExpanded` and is recorded in a `_loaded: set[int]` so re-expand is a no-op. Root is populated + expanded in `on_mount` so the user sees children immediately. Only `Kind.DIR` entries become children; `ScanError`s become non-expandable leaves with a `⚠ ` prefix. Sort is case-insensitive alpha; errors listed first.
- `wtree/widgets/contents_pane.py` — `ContentsPane(DataTable)` with columns `Name | Size | Modified | Perms`. `show_path(path)` clears and repopulates from `source.scan(path)`. All entry kinds shown. Sort key is `(Kind sort order, lowercased name)` so dirs cluster at the top. XTree-style `<DIR>` size and trailing-slash directory names. Errors are header rows with a `⚠ ` prefix.

Files modified in the project folder:

- `wtree/app.py` — `WTreeApp` now takes optional `source: EntrySource` and `root_path: str` constructor args so tests can inject `MockSource`. Compose yields `TreePane` + `ContentsPane` instead of two `Static`s. `on_mount` is now async — it shows the root's contents in the contents pane immediately and focuses the tree, so the user has somewhere to type from the moment the app draws. `on_tree_node_highlighted` is the coupling hook: it calls `contents.show_path(event.node.data)`, including the `None` case for error-leaves (which clears the contents pane).
- `tests/test_app.py` (new) — four pilot tests via `WTreeApp.run_test()`:
  1. tree pane shows directories only (files filtered out, alphabetical),
  2. contents pane shows the root's full entries on mount (file + dir + file),
  3. pressing ↓ moves the tree cursor and the contents pane follows to the child directory,
  4. a directory-level `ScanError` becomes a `⚠ ` leaf in the tree, with the scripted contents suppressed (matches `MockSource` priority semantics).

Verification in the sandbox:

- `python3 -m pytest -v --basetemp=/tmp/wtree-pytest` — 9 passed in 1.01s (5 existing + 4 new).
- Live NativeSource smoke run via `WTreeApp.run_test()` pointed at the sandbox project folder: tree shows 5 directories (`.pytest_cache`, `pytest-cache-files-*`, `tests`, `wtree`, `wtree.egg-info`), contents pane shows 7 rows total (5 dirs + `pyproject.toml` + `README.md`).

Staging to the project folder used the mount-write protocol: `cp + sync` per file, `stat -c %s` against the source, `cmp -s` byte-for-byte. All 5 staged files (the 3 new widget files + `app.py` + `test_app.py`) matched on the first pass — no atomic-rename retries needed.

### Mount corruption surfaced

Before staging, I read the existing `todo.md` from the project folder to plan the append. It was 2724 bytes; the sandbox copy was 3115 bytes. The mount's copy is silently truncated at "remote/" — losing the trailing "SFTP, SMB discovery, FS watching, bookmarks/history, batch rename, themes." This was a previously-staged file that has bit-rotted on disk; not the result of this session's writes. Repaired by rebuilding `todo.md` fresh in the sandbox and atomically staging it. Exactly the failure mode the `feedback_wtree_mount_rules` protocol is designed to catch and recover from.

### Surfaced during implementation, worth flagging

- **`Tree.NodeHighlighted` fires on initial mount too**, which means the contents pane could in theory receive two refreshes (one from `WTreeApp.on_mount`, one from the initial highlight event). In practice the second one is harmless because it loads the same path — but if scanning ever becomes expensive, we should add a "skip if path unchanged" guard in `ContentsPane.show_path`.
- **`_loaded: set[int]` keys off the Textual node ID, not the path.** Two different nodes that happen to back the same path (e.g., user navigates somewhere, comes back) would scan twice. Probably fine — the cache is per node, not per path — but worth remembering when we add `Ctrl+R` refresh.
- **No path-normalisation gate at the `EntrySource` boundary.** `os.path.join` and `os.path.abspath` mostly Do The Right Thing, but if a source ever returns a path with `..` or trailing slashes, downstream comparisons could get weird. Watch for this when implementing the tagged set (uses `(source_id, path)` tuples as identity).
- **`textual.widgets.tree.TreeNode` import path.** The public API exposes `Tree`, but typing `TreeNode` directly requires the sub-module path. If Textual reorganises this, the type annotation in `tree_pane.py` will need adjusting.

Next session pickup: `todo.md` "After the skeleton runs" item 3 — implement the tagged-set state. `(source_id, path)` tuples in a session-scoped set. Per `design.md` § Tagged set, this is the central object that operations apply to.

## 2026-05-20 — Tagged set landed

Item 3 of `todo.md` "After the skeleton runs" is done. The tagged set — the central selection object per `design.md` § Tagged set — is now wired end to end. Files added/modified, sandbox-verified, atomically staged to the mount.

Files added:

- `wtree/tagged_set.py` — `Tag` frozen+slots dataclass and `TaggedSet` class. `(source_id, path)` identity (design.md § Tagged set scope), so the set can hold entries from different drives, mounts, UNC paths, or any future source simultaneously. API: `add` (idempotent), `remove` (silent when absent), `toggle` (returns new state for the caller to render), `contains`, `clear`, `__len__`, `__iter__`, `__bool__`.
- `tests/test_tagged_set.py` — 9 pure-data-structure unit tests (no Textual, no disk). Covers set semantics, toggle return value, cross-source coexistence, iteration yields `Tag` objects.

Files modified:

- `wtree/widgets/contents_pane.py` — added a leading "T" column with "*" tag-marker; pane-local `Space` and `T` bindings call `action_toggle_tag` on the cursor row; `refresh_tag_markers()` updates only the marker column from the current tagged-set state without re-scanning the source; posts a `TagsChanged` message after every toggle so the app can refresh its subtitle. Error rows carry an empty-string sentinel in `_row_paths` and are deliberately non-taggable. Cursor is pinned to row 0 after a refresh so `action_toggle_tag` always has a valid row.
- `wtree/app.py` — `WTreeApp` now owns the `TaggedSet` as a public attribute and passes a reference to `ContentsPane`. App-level `Ctrl+U` binding → `action_untag_all` (clears the set and refreshes markers, no-op when already empty). `on_contents_pane_tags_changed` updates the header subtitle so the user can see the running tag count. `_update_subtitle` reads `len(self.tagged_set)` and renders either `"v0.0.1"` (empty) or `"v0.0.1 — N tagged"`.
- `tests/test_app.py` — 7 new pilot tests: Space toggles tag, T also toggles tag, marker renders in the "T" column, marker survives a pane refresh (the set lives on the app, not the pane), `Ctrl+U` clears both the set and the markers, subtitle reflects the count, error rows are non-taggable.

Verification in the sandbox:

- `python3 -m pytest -v` — 25 passed in 2.97s (5 native + 9 tagged_set + 11 app pilot tests).
- Live `NativeSource` smoke via `WTreeApp.run_test()` pointed at the sandbox project folder: tag two rows, subtitle shows "v0.0.1 — 2 tagged"; Ctrl+U clears, subtitle reverts to "v0.0.1".

Staging to the project folder used the mount-write protocol with the atomic-by-default form (`cp .tmp` → `sync` → `mv -f`). All 5 files (3 new/modified + 2 test files) matched on the first pass, byte-for-byte verified via `cmp -s`.

### Surfaced during implementation — Cowork `Write` tool truncates large files

Significant find. The Cowork `Write` tool **silently truncates files at ~3-4 KB**. Both `wtree/app.py` (4.7 KB target) and `wtree/widgets/contents_pane.py` (6.7 KB target) ended up truncated mid-line on disk after a `Write` call, but the editor reported "updated successfully" and the file state in Claude's context window appeared correct.

The truncation only became visible after pytest failed mysteriously — `app.tagged_set` was missing from the constructed app even though the source clearly assigned it. The pycache `.pyc` was happy to load the truncated source (Python compiles each top-level statement independently; classes with a syntax error inside an unfinished method still defined enough of their valid statements to make `WTreeApp` partly importable, with `__init__` simply lacking its later body). `dis.dis(WTreeApp.__init__)` showed the function ending one statement too early — that was the smoking gun.

**Workaround:** use bash heredoc (`cat > file << 'EOF' ... EOF`) for any file over ~3 KB. The heredoc went in clean; both `cat` writes verified to the exact expected byte count and pass syntax checks.

This is a separate failure mode from the previously-reported "Write silently no-op's an already-Read file" (the worklog.md issue last session) and from the "files bit-rot in place on the mount" finding (today's earlier todo.md issue). Three distinct ways for content to go missing in this stack. The mount-write protocol now also implicitly covers Write truncation, since the sandbox `wc -l` + `stat -c %s` checks catch it before staging.

Saved to memory: `feedback_wtree_mount_rules` updated with rule 10 (large files via heredoc when Write disagrees).

### Other items worth flagging

- **`refresh_tag_markers` vs `show_path` cost.** `refresh_tag_markers` updates one cell per row without re-scanning the source — cheap. `show_path` is a full repopulate. The split is so `Ctrl+U` doesn't pay the scan cost.
- **Initial cursor placement.** Without an explicit `move_cursor(row=0, column=0)` after `show_path`, DataTable's cursor would sometimes start at `-1`, making the first Space press a no-op. Pinned it; tested it.
- **`Tag.__hash__` via `frozen=True`.** Frozen + slots gives both hashability (needed for `set[Tag]`) and a smaller per-instance memory footprint — relevant if the session-long set ever holds thousands of entries.

Next session pickup: `todo.md` "After the skeleton runs" item 4 — bind the navigation keys (arrows, Tab, Enter, Backspace). Arrows in the tree pane should collapse/expand (per `design.md` § Modality); in the contents pane they should walk parent/enter-dir. Tab cycles pane focus.

## 2026-05-20 — Navigation keys (todo item 4)

Bound the arrow / Tab / Enter / Backspace navigation per `design.md` § Modality and § Keymap. Tree cursor remains the single source of truth — all directory navigation routes through it and the existing `NodeHighlighted` plumbing refreshes the contents pane.

Files modified:

- `wtree/widgets/tree_pane.py` — added Backspace binding plus two methods. `action_focus_parent()` moves the cursor to `cursor_node.parent.line` (Textual's `cursor_line` is a reactive — assignment fires `NodeHighlighted` for free). `focus_dir_under_cursor(child_path)` is the entry point used by the contents pane's →/Enter: expand the current node if needed, await `_populate` directly (deterministic instead of relying on the event-handler path), then scan children for a `data == child_path` match and reseat the cursor. Returns `False` if no match — gives the caller a way to detect the "user pressed → on a file row" case, though in practice we filter that out before calling.
- `wtree/widgets/contents_pane.py` — added a parallel `_row_kinds: list[Kind | None]` (None for error rows) so `action_enter_dir` can tell a directory row from a file row without rescanning. Added bindings for `left`, `right`, `enter`, `backspace`. The two new actions both delegate to the tree pane: `action_go_parent` → `tree.action_focus_parent`, `action_enter_dir` → `tree.focus_dir_under_cursor`. A `_tree()` helper does the `query_one(TreePane)` lookup with a local import — keeps `TreePane → ContentsPane → app` import order intact (no module-level circular dep).
- `wtree/app.py` — Tab is now an app-level binding (`action_cycle_focus`). Footer label is "Switch pane". `if self.focused is tree: contents.focus() else: tree.focus()` — biases toward landing on the tree from any other state, which matches the boot-time home position.
- `tests/test_navigation.py` (new) — 10 pilot tests:
  - Tab moves focus tree→contents, and contents→tree.
  - Backspace in tree moves cursor to parent; at-root is a no-op (no crash).
  - → in contents enters the highlighted dir; Enter behaves identically.
  - → on a file row is a no-op (cursor stays in current dir).
  - ← in contents goes to parent dir; Backspace mirrors ←; at-root is a no-op.

Sandbox results: 35 passed, 6.07 s. Mount results after staging: 35 passed, 6.43 s. Bytes verified per `feedback_wtree_mount_rules`: tree_pane.py 6505, contents_pane.py 9784, app.py 5658, test_navigation.py 8314 — all matched between sandbox and mount on first write.

Decisions worth recording:

- **Override DataTable's default left/right.** DataTable normally uses ←/→ for column navigation. We have five columns but only column 0 (the tag marker) ever changes meaningfully, so column-nav has no v0 utility. The pane-modal navigation (parent / enter-dir) from `design.md` § Modality wins. This is reversible if a future feature wants column scoping — a `priority=False` override would let DataTable's defaults coexist.
- **Why a `_row_kinds` parallel list instead of looking up the cell text.** The Name column has a trailing-slash convention for dirs (`foo/`) but parsing that back out feels fragile. A parallel list of `Kind` enums is cheap, unambiguous, and the same shape as `_row_paths`. Both lists are cleared and rebuilt in lockstep inside `show_path`.
- **`await self._populate(node)` instead of relying on `NodeExpanded`.** When the contents pane asks the tree to drill in, we want a deterministic "by the time this returns, the child is visible" contract. Going through the event handler would still work but adds an asynchronous gap. Calling `_populate` directly costs nothing — it's idempotent via `_loaded`.
- **Tab at app-level vs Textual's default `focus_next`.** Both work in practice with only two focusable widgets, but the explicit binding has two payoffs: (1) Footer shows "Switch pane" rather than nothing, and (2) `action_cycle_focus` will be the natural extension point when more focusable surfaces appear (status line, search prompt). The "if focused is tree else tree" branch handles the "neither pane focused" case by defaulting to the tree — the app's home position.
- **`action_focus_parent` writes to `cursor_line` rather than calling `select_node`.** `select_node` fires `NodeSelected`, which we don't listen for; `cursor_line` is the reactive that fires `NodeHighlighted`, which we do. The latter is what keeps the contents pane in sync.

Surfaced during implementation (added to follow-ups):

- The contents pane currently uses `query_one(TreePane)` to reach the tree pane. That's fine while we have exactly one tree pane, but the moment a second is introduced (e.g., a dual-pane mode) it'll need an explicit reference passed in at construction.
- ← / → / Enter / Backspace are only bound on the contents pane. If we add tagging from the tree pane (existing follow-up), that pane will also want a "tag the highlighted dir" action — and we'll need to decide whether Backspace stays "go to parent" or becomes something else there.
- No status-line feedback for the "→ on a file row is a no-op" case yet. Once the MC-style status line lands (item 7), a small "press V to view" hint would be friendly.

Next session pickup: `todo.md` "After the skeleton runs" item 5 — bind the first file op, Copy (`C` / F5). Per `design.md` § Selection rule, Copy operates on the tagged set when non-empty, else on the entry under the cursor.


---

## 2026-05-21 — Copy op scaffold (todo item 5, plan-only)

Implemented the *planner* half of `C` / F5. Matthew's framing: "overbuild it
to take many sources but do it in an iterative way — write the generic
scaffolds that do nothing initially except create lists of files that would
be copied for smoke testing." So plan is real; execute is intentionally not.

### What landed

**New package `wtree/ops/`** — generic file-operation plumbing, one layer
above `EntrySource`. Three files:

- `wtree/ops/__init__.py` — re-exports the public surface (`Plan`,
  `PlanItem`, `PlanError`, `WalkedEntry`, `WalkSummary`, `OperationKind`,
  `plan_copy`, `walk_tags`).
- `wtree/ops/base.py` — pure data types. `OperationKind` enum (COPY/MOVE/
  DELETE/RENAME — string values are stable wire format for the future undo
  log). `PlanItem` carries full `(src_source_id, src_path, dst_source_id,
  dst_path, kind, size)` so a cross-source dispatcher can pick the right
  transfer adapter without re-walking either side. `Plan.summary()`
  produces a one-liner for `notify()` / future status line.
- `wtree/ops/copy.py` — `walk_tags(tags, registry)` expands each tag into
  a flat list of leaves (recursing into dirs via `EntrySource.scan`),
  depth-first parent-first so the execute dispatcher can mkdir before
  copying contents into it. `plan_copy(tags, destination, registry)`
  wraps the walk and pairs each leaf with a destination path under
  `{destination}/{basename(tag)}/{relative path}` (matches every shell
  `cp` ever shipped). Internals use POSIX path joins regardless of
  source/dest platform — the execute dispatcher will translate to native
  separators when it actually applies the plan.

**`EntrySource.entry_at(path)` added** to `wtree/sources/base.py`. The
planner needs to classify each top-level tag as file vs. dir before
deciding whether to recurse; `scan(path)` returns children, not "what is
this path". The new method:

- Default implementation scans the parent and finds the entry by basename.
  Inefficient for tight loops but correct, and inherits-for-free for
  `MockSource`.
- `NativeSource` overrides with `os.lstat` for O(1) lookup + correct
  handling of filesystem roots (`/`, `C:\`) that the default can't
  classify.

**App binding** — `wtree/app.py`. Added:

- `("c", "action_copy", "Copy")` and `("f5", "action_copy", "Copy")` to
  the app-level BINDINGS.
- `self.sources: dict[str, EntrySource]` — a tiny registry, currently
  `{"native": NativeSource}`. The ops layer looks tags up here by
  `source_id`; future archive / SFTP sources slot in without touching
  the planner.
- `self.last_plan` — exposed so tests assert against the plan and a
  future "show last operation" affordance can introspect it.
- `action_copy` — async. Resolves source tags via the Selection rule,
  builds the plan, surfaces a summary via `self.notify()`. No FS writes.
- `_resolve_selection_tags()` — applies the design's Selection rule:
  tagged set if non-empty (sorted by (source_id, path) for
  deterministic plans), else the contents-pane's cursor entry. Tree-pane
  cursor isn't consulted because the tree shows only directories; the
  file-level cursor lives in the contents pane.

**ContentsPane helper** — added `cursor_entry() -> (path, Kind) | None`
to expose the current row to ops code without leaking DataTable details.

**Tests** — `tests/test_ops_copy.py`, 13 new (48 total, all green on
mount):

- `walk_tags` shape tests: single file, single dir recurses, unknown
  source_id error, missing path in mock.
- `plan_copy` shape tests: file into dir maps to `dest/basename`; dir
  preserves subtree; summary text is well-formed; errors propagate from
  walk; empty tag list yields `is_empty`; cross-source dst paths.
- `action_copy` integration via pilot: cursor-fallback when no tags;
  tagged-set path when present; empty-everything is a no-op.

### Decisions and rationale

- **Plan / execute split.** Keeping the planner as pure data lets the
  modal dialog render the plan, the future undo log persist it, and a
  smoke test assert on it. Execute will be a separate function (or
  `Plan.apply()` — undecided, follow-up entry on todo).
- **Source registry on the app, not global.** Same pattern as
  `tagged_set`: the app owns it, ops borrow a reference. Test isolation
  for free.
- **Why `entry_at` and not "tag carries kind".** `(source_id, path)` is
  the canonical tag identity from `design.md` § Tagged set scope; I
  didn't want to widen it on day one. `entry_at` is a small, useful
  primitive that every future op (move, delete, properties, rename) will
  reuse. Follow-up notes a possible optimization later.
- **Placeholder destination = app root.** Real flow needs a modal; the
  status line + modal infra (items 6 and 7) hasn't landed. Scaffold
  smoke-tests with a deterministic dest so plan items are inspectable.
  When the modal lands, `action_copy` becomes "plan → modal → apply".
- **POSIX-only path joins in the planner.** The destination side is the
  execute dispatcher's problem. Forcing one path flavour in the plan
  data keeps cross-source plans (e.g. native → archive) sane.

### Mount-write protocol notes

Hit the bit-rot pattern again on `todo.md`. After two `Edit` calls, the
file-tool view showed 78 lines (full content) but `bash` saw the file
truncated at 54 lines (mid-sentence cutoff at "press V to view hint w").
Re-staged via heredoc — wrote the full content from sandbox via `cp +
sync + mv -f + sync` and verified `wc -l` and `tail` matched expectations.
Updated `todo.md` "Notes for the next session" to flag this specific
failure mode for future sessions: **after any Edit on a markdown file
>3 KB, verify with `wc -l` + `tail` via bash before assuming the write
landed.**

All other files this session staged byte-identical first try via the
file tools (ops/* via heredoc + cp; tests via heredoc + cp; the four
`Edit`s to existing source files via the desktop API directly, all
verified clean by `wc -c` + `python3 -c "import ast..."`).

### Surfaced during implementation (added to follow-ups)

- **Execute dispatcher.** The other half of the operation. v0
  `("native","native")` pair via `shutil.copy2` + `os.makedirs`; everything
  else `NotImplementedError`.
- **Destination modal.** Reusable across copy / move / rename / make-new /
  log-source.
- **Tag-with-kind optimization.** Avoids the parent scan in `entry_at`.
- **Plan body in a real surface.** `notify` truncates at one line; the
  full plan items should land in a "command output" pane once that
  exists.
- **Conflict detection at plan time.** Pre-stat destinations so the user
  sees overwrite/skip/rename choices before execute runs.
- **Cross-platform dst translation.** Planner emits POSIX; execute
  dispatcher (or NativeSource on Windows) will need to translate.
- **`Plan.apply` placement.** Method vs. free function — decide when
  execute is real code.

### Next session pickup

`todo.md` § "After the skeleton runs": the Copy entry is now `[~]`
(partial). Two choices for what to do next:

1. **Execute dispatcher**, landing the actual `shutil.copy2`/`copytree`
   side for the `("native","native")` pair. Still needs a destination
   somehow — either hard-code a `/tmp` target for smoke, or…
2. **Destination modal first** so the full plan → prompt → apply flow
   works end-to-end with real input. This is a smaller widget and
   unblocks the Copy completion plus every future op that needs a path
   input.

My recommendation is option 2, but Matthew picks. The Selection rule
half is rock-solid; the remaining mile is destination + execute.


---

## 2026-05-21 (later) — Destination modal + @work flow

Implemented the destination modal half of `C` / F5. With this in, the plan
side of Copy is **end-to-end real**: press C, type a destination, see the
plan reflect the typed path, with Esc/empty-string as clean cancellations.

### What landed

**New widget `wtree/widgets/prompt.py`** — `PromptDialog(ModalScreen[str |
None])`. Single `Input`, optional title + hint labels, centered on a
dimmed backdrop. Esc dismisses with `None` (cancel); Enter dismisses with
the typed text. Reusable across every operation in `design.md` § Keymap
that takes a typed argument (Move, Rename, Make-new, Log-new source,
glob patterns for `+`/`-`). Generic type parameter narrows
`push_screen_wait`'s return so callers don't have to cast.

**`action_copy` rewired through the modal.** Now `@work`-decorated and
awaits `await self.push_screen_wait(PromptDialog(...))`. Default
destination is the contents pane's current path (most common "drop here"
expectation; falls back to launch root at boot). Title varies by tag
count ("Copy /readme.txt to:" vs "Copy 3 tagged item(s) to:"). Esc and
empty-string both treated as cancellation — last_plan untouched, prior
plan stays available for inspection.

**`@work` on the action.** Textual's `push_screen_wait` requires a worker
context — without `@work` it raises `NoActiveWorker`. The decorator wraps
the action body in a Textual worker; the action body stays `async def`.
Bonus: this is the natural seam for the eventual operation queue — when
that lands, the worker becomes the queue runner and the action body
shrinks to "enqueue plan + return".

**Tests.** `tests/test_ops_copy.py` grew from 13 → 16 tests; the three
prior `action_copy` pilot tests were rewritten to interact with the
modal (press C → pause → press Enter / type / Esc → pause). New cases:
typed-destination override, Esc cancellation, empty-string cancellation.
All 51 tests green on mount in 10.38s.

### Design decisions

- **`PromptDialog` is generic, not Copy-specific.** Every typed-argument
  binding in `design.md` § Keymap can reuse it. Avoids the v1 mistake of
  five near-identical dialog classes.
- **Default destination = contents pane's current dir.** Tested both
  "launch root" and "contents-pane current". The latter matches the user
  expectation that pressing C on `/proj/docs` and accepting the default
  copies *into* the dir they're looking at. The contents pane's
  `current_path` is the right value because tree-pane cursor is dir-only
  and contents-pane mirrors it via `NodeHighlighted`.
- **Esc and "" both cancel.** Slightly redundant but matches every OS
  Save-As / file-open dialog. The notify text differentiates: "Copy:
  cancelled." vs "Copy: cancelled (empty destination)." — so the user
  knows whether the empty-input case was intentional or a slip.
- **`last_plan` is not cleared on cancel.** A prior plan stays available
  for whatever "show last operation" affordance lands later. The early
  `last_plan = None` only happens when the *Selection rule itself*
  produces nothing — that's a true "no plan exists" state.

### Mount-write protocol — new failure mode

Hit a fresh variant of the bit-rot today (twice). Pattern:

1. Edit `wtree/app.py` via the desktop file API. Edit tool reports
   success. `Read` on the file shows the new content correctly.
2. `stat` and `wc -l` from bash show the *old* file size and old line
   count.
3. `python3 -c "import ast; ast.parse(...)"` from bash fails with a
   `SyntaxError` somewhere in the middle of the file — bash is seeing
   a frankenstein of new bytes from the Edit overlaid on old trailing
   bytes that didn't get truncated.

Recovery (worked both times): re-stage the *full* file content in
sandbox via heredoc, then `cp .tmp + sync + mv -f + sync` and verify
with bash. The full-file rewrite forces the mount to commit cleanly.

Detection: after **any** Edit on a mount-resident file >3 KB, run
`stat -c %s` + `python3 -c "import ast; ast.parse(...)"` from bash.
If either disagrees with the file-tool view, heredoc-rewrite.

Both `feedback_wtree_mount_rules` memory and `todo.md` "Notes for the
next session" updated with this pattern.

### Surfaced during implementation (added to follow-ups)

- **Modal validation.** Currently accepts any non-empty string; could
  classify the typed path before building the plan.
- **Focus restoration.** Pilot tests don't verify that dismissing the
  modal restores focus to the pre-modal pane. Worth a spot-check.
- **Path completion / MRU.** Tab-completion and Up-arrow history are
  parking-lot for now.

### Operation queue — design call, recorded for the next session

Matthew's preference, verbatim: "I was hoping eventually to be able to
minimize and queue them like the 2nd copy waits until the first is
finished, unless you feel like implementing a bespoke network device /
physical device interpreter that is multiplatform and can tell you what
disks are currently 'in use' for copying, my take is just to wait for the
1st to complete then do the next and so on though for simplicity sake."

**Shape:** `wtree/ops/queue.py` with `OperationQueue` owning a FIFO
`deque[Plan]` and exactly one running worker task. `enqueue(plan)`
appends and kicks the worker if idle. Worker awaits `apply_plan(plan)`
per item, then pulls the next. Exception in one plan should NOT crash
the queue — log and continue.

**Concurrency model:** strictly serial. No device-busy detection. Two
copies that target different physical disks still queue one-after-the-
other; we trade theoretical parallelism for cross-platform simplicity
and a code path that doesn't depend on platform-specific block-device
APIs.

**Minimize-and-resume:** the queue worker is independent of any UI
screen, so a future "minimize the progress dialog" gesture just
re-pushes the dialog later. The worker keeps running regardless. The
progress dialog is its own follow-up (needs the status line first).

**`action_copy` after this lands:** "build plan → `app.op_queue.enqueue(plan)`
→ notify queue depth → return". The `@work` decorator stays but the
action body shrinks.

### Next session pickup

Two natural orderings:

1. **Operation queue + native→native execute first** — fastest path
   to "Copy actually copies bytes". Subtitle reflects queue depth;
   serial runner; `shutil.copy2` / `os.makedirs` for the
   `("native","native")` pair; other pairs `NotImplementedError`.
2. **Status line + F-key bar first** — gives the queue depth and per-op
   progress a real home before they exist as data. Smaller deltas but
   reorders item 7 ahead of the queue.

Both are reasonable; my recommendation is option 1, since the queue
runner is the load-bearing piece and the subtitle is fine as a
temporary surface for queue depth until the status line lands.


---

## 2026-05-21 (later still) — Operation queue + execute (todo item 5 complete)

**This session closed the loop on Copy.** WTree now actually moves bytes.
Press C, type a destination, watch the file land. Multiple copies queue
up FIFO. The whole Copy chain — Selection rule -> modal -> planner ->
queue -> executor — works end to end with real filesystems.

### What landed

**New `wtree/ops/execute.py`** — `apply_plan(plan, registry, progress)`.
Iterates `plan.items` in the planner's emit order (depth-first parent-
first so directories are mkdir'd before files inside them). Dispatches
each item by `(src_source_id, dst_source_id)`:

- `("native", "native")`: `shutil.copy2` for files (preserves mtime),
  `os.makedirs(exist_ok=True)` for dirs, `os.symlink(readlink(src))` for
  symlinks. All file ops dispatched via `asyncio.to_thread` so the
  event loop stays responsive on multi-GB copies.
- Anything else: `NotImplementedError` per item -> `ItemResult(FAILED)`
  with a clean error message. Queue keeps going.

The executor *never* raises. Per-item exceptions become `FAILED` items;
the caller gets a complete `OperationResult` back every time. Result
types added to `wtree/ops/base.py`: `ItemStatus` enum, `ItemResult`
dataclass, `OperationResult` with `success_count`/`skipped_count`/
`failed_count`/`all_succeeded`/`summary()`.

**New `wtree/ops/queue.py`** — `OperationQueue`. Owns an
`asyncio.Queue[Plan]` + exactly one background worker task. Public API:

- `start()` / `stop()` lifecycle (idempotent both ways).
- `enqueue(plan)` sync put.
- `depth`, `running`, `completed` properties.
- `wait_until_idle()` for tests.
- `on_plan_start` / `on_plan_complete` callbacks for UI integration.

Design call from Matthew: strictly FIFO, no device-busy detection.
"my take is just to wait for the 1st to complete then do the next and
so on for simplicity sake." The worker is independent of any UI screen,
so future minimize/resume of a progress dialog just re-pushes the
dialog later — the worker doesn't care.

Resilience: per-callback exceptions are caught and logged; a
catastrophic `apply_plan` failure (shouldn't happen — it catches per
item — but defense in depth) synthesises an all-failed
`OperationResult` so the completed log stays consistent. One bad plan
NEVER kills the queue.

**`WTreeApp` wiring:**

- `self.op_queue: OperationQueue` constructed in `on_mount` (event
  loop must be running for `asyncio.Queue`'s contract).
- `on_unmount` cleanly stops the worker.
- `action_copy`: same flow as before through the modal, but after
  planning it now calls `self.op_queue.enqueue(plan)` instead of
  storing only `last_plan`. Tagged set is cleared on enqueue (XTree's
  behaviour — successful Copy "consumes" the tag list).
- `_on_plan_start` / `_on_plan_complete` callbacks update the subtitle
  and notify the user on completion (warning severity on failures so
  they don't get lost in a busy session).
- Subtitle now reads `v0.0.1` when idle, `v0.0.1 - 3 tagged` with tags,
  `v0.0.1 - running: copy` with an active op, `v0.0.1 - running: copy
  (+1 queued)` with both.

### Tests — 20 new, 71 total

- `tests/test_ops_execute.py` (9): file copy, dir+subtree, mtime
  preservation, mkdir-on-demand for missing parents, symlink
  recreation (skipped on Windows), missing-source -> FAILED,
  cross-source pair -> FAILED, progress callback fires per item,
  empty plan trivially succeeds.
- `tests/test_ops_queue.py` (7): single plan, FIFO ordering of two
  plans, strict serial (no overlap — verified via `events == [start:a,
  done:a, start:b, done:b]`), failing plan doesn't block next,
  `start()` idempotent, `stop()` safe when never started, callback
  exception isolation.
- `tests/test_copy_e2e.py` (4): cursor-entry single file, tagged dir
  with subtree, two C presses serialize through queue, subtitle
  returns to baseline post-drain.

All 71 green on mount in 13.43s.

### Decisions and gotchas

- **`asyncio.to_thread` for all native file ops.** Without it,
  `shutil.copy2` on a multi-GB file would block the Textual event loop
  and freeze the UI. `to_thread` is Python 3.9+ stdlib (we require
  3.10+); zero-cost for small files.
- **Tagged set cleared on enqueue, not on completion.** Matches the
  user's mental model: "I tagged these, I copied them, now I want a
  fresh slate for the next op." Waiting for completion would mean the
  set stays "stuck" while a big copy runs.
- **`self._running = None` BEFORE `on_plan_complete`.** Caught by a
  failing test: the subtitle still said "running: copy" after
  `wait_until_idle` returned. The callback should logically see the
  post-plan state. Reordered with a belt-and-braces clear in `finally`.
- **`pilot.press()` is slow when typing long strings.** Hit this when
  tests tried to type `/tmp/pytest-of-.../test_e2e_..` char-by-char.
  Fix: set `Input.value` directly via the widget API. The
  `Input.Submitted` event still fires on Enter, and the user-facing
  flow is identical. Test runtimes dropped from "timeout" to
  sub-second. Added to "Notes for the next session" in todo.md.
- **`@work` decorator stays on `action_copy`.** The action body still
  awaits `push_screen_wait` (modal), still awaits `plan_copy`. The
  enqueue is sync (`put_nowait`), so the action body's tail is fast.
  The actual byte-moving happens in the OperationQueue's worker —
  completely independent task.

### Mount-write notes

Heredoc-rewrote four files this session (base.py, queue.py, app.py,
todo.md) due to the recurring bit-rot pattern. At this point I'm
treating "heredoc-rewrite after every non-trivial Edit on the mount"
as the default protocol rather than the recovery step. Memory
`feedback_wtree_mount_rules` already covers it; today reinforced that
bash and the file tools regularly disagree by hundreds-to-thousands of
bytes on the same file path.

### Next session pickup

todo item 5 is **fully done**. Three reasonable starts for next session:

1. **Status line + F-key bar** (item 6 from the original list). The
   subtitle is doing too much work right now. A real MC-style status
   line at the bottom of the screen with the current operation's
   summary + an F-key reference bar would clean up the visual story.
2. **Bind Move (`M` / F6).** Same shape as Copy: plan -> modal ->
   enqueue. `apply_plan` dispatcher grows a Move adapter (`os.rename`
   on the same fs; copy+delete otherwise). Most of the infrastructure
   is reusable.
3. **Progress dialog for active copies.** Right now feedback is "the
   subtitle says running, then a notify when done". For big copies the
   user wants per-item progress. The `apply_plan(progress=)` callback
   already exposes it; just need to wire it to a `ModalScreen`.

My recommendation is option 1 — the status line is load-bearing
infrastructure that every later feature wants. Move and the progress
dialog both land more cleanly with a real status surface to render to.


---

## 2026-05-21 (later still^2) — Status line + F-key bar (todo item 6)

Two new bottom widgets, plus per-item progress wired through the
OperationQueue. The bottom of the screen finally has the MC look the
design called for, and big copies surface their progress live.

### What landed

**`wtree/widgets/keybar.py`** — `KeyBar(Static)`. Renders an MC-style
F-key cheat sheet across the bottom of the screen via `dock: bottom`.
Each cell is a dim two-character F-number followed by a reverse-video
label cell. The full list of 10 keys comes straight from `design.md`
canonical keymap (Help/Ren/View/Edit/Copy/Move/New/Del/Menu/Quit).
Wired vs unwired keys differ in style (`bold cyan` / `dim cyan` for the
number, `reverse` / `dim reverse` for the label). `_WIRED = {5, 10}`
today; as bindings land the set grows.

**`wtree/widgets/status_line.py`** — `StatusLine(Static)`. One-line
status above the F-key bar. Priority of what it shows (highest first):

1. **Queue running:** `Copy: 2/5 items` (or `Queued: 1 op(s) pending`
   if the worker hasn't picked up yet). Adds `[+N queued]` when more
   than one plan is queued.
2. **Cursor entry idle:** `/path/to/file  1.4 KB  2026-05-21 12:00`
   with a `/` or `@` kind marker. Best-effort `os.stat` on the path.
3. **Empty:** blank (or dim current-path) when no cursor entry.

Transient errors keep going through `self.notify()` toasts — StatusLine
is reserved for persistent state. `refresh_from(app)` is the public
API; cheap enough to call generously on every cursor move / queue
event.

**`OperationQueue` progress tracking.** Two new things on the queue:

- `running_progress: tuple[int, int] | None` property — `(done, total)`
  for the currently running plan, or `None` when idle.
- `on_item_progress(item_result, queue)` callback — fires once per
  item inside `apply_plan` so the UI can repaint mid-plan.

The worker's `_progress` closure increments the counter, updates
`_running_progress`, and fans out to the user callback. Pre-existing
queue tests still pass unchanged (the new field is None except during
a plan).

**`WTreeApp.compose`** — yields `StatusLine` and `KeyBar` after the
two-pane Horizontal. Both use `dock: bottom`. Dropped `Footer` because
KeyBar replaces it functionally and the design specifies the cheat
sheet, not Textual's default footer. Header subtitle shrunk back to
just `vX.Y - N tagged` (queue/running info moved entirely to the
StatusLine — more room there for fine-grained counts).

**Refresh hooks.** `_refresh_status()` is now called from every place
that might mutate displayed state: `on_tree_node_highlighted`,
`on_data_table_row_highlighted` (new — bubbles from ContentsPane),
`on_contents_pane_tags_changed`, `action_cycle_focus`,
`action_untag_all`, `action_copy` post-enqueue, and the three queue
callbacks (`_on_plan_start`, `_on_item_progress`, `_on_plan_complete`).

### Tests — 5 new, 76 total

`tests/test_status_keybar.py`:

- `test_keybar_lists_all_ten_fkeys` — every F-label and every digit 1-10
  present in the rendered bar.
- `test_statusline_shows_cursor_entry_when_idle` — `alpha.txt` + `11 B`
  in the status when cursor is on it.
- `test_statusline_empty_when_no_cursor` — empty dir, no "Copy:" / "Queued"
  noise leaks through.
- `test_statusline_shows_running_op_with_progress` — patches
  `op_queue._on_item_progress` to snapshot mid-flight, asserts at least
  one snapshot contains "Copy" + "N/M".
- `test_statusline_refreshes_on_cursor_move` — Down on contents pane
  swaps which file is displayed.

76/76 on mount in 12.52s.

### Decisions and gotchas

- **KeyBar uses `Static.render() -> Text` with rich markup**, not a
  horizontal of cells. Avoids the `1fr` column-sizing dance and gets
  exact widths for free. Costs: KeyBar doesn't expand to fill the line.
  For v0 acceptable — MC's bar isn't full-width either.
- **`on_data_table_row_highlighted` bubbles from ContentsPane.**
  ContentsPane is a DataTable subclass, so its `RowHighlighted` events
  propagate up to the app naturally. No custom message needed.
- **StatusLine does an `os.stat` per cursor move.** Cheap on local FS,
  potentially per-keystroke I/O on network shares. Documented as a
  follow-up; the fix is to cache the stat on `ContentsPane._row_paths`
  at scan time and reuse.
- **`app.query_one(Widget)` inside a modal mis-targets.** Hit this in
  tests: the snapshot callback queried `app.query_one(StatusLine)` from
  inside the OperationQueue worker while a PromptDialog was on top of
  the screen stack. Result: `NoMatches` on `Screen(id='_default')`.
  Fix: cache the StatusLine reference *before* pushing the modal. Added
  to next-session notes — this'll bite again whenever modal + worker
  interact.
- **Captures outside `async with app.run_test()` fail.** Forgot once
  in this session — assertion read from `app.query_one(StatusLine)`
  AFTER the with block, by which time the widget was unmounted.
  Capture into a local before the with-block exits.

### Mount-write notes

Heredoc-rewrote app.py and todo.md as usual. KeyBar, StatusLine, and
test_status_keybar.py landed clean first try via cp+sync from /tmp.
queue.py was also rewritten via heredoc to add `running_progress` +
`on_item_progress` — and a later linter-style touchup adjusted some of
the cosmetic comments without changing behaviour.

### Surfaced during implementation (added to follow-ups)

- **KeyBar should read `_WIRED` from BINDINGS at runtime** rather than
  hardcoding the set. Hardcoded keeps things simple for v0; the
  refactor is straightforward when there's a `letter` ↔ `F-key`
  mapping somewhere central.
- **No "Backspace at root" / "→ on file row" feedback yet.** Now that
  the StatusLine exists, the no-op cases can write helpful nudges.
- **Tab focus → status refresh.** Currently `action_cycle_focus` calls
  `_refresh_status` so the path under the newly-focused pane's cursor
  shows. Feels right today; verify when more bindings have status
  contributions.
- **F-key bar background and palette.** Cyan-on-black is the MC look;
  green-on-black is XTree's. A theme pass would offer both. Parking lot.

### Next session pickup

todo item 5 (Copy) and todo item 6 (Status line + F-key bar) are now
complete. Three logical next picks, in rough order of value:

1. **Bind Move (`M` / F6).** Mirrors Copy: plan -> modal -> enqueue.
   `apply_plan` dispatcher grows a Move adapter (same-fs = `os.rename`;
   cross-fs = copy then delete). Reuses almost everything we've built;
   first chance to test the dispatch table's "another adapter" path.
2. **Bind Delete (`D` / Del / F8).** Confirm dialog (reuse PromptDialog
   or add a tiny ConfirmDialog). Plan-side is trivial (no destination,
   just the source list); execute is `os.unlink` / `shutil.rmtree`.
3. **Wire `/` incremental search.** Local to the focused pane; reuses
   the modal-input pattern but in a non-modal form. Bigger UX scope
   than Move/Delete.

My recommendation is option 1 — Move is the smallest delta and proves
the dispatch table generalises. Delete after that. Search is its own
puzzle and deserves a clean session.

---

## 2026-05-21 (later still^3) — Move (todo item: Move done)

The dispatch table's "another adapter" path is now real. Move (`M` /
F6) lands end-to-end through the same plan -> modal -> queue ->
execute chain Copy uses, with the executor switching on
``plan.kind`` to pick the right adapter. 26 new tests, **102/102
green** on mount.

### What landed

**New ``wtree/ops/move.py``** — ``plan_move(tags, destination, registry)``.
The headline difference vs ``plan_copy``: Move does NOT recurse. One
``PlanItem`` per top-level tag, because ``shutil.move`` handles whole
subtrees in a single syscall when src and dst share a filesystem (and
falls back to copy + delete when they don't). Flattening like Copy
would force N rename calls where 1 suffices.

Destination mapping is identical to Copy: ``{dest}/{basename(tag)}``,
joined with POSIX semantics regardless of source platform. Errors
follow the same in-band ``PlanError`` pattern as Copy. Source-root
tags (``"/"``) are rejected (either by the source's ``entry_at`` or
by a defensive ``UnrootedTag`` check in the planner — both paths
covered by tests).

**``wtree/ops/execute.py`` extended.** The dispatcher now switches on
both ``(plan.kind, src_source_id, dst_source_id)``. The
``("native","native")`` pair handles ``OperationKind.COPY`` via the
existing ``_native_copy`` adapter and ``OperationKind.MOVE`` via the
new ``_native_move`` adapter. The dispatch helper is one private
function each so per-kind failure modes have clean per-line messages.

``_native_move``:

- Computes ``dst`` with the same Windows-path-normalisation helper Copy
  uses (factored out as ``_normalise_dst``).
- ``mkdir -p`` the destination parent — matches Copy's behaviour and
  lets typed destinations like ``/tmp/new-name`` work.
- **Pre-checks ``dst`` existence with ``os.path.lexists``.** Critical
  guard: ``shutil.move(src, dst)`` when dst is an existing directory
  silently nests src INSIDE it (i.e. ``dst/basename(src)``). That would
  surprise the user. Failing fast is safer; the modal-driven overwrite/
  skip/rename UX is parking lot.
- Skips ``Kind.OTHER`` (sockets/devices/fifos) — ``shutil.move``
  can't sensibly handle these.
- Delegates the actual move to ``await asyncio.to_thread(shutil.move,
  src, dst)`` — keeps the Textual event loop responsive on giant
  cross-fs moves the same way Copy does.

**``WTreeApp.action_move``** — mirrors ``action_copy``. Both are now
``@work``-decorated one-liners that delegate to a new
``_plan_modal_enqueue(verb=..., planner=..., kind=...)`` helper.
DRY's out the ~60 lines of Selection-rule + modal + planner + notify
boilerplate. Bindings: ``("m","action_move","Move")`` and
``("f6","action_move","Move")``.

The notification title now uses ``plan.kind.value.capitalize()`` so
``Move (done)`` / ``Move (done with errors)`` work without per-action
plumbing. StatusLine already reads ``running.kind.value.capitalize()``
so mid-move it renders ``Move: 2/3 items`` automatically — no widget
changes needed.

**``KeyBar._WIRED`` updated** to ``{5, 6, 10}`` so F6 lights up bold
cyan instead of dim. Module docstring now also mentions F6.

### Tests — 26 new, 102 total

- ``tests/test_ops_move.py`` (13) — planner shape: file into dir, dir
  preserves-but-doesn't-flatten subtree, mixed tags, summary text,
  unknown source, missing path, empty tag list, cross-source dst
  paths, unrooted-source guard. Pilot tests for ``action_move``:
  cursor-fallback, tagged-set path, Esc cancellation, empty-pane
  warning.
- ``tests/test_ops_execute.py`` (8 new, now 17 total) — Move-specific
  executor tests: single file rename, dir + subtree (one PlanItem
  contract), missing-parent mkdir, dst-exists pre-check refuses to
  clobber, cross-fs fallback (forced by monkeypatching ``os.rename``
  to raise EXDEV), missing source, cross-source FAILED, progress
  callback per item.
- ``tests/test_move_e2e.py`` (4) — full pilot chain: cursor-entry
  single file (relocated AND source gone), tagged dir with subtree
  (whole tree moves; one PlanItem; tagged set clears), two M presses
  serialise FIFO (with explicit ``down`` between since contents pane
  doesn't auto-refresh after moves write to its directory),
  subtitle returns to baseline post-drain.
- ``tests/test_status_keybar.py`` (1 new, now 6 total) —
  ``_WIRED`` contains 5 + 6 + 10 and does NOT contain 1/2/7 (catches
  accidental bulk-enable).

102/102 on mount in 19.35s, same on sandbox.

### Decisions and rationale

- **One PlanItem per top-level tag for Move, not flatten.** The
  underlying syscall (``os.rename``) is whole-subtree. Flattening
  would force N calls and lose the rename fast-path. Copy still
  flattens because its underlying call (``shutil.copy2``) is per-file.
- **``shutil.move`` instead of hand-rolling rename + copy fallback.**
  Stdlib already has the EXDEV detect + copy-and-delete dance. Rolling
  our own would invite per-platform bugs (Windows + macOS + Linux
  ``rename`` semantics for existing dst differ in unhelpful ways).
- **Pre-check ``lexists`` instead of trusting ``shutil.move``'s default.**
  ``shutil.move`` does "move INTO existing dir" when dst is a dir,
  which would silently nest user data deeper than intended. Failing
  fast is the conservative v0 behaviour; the friendly overwrite/skip/
  rename dialog is parking lot.
- **``_plan_modal_enqueue`` helper.** Move and Copy share 60 lines of
  Selection rule + modal + planner + notify + queue boilerplate.
  Pulling it out now (with 2 callers) sets the stage for Delete and
  Rename to slot in as one-liners. The differences are exactly:
  (1) verb string, (2) planner function, (3) operation kind.
- **Test for cross-fs forced via ``monkeypatch``.** Setting up two
  filesystems in a unit test is non-portable; patching ``os.rename``
  to raise EXDEV deterministically exercises the fallback path
  without requiring loop devices or tmpfs. The patched test verifies
  both that ``rename`` was attempted AND that the fallback succeeded.
- **Symlinks on cross-fs move dereference, per ``shutil.move``.**
  Same-fs case is fine (``os.rename`` preserves links). Cross-fs is
  an acknowledged v0 compromise; logged in follow-ups. Add a
  ``copy_function=lambda s,d: shutil.copy2(s,d,follow_symlinks=False)``
  override later if it bites.

### Mount-write notes

Heredoc-rewrote everything per the protocol. Nine staged files (3
new, 5 modified, plus ``todo.md``), all byte-identical first try via
``cp .tmp + sync + mv -f + sync + cmp -s``. No bit-rot this session
— but I checked anyway with ``stat -c %s`` and ``wc -l`` from bash
on every staged file. The full sandbox pytest run passed, then
``pip install -e .`` against the mount + a second pytest run also
passed: catches any post-stage divergence.

### Surfaced during implementation (added to follow-ups)

- **Contents pane doesn't auto-refresh after own writes.** The two-
  moves e2e test had to press ``down`` between moves; row 0 still
  shows the now-moved one.txt until the user navigates. Until FS-
  watching lands (parking lot), an explicit refresh hook on
  ``OperationResult`` completion would feel snappier.
- **Move summary undercounts for big dirs.** ``Plan.summary()`` reads
  ``file_count`` / ``total_bytes`` which only see top-level items.
  ``"move: 1 dir, 4 KB"`` is misleading when the actual operation is
  ``"1 dir, 12 GB"``. Optional: walk for accounting only, still emit
  one PlanItem per tag for execute.
- **Cross-fs symlink moves dereference.** Follow-up logged.
- **Overwrite policy differs between Copy and Move today.** Copy
  clobbers files via ``shutil.copy2``; Move pre-checks and fails.
  Should be unified at plan time via a dialog.
- **Move skips Kind.OTHER, matching Copy.** Documenting in case
  anyone asks.

### Next session pickup

todo items 5 (Copy), 6 (Status line/KeyBar), Move are done. Three
natural next picks:

1. **Bind Delete (``D`` / Del / F8).** Plan side is the cleanest of
   any remaining op (no destination, just the source list). Needs a
   tiny ``ConfirmDialog`` (could reuse ``PromptDialog`` with a yes/no
   contract, or land a proper modal). Execute is ``os.unlink`` /
   ``shutil.rmtree``. Slot directly into ``_plan_modal_enqueue`` (with
   the modal swapped for the confirm dialog).
2. **Bind Rename (``R`` / F2).** Single-entry only per design;
   rejects when tagged set is non-empty with a status-line nudge.
   ``PromptDialog`` reused; planner ``plan_rename(tag, new_name,
   registry)`` is dead simple; execute is ``os.rename``.
3. **Wire ``/`` incremental search.** Bigger UX scope than Delete/
   Rename; reuses modal-input pattern but in a non-modal form
   (cursor + Input below). Deserves its own session.

My recommendation is option 1 — Delete is the most-used op after
Copy/Move and proves the dispatch table's third op slot. The
ConfirmDialog also unblocks any future destructive operation.

---

## 2026-05-21 (later still^4) — Delete (todo item: Delete done)

The dispatch table's third operation slot is now real. Delete (``D``
/ Del / F8) lands end-to-end through a new ``ConfirmDialog`` and the
same queue/executor chain Copy and Move use, with a new
``_plan_confirm_enqueue`` helper that mirrors ``_plan_modal_enqueue``
for destinationless operations. 26 new tests, **128/128 green** on
mount.

### What landed

**New ``wtree/widgets/confirm.py``** —
``ConfirmDialog(ModalScreen[bool])``. The dual of ``PromptDialog``:
where Prompt collects a typed string (or cancellation), Confirm
collects a boolean.

* ``Y`` or ``Enter`` -> dismiss ``True``.
* ``N``, ``Esc`` -> dismiss ``False``.
* Optional ``body`` parameter shows up to 5 detail lines + a "+N more"
  tail; useful for "delete THESE 3 items: ...". Truncation cap
  matches the notify summary cap.
* Border colour ``$error`` (red) instead of ``$primary`` (blue) so
  destructive operations look visually distinct from ``PromptDialog``.

**New ``wtree/ops/delete.py``** — ``plan_delete(tags, registry)``.
Note the signature: no ``destination`` parameter, because there isn't
one. Headline differences from ``plan_copy`` / ``plan_move``:

* One ``PlanItem`` per top-level tag (like Move, unlike Copy);
  ``shutil.rmtree`` handles whole subtrees in one syscall.
* ``PlanItem.dst_source_id = src_source_id`` and ``dst_path = ""``
  as sentinels - the executor's dispatch table sees the dst fields
  but ignores them for DELETE.
* ``UnrootedTag`` guard refuses tags whose basename is empty
  (i.e. source root ``"/"``). Same conservative stance as
  ``plan_move``; matches the design's "errors as data" pattern.

**``wtree/ops/execute.py`` extended.** The dispatcher now checks
``plan.kind is DELETE`` *before* the (src, dst) pair check, because
DELETE doesn't have a meaningful dst:

```python
if kind is OperationKind.DELETE:
    if item.src_source_id == "native":
        return await _native_delete(item)
    raise NotImplementedError(...)

pair = (item.src_source_id, item.dst_source_id)
if pair == ("native", "native"):
    if kind is COPY: ...
    if kind is MOVE: ...
```

``_native_delete`` is short and explicit:

* ``Kind.DIR`` -> ``await asyncio.to_thread(shutil.rmtree, src)``.
* ``Kind.FILE``, ``Kind.SYMLINK`` -> ``await asyncio.to_thread(os.unlink, src)``.
* ``Kind.OTHER`` -> SKIPPED, matching copy/move.

``os.unlink`` on a symlink removes the link itself, not the target -
this is the correct behaviour (XTree, Norton, and every modern file
manager work this way) and is explicitly tested.

**``WTreeApp`` wiring.**

* Bindings: ``d``, ``delete``, ``f6``-but-actually-``f8`` ->
  ``action_delete``. Three keys is intentional - Del is the
  Windows-Explorer reflex; D is the XTree muscle memory; F8 is the
  MC F-key alias.
* New ``_plan_confirm_enqueue(verb, planner, kind)`` helper - the
  yes/no dialog dual of ``_plan_modal_enqueue``. Same shape:
  Selection rule -> dialog -> planner -> ``_finalise_plan``.
* Refactored common tail into ``_finalise_plan(plan, tags, verb,
  destination_path=None)`` - both action helpers were duplicating the
  "record last_plan; enqueue; clear tagged set; build notify body;
  update subtitle; refresh status" boilerplate. Now it lives in one
  place, with the ``destination_path`` arg optional so Delete's
  no-destination case doesn't render ``-> None`` in the notify line.

The notification title uses ``plan.kind.value.capitalize()`` already,
so ``Delete (done)`` / ``Delete (done with errors)`` work without
per-action plumbing. StatusLine reads ``running.kind.value.capitalize()``
so mid-delete it renders ``Delete: 2/3 items``.

**``KeyBar._WIRED`` updated** to ``{5, 6, 8, 10}`` so F8 lights up.

### Tests — 26 new, 128 total

- ``tests/test_ops_delete.py`` (14) - planner shape: single file,
  dir-emits-one-item, mixed tags, summary text, unknown source,
  missing path, empty tag list, refuses source root. Pilot tests
  for ``action_delete``: cursor-fallback + Y, cursor + Enter alias,
  cursor + N cancel, cursor + Esc cancel, tagged-set path + Y,
  empty-pane warning.
- ``tests/test_ops_execute.py`` (6 new, now 23 total) - delete
  executor: single file removal, dir+subtree (rmtree contract),
  symlink-not-target (Unix-only, skipped on Windows), missing source
  FAILED, cross-source FAILED, progress callback per item.
- ``tests/test_delete_e2e.py`` (5) - cursor-entry single file,
  tagged dir with subtree (one PlanItem; tagged set clears), N keeps
  file (no plan enqueued), two D presses serialise (with Down between
  for stale-pane workaround), subtitle returns to baseline.
- ``tests/test_status_keybar.py`` (1 new, now 7 total) - ``_WIRED``
  contains 5+6+8+10, doesn't contain 1/2/7.

128/128 on mount in 27.20s (one second slower than 102/102 was -
linear scaling, no test-suite hotspots emerging).

### Decisions and rationale

- **``dst_*`` as sentinel mirrors for DELETE PlanItem.** Considered
  making ``dst_*`` optional on ``PlanItem`` (``Optional[str]``) but
  that would ripple through every consumer (dispatcher, callbacks,
  serialisation). Setting ``dst_source_id = src_source_id`` and
  ``dst_path = ""`` keeps the dataclass shape uniform across ops;
  the executor's DELETE branch never inspects them. Documented in
  the planner module docstring.
- **DELETE check goes BEFORE pair check in dispatcher.** ``dst_*``
  for DELETE is sentinel, so the (src, dst) pair check would match
  ``("native","native")`` even though there is no destination. Better
  to short-circuit on ``plan.kind`` first. The structure also
  generalises: future destinationless ops (a hypothetical "touch
  mtime" or "chmod") would slot in the same way.
- **``ConfirmDialog`` is generic, not Delete-specific.** Same
  reasoning as ``PromptDialog`` being shared across Copy/Move:
  one widget, many users. If overwrite-during-copy ever lands, that
  prompt is also a Y/N gate.
- **``_plan_confirm_enqueue`` vs growing ``_plan_modal_enqueue``.**
  Considered adding a ``confirm_only=True`` flag to the existing
  helper. Decided against - the two dialog types have different
  signatures, return types, and required parameters; a flag would
  muddy the helper. Two helpers calling a shared ``_finalise_plan``
  tail keeps each variant focused.
- **Y is the affirmation key, Enter is an alias.** Hardware
  Y-vs-Enter is a UX preference that splits people. XTree and DOS
  era assumed Y; modern Linux assumes Enter. Both work. The
  ConfirmDialog hint line lists all four accepted keys so neither
  habit is wrong.
- **Border colour ``$error`` for ConfirmDialog.** Textual's theme
  has semantic colours; using the error/warning red here flags
  "this dialog leads to destruction" visually. PromptDialog stays
  ``$primary`` (the cool blue) because typing a path isn't itself
  destructive.

### Mount-write notes

Ten files staged this session (4 new, 6 modified, plus ``todo.md`` and
``worklog.md`` updates), all byte-identical first try via the standard
``cp .tmp + sync + mv -f + sync + cmp -s`` protocol. No bit-rot
encountered; ``stat -c %s``, ``wc -l`` and ``python3 -m pytest`` from
the mount all agreed with the sandbox. The "two pytest runs (sandbox
then mount)" check from the previous session caught nothing this
time, but is worth keeping in the rotation as a tripwire.

### Surfaced during implementation (added to follow-ups)

- **Pane auto-refresh after delete.** Same situation as Move - the
  contents pane keeps stale rows until manual navigation. The two-
  deletes e2e test does the same Down-between-ops workaround the
  move test does. A shared post-op refresh hook would catch both.
- **Delete summary undercount for big dirs.** Inherited from Move's
  one-item-per-tag emit pattern. Same fix would solve both.
- **ConfirmDialog "show all paths" toggle** for huge tagged sets -
  v0 caps at 5 lines + ellipsis.
- **rmtree partial-failure attribution.** A multi-file rmtree that
  fails mid-tree reports one FAILED with no per-file detail. Future
  progress dialog with streaming would surface this.
- **Soft-delete (trash) integration.** XTree had Y for trash vs D
  for delete; v0 only does hard delete. Stdlib doesn't have a
  cross-platform trash API, so this is parking-lot until
  ``send2trash`` or equivalent gets evaluated.

### Next session pickup

todo items 5 (Copy), 6 (Status line/KeyBar), Move, Delete are done.
Three natural next picks, in rough order of value:

1. **Bind Rename (``R`` / F2).** Single-entry only per design.md;
   rejects when tagged set is non-empty with a status-line nudge.
   Reuses ``PromptDialog`` (typed new name); planner is dead simple
   (one tag in, one PlanItem out, dst_path = parent + new_name);
   executor uses ``os.rename``. Smallest delta from existing
   scaffolding.
2. **Wire ``/`` incremental search.** Local to the focused pane;
   reuses the modal-input pattern but in a non-modal "inline" form
   (cursor + Input below the pane). Bigger UX scope than Rename;
   deserves a clean session.
3. **Bind View (``V`` / F3).** Built-in pager. New widget for
   reading text files within the app. Bigger than View/Edit's other
   half: needs scroll, search-within-file, charset detection.

My recommendation is option 1 - Rename closes out the "file ops"
checklist with the smallest remaining delta, and the single-entry-
only constraint is a nice exercise of the "reject with status nudge"
pattern that future ops will reuse.

---

## 2026-05-21 (later still^5) — Rename (todo item: Rename done)

Rename (``R`` / F2) lands as the v0 black sheep: single-entry only,
basename-only, rejects when the tagged set is non-empty. The rest of
the chain (modal -> planner -> queue -> executor) is the same shape
as the other ops. 31 new tests, **159/159 green** on mount.

### What landed

**New ``wtree/ops/rename.py``** — ``plan_rename(tag, new_name, registry)``.
Signature is intentionally different from the other planners: a
single ``Tag`` rather than ``Sequence[Tag]``, because rename is
single-entry by design. Other v0 differences:

* No ``destination`` parameter - the destination is computed from
  ``parent(tag.path) + new_name``.
* Rejects ``new_name`` containing ``/``, ``\\``, or ``os.sep`` with
  an ``InvalidName`` error - rename is basename-only, "move-by-
  typing-a-path" is a Move operation.
* Rejects empty / whitespace-only names with ``InvalidName``.
* Rejects same-as-current name with ``NoChange`` - no point queuing
  a no-op.
* Strips leading/trailing whitespace from the typed name (common
  typo, do the sensible thing).
* ``dst_source_id`` always equals ``src_source_id`` - renames never
  cross sources.

**``wtree/ops/execute.py`` extended.** The dispatcher gains a RENAME
branch (checked alongside DELETE, before the (src, dst) pair check
that handles Copy/Move). ``_native_rename``:

* ``await asyncio.to_thread(os.rename, src, dst)``.
* Pre-checks ``os.path.lexists(dst)`` and refuses with a FAILED
  ItemResult rather than silently clobbering - matches Move's
  conservative stance and produces consistent cross-platform UX
  (POSIX and Windows ``rename`` behave differently for existing dst;
  the pre-check normalises that).
* ``os.rename`` is atomic on POSIX (``rename(2)``) and on Windows
  same-volume operations; for a rename within the same parent dir,
  the inode doesn't move, so big directories rename in O(1).
* Renaming directories carries the whole subtree along with no
  separate handling - the inode just gets a new name.

**``WTreeApp.action_rename``** — different shape from the other
action_* methods. Doesn't use ``_plan_modal_enqueue`` /
``_plan_confirm_enqueue`` because:

* Selection rule is inverted - tagged set is *rejected*, not used.
* Modal input is a basename (not a destination path).
* Modal default is the current basename, not a directory.
* Planner signature is ``(tag, new_name, registry)`` not
  ``(tags, dest, registry)``.

So ``action_rename`` inlines the Selection-rule rejection + cursor
fetch + modal + planner, then calls the existing ``_finalise_plan``
tail for the enqueue + notify + refresh boilerplate. The rejection
on tags-present emits a notify with ``severity="warning"`` and a
``"Rename rejected"`` title; the message ("Rename works on one
entry; clear tags first (Ctrl+U).") matches design.md spirit.

**``_finalise_plan`` reused** without modification - already had
``destination_path=None`` support from the Delete session for
destinationless ops, which Rename takes advantage of (no "-> dest"
in the notify line).

**``KeyBar._WIRED`` updated** to ``{2, 5, 6, 8, 10}``.

### Tests — 31 new, 159 total

- ``tests/test_ops_rename.py`` (19) - planner shape: file rename,
  dir rename, summary text, unknown source, missing path, empty
  name, whitespace-only name, slash in name, backslash in name,
  same-as-current (NoChange), whitespace-stripped, trailing-slash
  preservation. Pilot tests for ``action_rename``: cursor + type +
  Enter, tagged-set rejection (the design.md headline), Esc cancel,
  empty input cancel, separator-in-name surfaces InvalidName,
  same-name surfaces NoChange, no-cursor warning.
- ``tests/test_ops_execute.py`` (6 new, 29 total) - executor:
  single file, dir + subtree, dst-exists pre-check refuses,
  missing-source FAILED, non-native source FAILED, progress callback.
- ``tests/test_rename_e2e.py`` (5) - full pilot: file rename,
  dir+subtree rename, tagged-set rejection produces no modal/no plan,
  modal default = current basename, subtitle returns to baseline.
- ``tests/test_status_keybar.py`` (1 new, 8 total) - ``_WIRED``
  contains 2+5+6+8+10. **Also fixed** the older keybar tests
  (``includes_f6``, ``includes_f8``) which had stale
  ``assert 2 not in _WIRED`` assertions that fire now that F2 is
  wired.

159/159 green on mount in 29.08s.

### Decisions and rationale

- **Single-entry only is enforced at the action layer, not the
  planner.** ``plan_rename`` takes one Tag; the action layer is
  what rejects when the tagged set is non-empty. This keeps the
  planner pure (it can't know about UI state); the rejection is a
  UX concern that lives where UX concerns live.
- **Reject path separators in the new name.** Considered allowing
  them and treating as move-disguised-as-rename. Decided against -
  user intent is unclear ("did I mean to rename to a name that
  happens to look like a path? Or to move?"), and Move (M / F6) is
  one keypress away if that's actually wanted. Better to error
  early than to surprise.
- **Reject same-as-current name (NoChange).** A user who types
  exactly the current name and presses Enter is probably confused
  about the dialog state; treating it as a no-op silently would
  hide that confusion. The error message ("new name is identical
  to current") tells them what happened.
- **Default the modal input to the current basename, not blank.**
  Two reasons: (1) the user usually wants to tweak (e.g. change
  extension or add a suffix), not retype; (2) the modal becomes
  self-documenting - they see what they're renaming. A future
  enhancement is to pre-select the basename-without-extension so
  typing replaces the stem and keeps the extension.
- **Strip whitespace from the typed name.** A trailing space is a
  common typo (e.g. accidentally pressing space before Enter); silently
  trimming it is the only sensible behaviour. An OS-level trailing-
  space filename is *valid* on most filesystems but almost never
  intentional.
- **Reject in ``plan_rename`` even though the action layer also
  rejects.** Belt-and-braces: callers calling ``plan_rename``
  directly (smoke tests, future scripting) still get the validation.
- **``action_rename`` doesn't share ``_plan_modal_enqueue``.**
  Considered adding a ``"reject_when_tagged"`` flag plus a planner-
  signature variant. Decided two parallel call sites are clearer
  than one helper with three conditional branches. ``_finalise_plan``
  is the right level of sharing - the post-planner tail is
  genuinely identical across all four ops.
- **Status-line nudge vs notify.** design.md explicitly calls for
  a "status-line nudge" for the tagged-set rejection. v0 ships a
  notify toast because StatusLine has no transient-message API yet.
  Added a follow-up: implement ``StatusLine.flash(message,
  timeout=2.0)`` and route the rejection through it.

### Mount-write notes

Nine files staged this session (3 new, 6 modified, plus todo.md and
worklog.md updates), all byte-identical first try via the standard
protocol. Sandbox pytest then mount pytest both green - the two-step
verification continues to pay for itself.

### Surfaced during implementation (added to follow-ups)

- **Status-line nudge API** for transient messages - design.md
  called for it, v0 uses notify as a placeholder.
- **Smart cursor placement** in the rename modal: select the
  basename-without-extension so users can replace the stem and keep
  the extension (Finder / Explorer pattern).
- **Batch rename** - parking lot per design.md, but the single-entry
  guard at the action layer is where it'd be lifted.
- **Case-only renames on case-insensitive filesystems** (macOS
  HFS+, Windows NTFS by default). May need intermediate-name dance.
- **Reserved Windows names** (``CON``, ``NUL``, ``PRN``, ...) should
  get a planner-level rejection with a friendly InvalidName message.
- **Older keybar tests need updating** when new F-keys land. The
  ``"N not in _WIRED"`` assertion is a tripwire that fires when a
  new key joins the set. Caught here for F2; fix is to drop the
  stale negative assertion in the prior test.

### Next session pickup

todo items 5 (Copy), 6 (Status line/KeyBar), Move, Delete, Rename are
done. The "Bind file ops" checklist is complete for the destructive /
moving / renaming class of operations. Three natural next picks:

1. **Bind View (``V`` / F3).** Built-in pager. New widget for
   scrolling text files in-app without shelling out. Needs:
   scroll, optional line numbers, basic charset detection
   (UTF-8 / latin-1 / windows-1252 fallback), and a way to bail
   to the user's pager for files too big or binary. The first
   significant new widget since StatusLine/KeyBar.
2. **Wire ``/`` incremental search.** Local to the focused pane;
   reuses the modal-input pattern but inline (cursor + Input below
   the pane). Bigger UX scope than the file ops.
3. **Bind Make-new (``N`` / F7).** Dir or file sub-prompt. Smaller
   than View; the create-then-edit pattern is the natural sequel
   to Edit (which itself depends on shell-out plumbing that hasn't
   landed). Probably nice to do Make-new before Edit.

My recommendation is option 1 (View) - it unlocks a satisfying
"WTree can now actually show me file contents" milestone, and the
pager widget is the right place to invest before Edit lands (the
two share scroll/charset/binary-detect concerns). Make-new and
Edit are smaller in scope but feel more incremental.

---

## 2026-05-21 (later still^6) — View + skeleton-era cleanup

Two landings this session: the **View** (``V`` / F3) built-in pager,
and the two quick-win Skeleton-era follow-ups (sources re-exports
and ``python -m wtree``). **179/179 tests green** on mount.

### Part A — Skeleton-era cleanup

**``wtree/sources/__init__.py``** now re-exports ``NativeSource`` and
``MockSource`` alongside the base types. Callers can now write
``from wtree.sources import NativeSource, MockSource`` without
knowing the submodule layout. ``__all__`` updated.

**``wtree/__main__.py``** — new one-line module delegating to
``wtree.app.main``. Mirror of the ``wtree`` console-script entry.
Means ``python -m wtree`` works even when the ``wtree`` script isn't
on PATH (fresh installs, sandboxed envs).

**``tests/test_packaging.py``** — 3 smoke tests catching silent
regressions in the public import paths: sources re-exports resolve
to the same classes the submodules expose; ``__all__`` lists the
concrete sources; ``wtree.__main__`` exposes ``main`` matching
``wtree.app.main``.

todo.md ticked off two of the three Skeleton-era items. Cross-
platform owner lookup remains - flagged in the worklog memory as
"a small project on its own", deferred until there's actual demand.

### Part B — View / F3 pager

**New ``wtree/widgets/viewer.py``** — ``ViewerScreen(ModalScreen[None])``.

Loading model:

* File read happens in ``on_mount`` via ``asyncio.to_thread`` so
  big-file I/O doesn't block the Textual event loop. The modal
  frame appears immediately; the body fills in once bytes arrive.
* ``_load_file_sync`` is the synchronous worker. Never raises -
  every failure (missing file, unreadable, oversize, binary)
  becomes a ``_LoadResult.refusal`` string the viewer renders.
* Charset detection: UTF-8 first; on ``UnicodeDecodeError`` fall
  back to latin-1. latin-1 has a total decoding (every byte 0-255
  maps to a code point), so the viewer never crashes on funny
  encodings.
* Binary detection: peek first 8 KB, refuse if any NUL byte. Catches
  ELF, PNG, ZIP, etc. without needing a magic-number table.
* Size ceiling: ``MAX_BYTES = 10 MB``. Larger files get a refusal
  with a "use $PAGER externally" nudge. Configurable as a follow-up.
* Symlinks: followed by the underlying ``open`` call. The action
  layer admits ``Kind.SYMLINK`` alongside ``Kind.FILE``.

UI:

* Header label (dock top) shows path + size + encoding (or refusal
  one-liner). Body is a ``Static`` inside a ``VerticalScroll``;
  scroll handled by Textual for free (arrows, PgUp/PgDn, Home/End).
* Hint label (dock bottom) lists the close keys: Esc / Q. Bindings
  are explicit ``Binding`` objects on the screen rather than
  literal tuples - the literal-tuple form was rejected by Textual
  for ``Binding`` lookups on the new pattern.
* Border is thick ``$primary``; refusal text gets a warning colour.

**Action layer** - ``WTreeApp.action_view`` is a sync method (no
``@work``, no ``push_screen_wait``). It validates the cursor entry
kind and either pushes the viewer or emits a notify:

* ``Kind.FILE`` / ``Kind.SYMLINK`` -> push ``ViewerScreen(path)``.
* ``Kind.DIR`` -> notify ("press Enter to navigate into it").
* ``Kind.OTHER`` -> notify (kind name in the message).
* No cursor entry -> notify ("nothing under the cursor").

Bindings: ``v``, ``f3``. Both go through the same action.

**KeyBar ``_WIRED``** is now ``{2, 3, 5, 6, 8, 10}``.

### Tests - 17 new, 179 total

- ``tests/test_viewer.py`` (11) - unit + widget mount:
    * UTF-8 file decodes clean
    * Unicode (emoji + accents) round-trips
    * Invalid UTF-8 falls back to latin-1
    * Binary file (NUL bytes) refusal
    * Oversize file refusal (monkeypatched MAX_BYTES)
    * Missing file refusal (stat error)
    * Unreadable file refusal (open patched to raise)
    * ViewerScreen renders text body
    * ViewerScreen renders binary refusal
    * Esc dismisses
    * Q dismisses
- ``tests/test_view_e2e.py`` (5) - action_view pilot:
    * V on file opens viewer
    * F3 alias works identically
    * V on directory doesn't open viewer
    * V with empty pane doesn't open viewer
    * Esc returns to underlying screen (no lingering modals)
- ``tests/test_status_keybar.py`` (1 new, 9 total) - ``_WIRED``
  contains 2+3+5+6+8+10; F1/F4/F7/F9 still unbound. Older F2
  test had its stale ``3 not in _WIRED`` assertion dropped.

### Decisions and gotchas

- **``_render`` name clash.** First implementation defined a
  ``ViewerScreen._render_load_result`` helper as ``_render(self,
  result)``. Textual's ``Widget`` has its own ``_render(self)``
  method (no args), so every render attempt blew up with
  ``TypeError: _render() missing 1 required positional argument:
  'result'``. Renamed to ``_render_load_result``. Lesson: don't
  shadow Textual's internal underscore methods - they're not
  conventional protected, they're load-bearing.
- **Sync ``action_view``, not ``@work``.** Other action methods are
  ``@work async def`` because they ``await push_screen_wait`` for a
  user answer (destination, confirm, new name). View just wants to
  show a screen and let the user dismiss it; ``push_screen`` is the
  right primitive and ``@work`` is overhead. Future-Claude: don't
  copy-paste-with-``@work`` from the file-op actions.
- **Latin-1 fallback over chardet / ``charset-normalizer``.** Those
  libraries do proper detection but pull in dependencies and add
  startup latency. latin-1 is a total decoding - every byte maps to
  *some* code point - so the viewer never crashes, just sometimes
  displays mojibake. Acceptable v0 trade.
- **Binary detection via NUL scan, not magic numbers.** Files
  starting with ``ELF`` / ``\\x89PNG`` / ``PK\\x03\\x04`` etc. all
  contain NUL bytes within their first 8 KB. The NUL scan catches
  them without needing a libmagic-style table. False positives are
  vanishingly rare (text files rarely contain NUL).
- **``Binding`` objects in BINDINGS list.** Older actions used
  literal tuples ``("v", "view", "View")``; ViewerScreen uses
  explicit ``Binding(...)`` objects. Both work; Binding is more
  explicit about the description field. Mixing them in a single
  app is fine.
- **``MAX_BYTES = 10 MB``** is generous for plain text; pinned to
  avoid surprise OOMs on accidentally-tagged log files. Made
  configurable in a future settings-layer pass.

### Mount-write notes

Six files staged (3 new for View, 1 modified app.py, 1 KeyBar
tweak, 1 modified status_keybar test). Plus the three smaller
files for Part A (sources/__init__.py rewrite, new __main__.py,
new test_packaging.py). All byte-identical first try via the
standard cp+sync+mv-f+sync protocol. Sandbox pytest then mount
pytest both green.

### Surfaced during implementation (added to follow-ups)

A new **View-era follow-ups** section in todo.md collecting:

- In-viewer ``/`` incremental search (highlight + n/N step).
- Syntax highlighting via ``TextArea`` (read-only) + Pygments.
- Line-number gutter (trivial after TextArea swap).
- Streamed / paged read for huge files (relax the 10 MB ceiling).
- Hex mode for binary files (opt-in alternative to refusal).
- ``utf-8-sig`` for BOM detection before latin-1 fallback.
- Runtime-configurable ``MAX_BYTES``.
- Friendlier symlink-loop / dangling-target messaging.

### Next session pickup

Five items remain on the main "After the skeleton runs" list:

1. **Bind Edit** (``E`` / F4) - shell out to ``$VISUAL`` / ``$EDITOR``.
   Subprocess management: suspend Textual rendering during the
   subprocess (``app.suspend()``), restore on return. Shares
   "what file is this" logic with View - probably calls into the
   same kind-validation pattern from action_view.
2. **Bind Make-new** (``N`` / F7) - dir/file sub-prompt. Smaller
   than Edit; uses ``PromptDialog`` for the name and a tiny
   sub-prompt (dir vs file) via ``ConfirmDialog`` with custom
   labels, or a dedicated 3-option dialog.
3. **Wire ``/`` incremental search.** Local to the focused pane;
   reuses the modal-input pattern but inline (cursor + Input
   below the pane). Bigger UX scope.
4. **Bind menu bar** (``F9``) - MC-style top menu. Smallest of
   the new-widget tasks; mostly visual scaffolding.

My recommendation is **Edit (option 1)** - it pairs naturally with
View (same kind validation; complementary user gesture) and the
``app.suspend()`` machinery it needs is shared infrastructure for
any future shell-out (open-with, external diff, etc.). Make-new
is small enough to bundle in the same session if there's appetite.

---

## 2026-05-22 - Edit (E / F4) lands

**Goal recap.** The View session left exactly four items on the v0
checklist: incremental search (``/``), Edit (``E`` / F4), Make-new
(``N`` / F7), and the menu bar (F9). Per the last session's
recommendation we picked **Edit**: it pairs naturally with View (same
kind-validation skeleton, complementary user gesture), and the
``app.suspend()`` plumbing it needs is the reusable infrastructure
that future shell-outs (open-with, external diff, ``!`` shell prompt)
will all share.

### Decision: where Edit lives

View put its code in ``wtree/widgets/viewer.py`` because it's a modal
screen. Edit is *not* a modal - it's a shell-out that takes over the
real terminal via ``app.suspend()``. So putting the helpers in
``widgets/`` would mis-classify them. ``wtree/ops/`` was the other
candidate, but that package is specifically for ``Plan``-producing
operations that flow through the queue; Edit doesn't fit that shape
either.

Settled on a new top-level module: ``wtree/editor.py``. Two pure
helpers:

* ``resolve_editor() -> list[str]`` - applies the design's precedence
  (``$VISUAL`` -> ``$EDITOR`` -> platform default), ``shlex``-splits
  the chosen value so ``EDITOR="code --wait"`` survives.
* ``launch_editor_blocking(argv, path) -> int`` - appends the path to
  argv, runs ``subprocess.run`` synchronously, returns the exit code.
  No Textual dependency anywhere in this module, so unit tests are
  trivial.

Platform defaults: ``notepad`` on Windows; ``nano`` if
``shutil.which("nano")`` finds it on Unix, ``vi`` otherwise. Matches
``design.md`` Editing files section verbatim.

### Decision: the suspend seam

``App.suspend()`` is a context manager that hands the terminal over
to the wrapped block, then re-grabs it on exit. Reading Textual's
source:

```
@contextmanager
def suspend(self) -> Iterator[None]:
    if self._driver is None:
        return
    if self._driver.can_suspend:
        ...
        yield
        ...
    else:
        raise SuspendNotSupported(...)
```

The headless driver used by ``app.run_test()`` inherits
``can_suspend = False`` from the base ``Driver``, so calling
``with self.suspend(): ...`` from a pilot-driven test raises.

To keep tests honest we factored the suspend + subprocess into
``WTreeApp._launch_editor_blocking(argv, path)`` - a single seam that
tests monkeypatch with a fake. The fake records what was about to be
launched (argv, path) and can optionally mutate the target so we can
assert the post-edit pane refresh.

Tests never trigger a real ``app.suspend()``, never spawn a real
editor, and remain headless-driver clean.

### Decision: scope - tagged set vs cursor

``design.md`` Selection rule says "Commands operate on the tagged set
if it is non-empty; otherwise on the entry under the cursor. Rename
is the exception." Strictly, Edit should follow Selection rule.

But sending multiple file arguments to an external editor is
editor-specific: ``vim file1 file2`` opens tabs, ``code file1 file2``
opens both in one window, ``nano`` opens them sequentially, etc. The
contract WTree would be promising users isn't crisp.

View already took the same liberty for the same reason ("Single-entry
op (no Selection rule - viewing the tagged set makes no sense)").
Edit mirrors that choice: operate on the cursor entry, ignore the
tagged set. Documented in the action docstring. If users complain
that they want to batch-edit a tagged set, post-v0 can revisit -
likely via ``$EDITOR`` policy detection or a per-editor opt-in.

### The action body

``action_edit`` (in ``wtree/app.py``) is ``@work``-decorated because it
``await``s ``asyncio.to_thread`` on the blocking spawner. Flow:

1. Pull the cursor entry from ``ContentsPane``.
2. Reject: ``None`` -> "nothing under the cursor"; ``Kind.DIR`` ->
   "press Enter to navigate"; ``Kind.OTHER`` -> "cannot edit a {kind}".
3. ``argv = resolve_editor()``.
4. ``rc = await asyncio.to_thread(self._launch_editor_blocking, argv, path)``.
5. Catch ``FileNotFoundError`` -> notify "editor not found, set $VISUAL
   or $EDITOR". Catch any other ``Exception`` -> generic notify with
   exception class + message. Don't propagate; the action loop should
   stay responsive.
6. Non-zero exit -> warning notify (the editor exited with a status the
   user might care about) but flow continues.
7. ``await contents.show_path(contents.current_path)`` to refresh in
   case the file changed size / mtime / vanished.
8. ``self._refresh_status()``.

Plus a one-liner ``_launch_editor_blocking`` that wraps
``with self.suspend(): return launch_editor_blocking(argv, path)``.

### KeyBar update

``_WIRED`` is now ``{2, 3, 4, 5, 6, 8, 10}``. F4 (Edit) is no longer
dimmed. Module docstring's "Currently wired" line bumped accordingly.

### Tests landed

``tests/test_ops_edit.py`` (12 tests):

* ``resolve_editor`` env-precedence cases: VISUAL wins; empty VISUAL
  falls through; whitespace-only VISUAL is treated as unset; shlex
  splits a command with args; shlex respects quoted args.
* ``resolve_editor`` platform-default cases: Unix prefers nano if
  ``shutil.which`` finds it; falls back to vi if not; Windows always
  uses notepad. ``os.name`` is monkeypatched.
* ``launch_editor_blocking``: path appended to argv as final arg;
  exit code propagates; real ``/bin/true`` subprocess works
  end-to-end (skipped on Windows); ``FileNotFoundError`` surfaces
  when the binary doesn't exist.

``tests/test_edit_e2e.py`` (7 tests):

* E on a file invokes the suspend-and-spawn helper with the right
  argv + path.
* F4 alias works identically.
* E on a directory rejects without invoking.
* E with empty pane rejects without invoking.
* Post-edit refresh works (fake editor mutates the file, then we
  verify the on-disk change is visible).
* Non-zero exit code surfaces as warning, doesn't raise.
* ``FileNotFoundError`` from the spawner is caught, doesn't raise.

Plus ``test_keybar_wired_set_includes_f4`` in ``test_status_keybar.py``
(F4 is in the wired set; F1/F7/F9 still aren't).

The F2/F3 wired-set tests had stale ``4 not in _WIRED`` assertions
left over from the View session - cleaned up to assert only what was
true at the time those keys landed (the F4 test asserts the current
state).

### Numbers

* Pre-Edit: 179/179 green.
* Edit landing: 199 tests pass, 0 fail. (+12 unit + +7 e2e + +1 keybar.)
* Real ``/bin/true`` subprocess test exercises the spawner with no
  Textual involvement at all - confirms the spawn-and-wait surface
  is correct independent of the action layer.

### Mount caveats hit this session

* First ``Write`` of ``app.py`` succeeded on inspection (size matched)
  but Python parsing later showed the file was truncated mid-statement
  at line 547. Heredoc-rewrite to ``outputs/`` then atomic ``mv`` to
  the mount fixed it; the rewrite verified size before and after.
* ``Edit`` on ``keybar.py`` initially appeared to write through
  (``grep`` saw the change on disk, ``wc -c`` matched), but Python
  loading the same path saw the *old* contents. Final ``pytest`` run
  caught it - the F4 keybar test failed because ``_WIRED`` was still
  ``{2, 3, 5, 6, 8, 10}`` in memory. Heredoc-rewrite of the whole
  file fixed the inconsistency on the second pass.
* Lesson re-confirmed: after any non-trivial Edit to a file in the
  mount, **always** verify by reading back through Python (or running
  the relevant tests) before declaring the change done. The Edit tool
  and bash filesystem operations have inconsistent views of the
  mount; only "the bytes Python actually loads" is authoritative.

### Follow-ups identified during the work

Captured in todo.md under a new "Edit-era follow-ups" section. The
two big ones:

* **Status-line nudge instead of notify toast** - same shape as the
  Rename-era follow-up. The notify toasts are intrusive for
  "couldn't spawn editor" cases.
* **Suspend-friendly TUI re-entry** - after the editor exits we
  ``show_path()`` once but don't re-focus the pane that had focus
  before. Verify this feels right; otherwise add focus restoration.

### Next session

Per the v0 checklist three items remain: incremental search (``/``),
Make-new (``N`` / F7), menu bar (F9). Make-new is the smallest and
shares the modal-prompt + planner shape with Rename, so that's the
natural next pick. After that the search and menu bar can be tackled
in either order.

## 2026-05-22 — Make-new (N / F7)

Picked up the smallest of the three remaining v0 items: ``N`` / F7
"make new directory or file", which the design.md keymap had already
committed to. Architecturally the closest cousin to Rename — typed
input, a planner that emits one PlanItem, no Selection rule consumption
— but with three new UX shapes to nail down before code.

### Three decisions captured to design.md

Asked at the top of the session:

1. **Sub-prompt shape** — chooser modal then name prompt. Two screens,
   each unambiguous. The trailing-slash convention (``mydir/`` means
   directory) was tempting for the one-keystroke saving but loses too
   much clarity — easy to forget the slash, and the type matters more
   than the name. A combined radio-plus-input modal would have been a
   new widget shape; the chooser-then-prompt sequence reuses
   PromptDialog unchanged and matches XTree's keystroke-driven feel.

2. **Path separators** — lenient. ``foo/bar/baz`` is allowed and
   creates intermediate directories on apply. This diverges from
   Rename, which rejects separators because rename-with-path would be
   move-disguised-as-rename. Make-new starts from "no existing entry",
   so creating intermediates is the same scope of work the user is
   asking for. Implementation: planner accepts forward-slash-separated
   names, walks the segments and rejects ``..``; executor does
   ``os.makedirs(parent_of_leaf, exist_ok=True)`` before exclusive-
   creating the leaf.

3. **Parent dir** — ``ContentsPane.current_path`` (the directory the
   user is *looking at*). Tagged set silently ignored, cursor entry
   irrelevant. Make-new is a "create here" operation, not a Selection-
   rule operation. Mirrors View / Edit's stance with the additional
   twist that there's no per-op destination to wire through.

All three rows landed in the decision log at design.md:215.

### Implementation shape

Three new files plus four edits:

* ``wtree/ops/make_new.py`` — new module. Planner signature is
  ``plan_make_new(parent_path, name, kind, source_id, registry)``,
  different from the others which take a tag list. Kind comes in from
  the chooser modal so the planner doesn't infer it from a trailing
  slash. Rejections (PlanError causes): ``UnknownSource``,
  ``InvalidKind`` (kind not in DIR/FILE), ``InvalidName`` (empty,
  absolute, ``..`` segments, collapses to nothing), ``Exists`` (leaf
  already there). The leaf-exists check uses ``src.entry_at(leaf)``
  so it stays source-agnostic.

* ``wtree/widgets/kind_chooser.py`` — new modal. ``KindChooserDialog``
  is a ``ModalScreen[Kind | None]`` with three bindings: ``d`` →
  ``Kind.DIR``, ``f`` → ``Kind.FILE``, ``escape`` → ``None``. Looks
  like a small ConfirmDialog. Title and hint configurable but default
  to "Make new:" + "D for directory  -  F for file  -  Esc to cancel".

* ``wtree/ops/execute.py`` — new branch. ``_native_make_new`` dispatches
  to ``_make_new_blocking`` via ``asyncio.to_thread``. The blocking
  body is two lines per kind: ``os.makedirs(dst, exist_ok=False)`` for
  DIR, and ``os.makedirs(parent, exist_ok=True) + open(dst, "x")`` for
  FILE. The "x" mode is open-for-exclusive-create — raises
  ``FileExistsError`` if the leaf already exists, which the executor
  catches and converts to a FAILED ItemResult with "already exists"
  message. Belt-and-braces vs the planner's pre-check: a race between
  plan and apply surfaces as a clear failure rather than a silent
  overwrite.

* ``wtree/ops/base.py`` — added ``OperationKind.MAKE_NEW`` enum value.

* ``wtree/ops/__init__.py`` — exported ``plan_make_new``.

* ``wtree/app.py`` — added ``action_make_new`` (an ``@work`` async
  method that pushes the chooser, then the prompt, then planner, then
  ``_finalise_plan``). Added ``("n", "make_new", "New")`` and
  ``("f7", "make_new", "New")`` to BINDINGS. Updated the module
  docstring to describe Make-new's no-Selection-rule shape.

* ``wtree/widgets/keybar.py`` — bumped ``_WIRED`` from
  ``{2, 3, 4, 5, 6, 8, 10}`` to ``{2, 3, 4, 5, 6, 7, 8, 10}``.

### PlanItem shape detail

Make-new has no "from" path — the new entry doesn't pre-exist. But the
executor's dispatch table is keyed on ``(src_source_id, dst_source_id)``
and reads ``item.kind`` to pick the per-kind branch. Setting
``src_source_id == dst_source_id == source_id`` and
``src_path == dst_path == leaf_path`` lets the existing dispatch
machinery treat Make-new like any other op without introducing a
"destinationless" sentinel concept. ``size`` is ``0`` — new entries
are empty at birth.

### Tests landed

46 new tests across two files:

* ``tests/test_ops_make_new.py`` (38 tests):
  - 9 planner happy-path (simple file, simple dir, lenient subdirs,
    parent root, trailing-slash trim, whitespace strip, double-slash
    collapse, ``.`` drop, summary text).
  - 11 planner rejections (UnknownSource, SYMLINK kind, OTHER kind,
    empty, whitespace-only, absolute POSIX, Windows drive, ``..``,
    existing leaf dir, existing leaf file, ``.`` only).
  - 10 executor real-filesystem tests via ``tmp_path``: dir create,
    file create, lenient intermediate dirs, clobber refused for dir
    and file, unsupported-kind defensive, plus four full
    ``plan_make_new + apply_plan`` round-trip tests (dir, file,
    lenient, race-clobber).
  - 8 action-layer pilot tests: chooser then prompt, dir via chooser,
    chooser Esc cancels, prompt Esc cancels, empty name cancels,
    existing leaf surfaces Exists, ``..`` surfaces InvalidName,
    tagged-set silently ignored.

* ``tests/test_make_new_e2e.py`` (7 tests): real Pilot driving the
  full keystroke flow — dir, file, lenient subdirs, chooser cancel,
  prompt cancel, clobber refused, subtitle returns to baseline.

* ``tests/test_status_keybar.py``: new ``test_keybar_wired_set_includes_f7``;
  removed stale ``assert 7 not in _WIRED`` lines from the older
  per-op snapshots so they reflect current state without
  contradicting it.

Full suite: **245/245 green** (was 199 before this session, so +46).

### Mount-truncation incidents

Hit the bash-vs-Python disagreement (feedback rule 11) on three files
during this session — app.py, keybar.py, and test_status_keybar.py.
The Edit tool reported success and the mount view (grep, cat, sha)
looked correct, but Python's parser saw truncated content cut off
mid-statement. The recovery protocol from the feedback memory worked
exactly as documented: heredoc full content into ``/tmp/wtree-stage/``,
``cp + sync + mv -f + sync`` into the mount, then verify with a
Python import / pytest run. After the rewrite, mount and Python both
saw the same bytes. The same pattern hit design.md and todo.md during
the documentation pass; same recovery.

The takeaway: any non-trivial Edit on a mount-resident file >3 KB
needs a Python-side verification, not just a bash-side grep. Bash
sees a stale mirror more often than I'd expect. Heredoc-stage
+ atomic-mv is the reliable path for anything bigger than a small
patch.

### Follow-ups parked

A new "Make-new-era follow-ups" section in todo.md:

- Status-line nudge instead of notify toast (same shape as the
  Rename / Edit follow-ups).
- Pane auto-refresh after a make (shared post-op refresh hook).
- Pre-position cursor on the newly-made entry.
- Initial-name suggestion in the prompt (``New Folder`` / ``untitled.txt``).
- Symlink creation (rejected as InvalidKind today; needs a target prompt).
- Tagged-set "copy template" semantics (XTree-ish "duplicate as").
- Umask vs explicit mode for created entries.
- Case-only collisions on case-insensitive filesystems.

### Next session

Two v0 items remain: incremental search (``/``) and the menu bar (F9).
Search is the more interesting of the two — it's a non-modal inline
input with a new "what does ``/`` look like in the contents pane?"
design question. F9 is mostly UI plumbing once the menu structure is
worked out. Either order works.

## 2026-05-22 (late) — Left-on-root ascend

Course correction mid-session. After Make-new landed, Matthew asked how
hard it would be to bind ascend-and-relog to Left-on-root in the tree
pane — pressing Left while the cursor is on the root re-roots the tree
at the parent directory. XTree's "widen the logged window" idiom; a
clean win because Left-on-root was a dead key (the root has nothing to
collapse to and no parent in the existing default).

### Three design decisions, captured to design.md

The keystroke shape was already what Matthew proposed. Two follow-up
options he also wanted but parked for now:

- Backspace on the tree pane as a parallel binding (would mirror the
  contents pane's "go to parent dir" Backspace).
- Blank-Enter inside the existing ``L`` "Log new source" prompt — when
  the user just hits Enter without typing anything, default to the
  parent of the current root.

Both are on the new Ascend-era follow-ups list. v0 ships Left-on-root
only.

The other locked decisions:

- **No-parent behaviour.** ``os.path.dirname(root) == root`` is the
  canonical "at filesystem root" signal — works for ``/`` on POSIX
  and ``C:\\`` on Windows. UNC server-level paths (``\\\\server\\share``)
  are noted as a Windows-specific spot to verify; SMB browsing is
  parking-lot.
- **Status feedback.** Notify-toast for "Logged: NEW (ascended from
  OLD)". The eventual ``StatusLine.flash`` API is on the follow-ups
  list, same as the equivalent Rename / Edit / Make-new notes.
- **Cursor + contents pane after ascend.** Cursor lands on the row
  representing the old root (so the user can Right-arrow back in).
  Contents pane stays on the old root's contents because the cursor-
  driven NodeHighlighted handler picks the new cursor's path. Net
  effect: tree widens, working context stable.

### Implementation shape

Three edits and one new test file:

* ``wtree/widgets/tree_pane.py`` — new ``AscendRequested`` Message
  class on TreePane; new ``on_key`` override that intercepts Left
  only when ``cursor_node is self.root`` (consumes the event with
  ``event.stop() + event.prevent_default()``, posts the message); new
  ``re_root(path)`` method (wipes children, resets root data and
  label, clears ``_loaded`` memo, re-populates and re-expands); new
  ``focus_child_of_root(path)`` method (yields once via
  ``await asyncio.sleep(0)`` so Textual's lazy line indexer rebuilds
  before reading ``child.line``).

* ``wtree/app.py`` — new ``on_tree_pane_ascend_requested`` handler.
  Computes ``os.path.dirname(self._root_path)``, no-ops with notify
  nudge if ``new_root == old_root`` (filesystem root), otherwise
  re-roots the tree, focuses the old-root child row, and emits the
  "Logged: NEW (ascended from OLD)" notify. Does NOT explicitly
  refresh the contents pane — the cursor-driven NodeHighlighted
  handler does that.

* ``tests/test_ascend.py`` — 8 new tests: basic ascend, cursor lands
  on old-root row, contents stays on old root + Up moves cursor up to
  new root (contents follows), filesystem-root no-op, non-root Left
  still collapses (default Textual behaviour preserved), tagged set
  survives, two consecutive ascends, trailing-slash root.

### One bug surfaced, two design refinements

First implementation set the cursor with ``cursor_line = child.line``
and got ``line == -1`` because Textual's line indexer rebuilds
lazily on the next render — the indexer hadn't seen the freshly-
populated children yet. Adding ``await asyncio.sleep(0)`` before
reading ``child.line`` yields once, the render cycle runs, the
indexer rebuilds, and ``child.line`` returns a valid value. One-line
fix in ``focus_child_of_root``. Worth documenting as a Notes-for-the-
next-session entry — it's the kind of thing easy to forget and
re-hit later.

Second issue caught in the same test pass: the handler explicitly
called ``contents.show_path(new_root)`` after focusing the old-root
row. But ``focus_child_of_root`` already fires NodeHighlighted via
``cursor_line`` reactive, and the app's existing
``on_tree_node_highlighted`` handler drives the contents pane. So
two writes raced — the explicit one set ``contents`` to ``new_root``,
the event-driven one set it to ``old_root``. On reflection the
event-driven path is the better UX (working context stable), so the
explicit call was removed. The test for "contents pane after ascend"
got renamed and updated to assert old-root contents + a follow-up
"press Up to see new root" step.

### Tests landed

8 new tests in ``tests/test_ascend.py``. Full suite **253/253 green**
(was 245).

### Mount-truncation: still present, still annoying

Hit truncation on app.py, tree_pane.py, test_ascend.py, design.md,
and todo.md during this session. Each time the Edit tool reported
success and the bash view looked correct, but Python's parser saw
truncated content (rule 11). The heredoc-stage + atomic-mv protocol
recovered each one without fuss. Two new "Notes for the next
session" entries:

- Re-rooting needs an ``asyncio.sleep(0)`` yield before
  ``child.line`` reads.
- Cursor-driven NodeHighlighted is the right path to update the
  contents pane after re-root.
- Tree-pane ``on_key`` lets you intercept individual keys without
  overriding the whole Tree default.

### Follow-ups parked (Ascend-era)

Eight items on the new Ascend-era follow-ups list:

- Backspace-on-tree-pane parallel binding.
- Blank-Enter ascend in the ``L`` prompt.
- Preserve old expansion state under the new root after re-root.
- ``StatusLine.flash`` API for non-toast status feedback.
- **Passive folder-change detection with idle debounce** — Matthew's
  bigger idea: cheap periodic diff of the displayed dir against its
  cached list, surfaced via status nudge when contents have drifted
  on disk. Bound the overhead with a size threshold (~1000 entries
  bails), a min interval (~10 seconds between checks), and only the
  current contents pane (not the tree). A predecessor to full
  FS-watching without OS-specific watch APIs.
- UNC path ascend spot-check on real Windows.
- Symlink-at-root: realpath or as-is?
- Status nudge when the old-root row isn't enumerable after the
  ascend (permission-denied races).

### Next session

Two v0 items remain: incremental search (``/``) and the menu bar
(F9). Search is the more interesting design question — non-modal
inline input, focused-pane semantics. Either order works.

## 2026-05-22 (later) — Incremental search (`/`)

Picked this up after Matthew confirmed he wanted to do it next. The
more interesting of the remaining v0 items because it's the first
truly modeless input flow — every other typed-input action so far has
been a modal PromptDialog. `/` had to feel like Vim or ranger or
fzf's incremental search: type and the cursor moves in real time,
no Enter-to-commit delay.

### Three design decisions, captured to design.md

- **Substring, case-insensitive matching.** Prefix-only (XTree-strict)
  too restrictive; regex (Vim-style) too much for v0. Substring is the
  modern default and easiest to predict. Regex / prefix toggles via
  syntax prefix (`/^foo` for prefix, `/\foo` for regex) parked on the
  follow-ups list.
- **Visible-nodes-only scope in the tree pane.** Auto-expand-to-find
  would require eager subtree scans on every keystroke and conflicts
  with sources that refuse `LogAll`. Collapsed subtrees stay
  uninspected; the user expands first, searches second.
- **Replace the StatusLine inline.** Modal PromptDialog rejected as
  too heavy. The SearchBar lives in the StatusLine's screen row;
  `display: none` / `display: block` toggles which one is visible.
  No layout shift.

Two implied choices also captured (Esc restores cursor, Enter commits
and leaves cursor; empty query is a no-op; no-match indicator turns
the bar text red).

### Implementation shape

One new widget, two pane additions, six handler methods:

* **`wtree/widgets/search_bar.py`** — `SearchBar(Widget)`. Reactive
  `query`, `match_total`, `match_idx`; custom `on_key` for letters,
  Backspace, Esc, Enter, Up, Down, Ctrl+G; posts five messages
  (`QueryChanged`, `NextMatch`, `PrevMatch`, `Committed`,
  `Cancelled`). Started life as a `Static` subclass; rewrote to
  `Widget` after Textual 8.x's Visual pipeline blew up on
  `Static.update()` called from `__init__` (the
  `'NoneType' object has no attribute 'render_strips'` error - see
  bugs section).

* **`ContentsPane.iter_searchable() / set_search_cursor() /
  get_search_cursor()`** — `iter_searchable` yields `(row,
  basename)` for non-error rows; basename via `os.path.basename` so
  the user's "rep" matches both `report.txt` and `reports/` (the
  trailing slash on dir display is purely cosmetic). Cursor get/set
  delegates to `cursor_row` / `move_cursor`.

* **`TreePane.iter_searchable() / set_search_cursor() /
  get_search_cursor()`** — depth-first walk of visible nodes via
  `_walk_visible`. A single-element list serves as a mutable counter
  threaded through the recursive generators so the yielded
  `line_index` aligns with Textual's `cursor_line` numbering without
  any post-walk translation. Collapsed subtrees are skipped (their
  children aren't visible, so the cursor can't land on them).

* **`WTreeApp.action_search`** — bound to `slash`. Captures the
  focused pane, records its current cursor as the restore-on-Esc
  anchor, hides the StatusLine, activates the bar.

* **`on_search_bar_query_changed`** — recomputes matches on every
  keystroke. Substring case-insensitive against `iter_searchable()`.
  Picks the first match at-or-after the pre-search cursor (so typing
  `/rep` from row 5 prefers row 7 over row 2 — feels like a
  forward-scan); wraps to the first match if none qualify. Empty
  query leaves the cursor put.

* **`on_search_bar_next_match` / `on_search_bar_prev_match`** —
  step through `_search_matches` with modulo-wrap.

* **`on_search_bar_committed` / `on_search_bar_cancelled`** — both
  route to `_exit_search(restore=...)`, which tears down state,
  re-shows StatusLine, returns focus to the pane.

The pane-side `SearchTarget` protocol is duck-typed (not a
`typing.Protocol`); only two implementers and they're sibling
modules. If a third pane joins later it can be formalised.

### Two bugs surfaced

**Bug 1: `'NoneType' object has no attribute 'render_strips'`.**
First implementation subclassed `Static` and called `self.update("/")`
from `__init__` to set initial content. Textual 8.x's Visual pipeline
expects a non-None `_renderable` by the time the widget first
renders; calling `update` too early apparently leaves it in a state
where the visual pipeline gets None. Symptom: every test that
activated the bar crashed with the AttributeError. Fix: subclass
`Widget` directly and implement `render()` returning a Rich `Text`,
which bypasses the Static/Visual indirection entirely.

**Bug 2: tests asserted private API.** My initial test file checked
`bar.renderable` (which isn't a public attribute on Widget) and
`bar.has_class("-no-match")` (which the rewritten widget doesn't use
- the no-match state lives in match_total + query). Fixed by
exposing `bar.no_match` as a property and switching tests to assert
public state (`bar.query`, `bar.match_total`, `bar.match_idx`,
`bar.no_match`).

### Tests landed

15 new tests in `tests/test_search.py`:

- Protocol unit tests: iter_searchable on contents (basenames,
  dense indices) and tree (visible-only, collapsed subtree
  excluded).
- SearchBar widget unit tests: activate takes focus; deactivate
  clears state.
- Action wiring: `/` activates bar in contents and tree.
- Typing: cursor jumps to first match; case-insensitive.
- No-match: cursor stays put, bar reports `no_match`.
- Down/Up wrap through multiple matches.
- Esc restores cursor; Enter commits at match.
- Backspace shrinks query.
- Tree pane search jumps to matching child.

Full suite: **268/268 green** (was 253, +15). Suite now takes ~45s
total, hitting the bash timeout when run in one shot; split runs by
file work fine. `pytest-xdist` is a follow-up.

### Mount-truncation incidents

Hit on app.py, contents_pane.py, tree_pane.py, test_search.py,
design.md, todo.md - basically every non-trivial Edit. Each one
recovered via the heredoc + atomic-mv protocol. Three new "Notes for
the next session" entries capture the patterns:

- Static + update() in `__init__` blows up the Visual pipeline — use
  Widget + render().
- Reactive attributes auto-refresh on assignment.
- Full pytest suite is at the 45s bash-timeout boundary.

### Follow-ups parked (Search-era)

Nine items on the new Search-era follow-ups list:

- Remembered query for Ctrl+G outside search mode.
- Regex / prefix toggles via syntax prefix.
- Auto-expand tree subtrees during search.
- `StatusLine.flash` API (shared with Rename/Edit/Make-new/Ascend).
- Highlight matched substrings inside row labels.
- Search across the tagged set.
- Find-across-tree `Ctrl+F` (already in keymap; reuses this matcher).
- Empty-query restore semantics.
- "Other keys cancel and pass through" exit.

### v0 is nearly complete

Only **one item** remains on the v0 list: the F9 menu bar. After this
session, every Selection-rule operation (Copy, Move, Delete, Rename),
every shell-out flow (View, Edit), the create flow (Make-new), the
ascend gesture (Left-on-root), and the search gesture (`/`) are all
landed. **268/268 tests green.** Next session can either ship F9 and
call v0 done, or do a round of polish on any of the follow-ups list
items before the menu bar.

## 2026-05-22 (last) — `StatusLine.flash` + pane auto-refresh

Two cross-cutting features that had been on the follow-ups list since
the Rename work (the flash API) and the Move work (pane auto-refresh).
Both touch most of the app handlers and shipped together because they
share the same "post-op user feedback" feel.

### Two-tier feedback design

Locked the split: `StatusLine.flash(msg, timeout=3.0)` for user-
immediate nudges, `App.notify()` retained for queue-completion toasts.
Rationale: a completion may fire async ten minutes later when the user
has looked away, and Textual's notification stack queues those visibly
on next return; the status line would silently overwrite. Immediate
nudges, by contrast, are seen right now and shouldn't pile up - the
status line is the right surface.

Three sub-decisions captured to the design log:

- **3 second default timeout.** Long enough to read a sentence,
  short enough to feel transient. Matches Vim-ish status messages.
- **Replace, don't queue.** A new flash() cancels the previous
  timer and immediately shows the new message. Easier reasoning;
  rapid-fire flash queuing parked as a follow-up.
- **Flash holds through `refresh_from()`.** Cursor moves and queue
  ticks call `refresh_from(app)` constantly; without the guard,
  the ascend flash would be overwritten the moment the user's next
  keypress fired NodeHighlighted. The `refresh_from` body now bails
  out early if `_flash_message is not None`.

### Implementation shape

* **`wtree/widgets/status_line.py`** — added `_flash_message` /
  `_flash_timer` state, `flash(message, timeout=3.0)` method,
  `_clear_flash` timer callback (reverts via `refresh_from(self.app)`).
  `refresh_from()` now respects the active flash.

* **`wtree/app.py`** — added `WTreeApp.flash(message, timeout=3.0)`
  convenience that looks up the StatusLine and calls its flash with
  early-mount defensive try/except.

* **`_on_plan_complete` auto-refresh hook** — fires
  `asyncio.create_task(self._refresh_panes_after_op())` after the
  existing notify + status update. The async helper is `try/except`-
  wrapped so a refresh failure doesn't propagate back to the queue
  worker. Fires unconditionally - even on partial-success - because
  some items may have touched disk.

* **Routed ~20 notify warnings through flash:** action_rename's
  rejection + cancellation + planner-error nudges; action_view's
  kind validation nudges; action_edit's editor-not-found / non-zero-
  exit / spawn-error nudges; action_make_new's cancellation +
  planner-error nudges; `_plan_modal_enqueue` /
  `_plan_confirm_enqueue` / `_finalise_plan` cancellation +
  empty-plan nudges; `on_tree_pane_ascend_requested`'s "Already
  at filesystem root" and "Logged: NEW (ascended)" nudges;
  action_search's "focus a pane first" nudge.

  **Kept as notify:** `_finalise_plan`'s "X (queued)" message
  (carries non-trivial info worth queuing through the toast
  stack), and `_on_plan_complete`'s "X (done)" / "X (done with
  errors)" completion message (async, may fire when user has
  moved on).

### Bug surfaced: `status.renderable` isn't public on Static

First pass of the tests asserted `str(status.renderable)` to
inspect the displayed text. Textual 8.x's Static doesn't expose
`renderable` publicly; the public surface is `status.render()`
which returns the current Rich renderable. Fixed by adding a
`_status_text(status)` helper that calls `str(status.render())`.

### Tests landed

12 new tests in `tests/test_flash_and_refresh.py`:

- **Flash unit:** shows the message; clears after timeout; replaces
  active flash; holds through `refresh_from()`; default timeout is 3s.
- **App convenience:** `app.flash()` routes to StatusLine.
- **Flash integration:** Rename-with-tags rejection flashes;
  Ascend at FS root flashes; Ascend success flashes "Logged: NEW
  (ascended from OLD)".
- **Auto-refresh e2e:** Make-new entry appears in pane without the
  user pressing anything; Delete row vanishes; refresh survives
  current_path being deleted under it.

Full suite: **280/280 green** (was 268, +12). Suite split into four
runs again because the 45s bash timeout still bites.

### Notify-to-flash conversion was test-safe

None of the existing 268 tests asserted on notify content; they
assert on `app.last_plan`, file existence, cursor position, etc.
The conversion was a clean swap.

### Mount-truncation incidents

Hit on app.py (~36 KB final size), status_line.py, design.md,
todo.md, test_flash_and_refresh.py during this session. Each one
recovered via the heredoc-stage + atomic-mv protocol. Three new
"Notes for the next session" entries capture lessons:

- Static's `renderable` is private; use `str(widget.render())`.
- `asyncio.create_task` is the right pattern for firing async work
  from a sync queue callback.
- `set_timer(timeout, callback)` is Textual's one-shot deferred-
  work primitive; keep the Timer reference for cancel-and-replace.

### Follow-ups parked (Flash + auto-refresh)

Five items on a new "Flash + auto-refresh follow-ups" section:

- Severity-styled flash (yellow/red coloring).
- **Tree-pane auto-refresh** — the bigger missing piece. The
  contents pane refreshes automatically; the tree doesn't.
  Solution requires a "touched paths" signal from
  planner/executor + `_loaded` memo invalidation per node.
- Preserve cursor position across auto-refresh (currently resets
  to row 0).
- Flash queue for rapid-fire messages.
- Flash from inside async ops on refresh failure.

### v0 status

Two flash-list items completed this session. One v0 item remains:
**F9 menu bar**. After that, v0 is done. Memory + worklog + design.md
+ todo.md all in sync. The current commit on the branch is the
initial scaffold; everything since is uncommitted (per Matthew's
plan to commit the whole batch).

### Next session

F9 menu bar, then v0 ships. After v0, the "passive folder-change
detection with idle debounce" idea (from the Ascend-era follow-ups)
is the most interesting parked thread — it's a small but visible UX
win that doesn't require touching the planner machinery.
