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

## 2026-05-22 (v0 complete) — F9 menu bar

Last v0 item, now landed. **v0 is functionally complete.**

### Design decisions

Matthew confirmed all three recommended options at the start:
**always-visible MC-style bar at top**, **only show implemented
items**, **first-letter accelerators highlighted**.

Two top-level menus:

- **File**: New, View, Edit, Copy, Move, Rename, Delete, ---, Quit
- **Commands**: Search, Untag all

Help menu deferred — no About modal yet. Parked as a follow-up.
F1 (Help) is the only F-key still unbound on the cheat sheet.

### Implementation shape

Two widgets + one screen + one action:

* **`wtree/widgets/menu_bar.py`** — `MenuBar(Widget)` is the
  always-visible passive top row. Renders the menu names with
  accelerator letters underlined. Doesn't own focus; never
  receives input. Also defines `MENUS` as a module-global tuple
  of `Menu` dataclasses (each with a tuple of `MenuItem`
  dataclasses), plus `render_menu_row(active_idx)` which both
  the passive bar and the active modal use so they paint
  visually-identically.

* **`wtree/widgets/menu_screen.py`** — `MenuScreen(ModalScreen[str
  | None])` is the interactive surface pushed on F9. Renders a
  top row that mirrors the passive `MenuBar` (with one menu
  highlighted as active) plus a `_DropdownPanel` Widget child
  showing the active menu's items + shortcuts. Owns `on_key`:
  Up/Down navigate dropdown (skipping separators), Left/Right
  rotate top-level menus with wrap, Enter dismisses with the
  selected item's `action` string, Esc dismisses with `None`,
  letter accelerators jump to + activate the matching item.

* **`wtree/app.py` `action_menu_bar`** — `@work`-decorated
  async; pushes `MenuScreen`, awaits dismiss, dispatches via
  `getattr(self, f"action_{name}")()`. Handles both sync and
  `@work` action methods (the latter return None synchronously
  after spawning a worker; the former may return a coroutine
  that we await).

* **MenuBar in `compose()`** — yielded right after `Header()`,
  so the layout from top to bottom is: Header (Textual built-in
  title), MenuBar (passive menu row), Horizontal (panes),
  StatusLine, SearchBar (hidden default), KeyBar.

* **`f9` binding + `_WIRED` bump** — F9 wired in `WTreeApp.BINDINGS`;
  KeyBar `_WIRED` bumped from `{2,3,4,5,6,7,8,10}` to
  `{2,3,4,5,6,7,8,9,10}`. F1 remains unwired.

### Tests landed

14 new tests:

- `tests/test_menu.py` (13 tests): MenuBar renders both menus;
  MENUS definition shape; row-renderer accepts active_idx; F9
  opens MenuScreen; Esc closes; Right/Left rotate (with wrap);
  Down moves cursor; Down skips separator (File menu's
  separator at index 7 -> next is Quit at 8); letter
  accelerator activates Copy directly; Enter activates current
  item; Commands -> Search dispatches search; Commands -> Untag
  all clears the tagged set.

- `tests/test_status_keybar.py` (1 new test): F9 wired-bit
  assertion; the four stale "F9 still unbound" snapshots in
  earlier per-op tests also updated to drop the now-incorrect
  `9 not in _WIRED` lines.

Full suite: **294/294 green** (was 280, +14).

### Lessons captured

Three new "Notes for the next session" entries:

- Module-global definitions are the right shape for shared
  state between a passive widget and a modal screen.
- `@work`-decorated actions return None synchronously; only
  naked `async def` actions return coroutines worth awaiting.
- Reactive children + `_sync_children()` is the pattern for
  modal screens with internal state.

### Mount-truncation incidents

Hit on app.py (~28 KB final), keybar.py, test_status_keybar.py,
design.md, todo.md. The heredoc-stage + atomic-mv protocol
recovered each. No new "Notes for the next session" entries —
these are all already documented patterns.

### v0 status: COMPLETE

Every operation in design.md's canonical keymap that has an
implementation is now wired and tested. The only unwired thing
on the F-key bar is F1 (Help). v0 ships now.

The repo has a single initial commit; everything since has been
on the working tree. Matthew's plan: commit + push in chunks.
Three commits so far covered the Make-new + ascend, search, and
flash + auto-refresh slices. F9 menu bar is the fourth commit —
the "v0 complete" milestone commit.

### Next session

Per the follow-ups list, the most interesting parked threads
are:

- Help menu + About modal + F1 binding (small, completes the
  chrome).
- Passive folder-change detection with idle debounce (Matthew's
  idea from the Ascend session).
- Tree-pane auto-refresh (the unfinished half of the post-op
  refresh).
- `StatusLine.flash` severity styling (yellow / red).

But v0 is shippable as-is.


---

## 2026-05-22 evening — tagging polish pass

**Outcome: 318/318 green (294 baseline + 24 new). Tagged-set-era follow-ups in `todo.md` checked off.**

After v0 wrap + the morning's mouse-design conversation, picked up the tagged-set polish work that had been parked. The goal was to round out the tagged set as WTree's headline differentiator — bulk gestures so the user doesn't fall back to Space-times-thirty for a multi-file selection.

### Design choices (Matthew confirmed via AskUserQuestion)

1. **Visual style for tagged rows: bold yellow on every cell, marker stays `*` (rendered bold yellow too).** Reverse-video and marker-only-glyph both rejected — reverse-video clashes with the cursor highlight; marker-only wouldn't give the row-level signal that makes a multi-tag selection visually obvious.
2. **Glob casing: platform-default via `fnmatch.fnmatch`** (case-sensitive on POSIX, case-insensitive on Windows). Considered case-insensitive-everywhere for symmetry with `/`-search but the standard fnmatch behaviour wins on user expectation.
3. **Tree-pane Space recursion: toggle by the directory's own tagged state.** Tagged dir -> recursive untag; untagged dir -> recursive tag. Always-additive and any-descendant-tagged-means-untag both considered and rejected (the cursor-tagged-state-IS-the-signal model is the easiest to reason about and naturally inverse-able).

### What landed

- **`TaggedSet.add_many` / `remove_many`** (`wtree/tagged_set.py`) returning the actual delta (not the iterable length). Foundation for every bulk gesture below. 6 new unit tests including empty-input, duplicate-in-input, and round-trip.
- **Visual style** (`wtree/widgets/contents_pane.py`): added `_TAGGED_STYLE = "bold yellow"`, helper `_cell(value, tagged)` that returns `Text(value, style=_TAGGED_STYLE)` for tagged rows and plain str otherwise. New `_row_cells: list[list[str]]` parallel list stores the raw strings so `refresh_tag_markers` can restyle a whole row without re-scanning the source. `action_toggle_tag` now restyles all cells of the row, not just the marker column. New `row_paths()` helper on the pane returns absolute paths for taggable rows (skipping error rows) — used by Ctrl+A and `+`/`-`.
- **`Ctrl+A` tag-all-in-current-dir** (`wtree/app.py`): sync `action_tag_all`. Iterates `ContentsPane.row_paths()`, calls `add_many`, refresh, flash with delta. Idempotent — running again when all are tagged shows "N entries already tagged".
- **`+` / `-` tag-by-pattern** (`wtree/app.py`): `@work` actions push a `PromptDialog`. Shared `_tag_pattern_impl(add=bool)` helper runs the actual match. `fnmatch.fnmatch` against `posixpath.basename(p)`. Empty pattern or Esc both cancel cleanly with a status flash. No-match also flashes a clear message.
- **Recursive tree-pane Space** (`wtree/widgets/tree_pane.py` + `wtree/app.py`): new `TreePane.TagRequested(path)` message, `TreePane.on_key` extended to intercept Space (event.stop + prevent_default) on any node with `data is not None`. App handler `on_tree_pane_tag_requested` is `@work`; the walker `_walk_subtree(root)` is a stack-based async generator (iterative to avoid Python recursion limits on deep trees) that yields the root + every descendant via `EntrySource.scan()`. Symlinks treated as leaves; ScanErrors silently skipped.

### Tests

- `tests/test_tagged_set.py` — 6 new unit tests for the bulk API (15 total in file).
- `tests/test_app.py` — 3 existing assertions updated (`marker == "*"` -> `str(marker) == "*"`) to handle styled cells.
- `tests/test_tag_bulk_e2e.py` (new) — 11 e2e pilot tests for Ctrl+A and `+`/`-` covering empty dirs, error rows, idempotency, cancel-via-Esc, no-match, and basic glob.
- `tests/test_tree_recursive_tag.py` (new) — 7 e2e pilot tests covering root-of-tree, sub-dir, empty-dir, ScanError-skip, error-placeholder, and the bulk-then-single-toggle interaction.

### Mount fights (worth documenting)

The mount truncated three separate writes today: `tagged_set.py`, `contents_pane.py`, `app.py`, plus `tests/test_app.py`. Each one looked successful at the Edit-tool layer but a `wc -l` plus `ast.parse` afterward showed the file was cut mid-statement. The mitigation that now works reliably: assemble the entire intended file content in `/tmp`, run `ast.parse` on the `/tmp` copy, then `cp` to `<target>.tmp`, byte-compare `wc -c`, and only `mv` on a match. Treat this as the default protocol for any file write into the project folder larger than a one-liner — Edit is convenient but the mount layer can lie, especially when the change spans more than a small handful of lines.

Stale `.pyc` files were a second source of confusion mid-session: a previous session's bytecode in `__pycache__` had the OLD session-path baked into `co_filename`, and the FUSE layer refused `rm` on those files ("Operation not permitted"). The workaround that worked: `PYTHONPYCACHEPREFIX=/tmp/pyc_wtree` forces Python to write its bytecode cache outside the project, sidestepping the locked-in `.pyc`s. Worth a follow-up — see notes for the next session.

### State at end of session

- v0.x scope: the tagged-set feature is now genuinely demonstrable. Tag a dir from the tree, see the whole subtree light up in the contents pane in bold yellow.
- Open follow-ups in `todo.md` under "Tagged-set-era follow-ups": recursive `+`/`-`, tree-pane node styling (separate from contents-pane styling because Tree uses Rich-renderable labels, not table cells), progress feedback for big recursive walks, cancel during walk.

### Recommended next pickup

- **F1/Help + About modal.** Closes out the F-key bar (only unbound F-key). Small scope, gives a "fully bound" milestone.
- **`Ctrl+F` find-across-tree.** Reuses the search infrastructure built earlier; the walker we just landed (`_walk_subtree`) is structurally adjacent.
- **Tree-pane node styling** to mirror the contents-pane tagged-row styling so the visual story is consistent across panes.

But none of these are blocking — v0 plus the tagging polish is a coherent shippable point.

## 2026-05-23 — F1 / Help + About modal (v0 keymap loop closed)

Picked the F1 Help follow-up off `todo.md` to close out the F-row. v0
was already functionally complete, but F1 was the only unbound F-key
on the cheat-sheet bar and the menu-era todo asked for a Help / About
modal as a discoverability surface. One small session, one modal, one
new menu, no new infrastructure.

### Design call

Single combined modal `HelpScreen` does double duty: F1 cheat-sheet
and the menu-bar Help → About item open the same screen. Two reasons
to combine rather than split into "Help" and "About" surfaces:

* The About info (name, version, attribution, one-line description)
  fits in five lines. A dedicated About modal would feel underfed.
* The Help content (categorised keymap reference) wants the About
  header anyway — "what is this thing and what version" sits
  naturally above "how do I drive it".

A future "Keymap only" sub-item can layer on top by adding a second
`MenuItem` that opens the same screen scrolled to the keymap section,
without changing the screen itself. v0 keeps it one item.

Modal shape mirrors `ViewerScreen` exactly — `VerticalScroll`
container, `Static` body with Rich `Text`, `Label` header docked top,
`Label` hint docked bottom, dismiss on `Esc` / `Q` via `BINDINGS`. The
view-style modal contract is the established pattern for read-only
screens; reusing it kept the implementation under ~150 lines.

Keymap content is hand-curated in `_help_content()` rather than
introspected from `WTreeApp.BINDINGS`. `BINDINGS` is a flat list of
`(key, action, label)` tuples; the help screen wants conceptual
grouping (Navigation / Tagging / File operations / Search /
Application / Selection rule). Maintaining two surfaces means a new
binding touches both `BINDINGS` and `_help_content()`, but the cost
is small at v0 scale and the readability gain for the user is large.
If the keymap grows past ~50 entries we'd revisit.

### Implementation

* `wtree/widgets/help.py` — new module. `HelpScreen(ModalScreen[None])`
  with `Esc` / `Q` bindings, `VerticalScroll` body. Pure `_help_content()`
  function returns a Rich `Text` so tests can assert on the rendered
  content without instantiating the screen.
* `wtree/widgets/menu_bar.py` — added a third `Menu` entry: `Help`
  with accelerator `h` and a single `About` item (accelerator `a`,
  shortcut display `F1`, action `help`).
* `wtree/widgets/keybar.py` — `_WIRED` is now `{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}`.
  The docstring's "currently wired" comment updated. F1 (Help) now
  renders bold rather than dim — the cheat sheet finally tells the
  truth about every F-key.
* `wtree/app.py` — added `("f1", "help", "Help")` to `BINDINGS`,
  changed the existing `("question_mark", "noop", "Help")` to
  `("question_mark", "help", "Help")`, added `action_help` which
  pushes `HelpScreen()`. Removed the now-dead `action_noop`
  placeholder. New import `from wtree.widgets.help import HelpScreen`.

The dispatch via the F9 menu reuses the existing pattern — the
`About` item's `action` is `"help"`, and `action_menu_bar` dispatches
via `getattr(self, f"action_{name}")()`. No new dispatcher logic.

### Tests

`tests/test_help.py` — 13 new tests covering:

* Pure content assertions (no app instance needed): the version
  string is present, every section header exists, key bindings like
  `Tab`, `Space`, `Ctrl+A`, `F5` are listed.
* F1 opens `HelpScreen`. Same for `?`.
* `Esc` and `Q` both dismiss the modal off the screen stack.
* `_WIRED == frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10})` (structural)
  and KeyBar renders the `Help` label (rendered surface).
* `MENUS` has `Help` as the third top-level; the dropdown has a
  single `About` item with action `help`.
* End-to-end menu walk: F9 → Right → Right → Enter lands on
  `HelpScreen`. Same with the `a` letter accelerator.

Updated `tests/test_menu.py`:
* `test_menus_definition_has_expected_items` — added the
  `["File", "Commands", "Help"]` assertion plus the About item
  check.
* `test_left_rotates_wraps` — the comment said "wraps to Commands"
  but now wraps to Help (the new last menu). Asserts
  `len(MENUS) - 1` rather than the literal `1` so a future fourth
  menu won't break this test.
* `test_menu_bar_renders_both_menus` — added the `Help` substring
  check to the rendered output.

Updated `tests/test_status_keybar.py`:
* All six `test_keybar_wired_set_includes_f*` tests had a trailing
  `assert 1 not in _WIRED` that documented "F1 (Help) remains
  unbound". Flipped to `assert 1 in _WIRED` with a date-stamped
  comment. The history is preserved in the surrounding context.

### Mount fights (again)

Mount truncation hit twice this session:

1. **test_menu.py** — after the `test_left_rotates_wraps` Edit, the
   final test (`test_untag_all_from_commands_menu`) got cut mid-line
   at `awa` (truncated `await pilot.press("right")`). File-tool Read
   showed all 267 lines intact; bash + Python both saw a 257-line
   file ending mid-statement. Recovery per the protocol: full file
   rebuild in `/tmp/wtree-stage/` via heredoc, `ast.parse` check,
   `cp` to `.tmp`, `mv -f` atomic. Resulting file was 9875 bytes,
   parsed clean both sides.

2. **design.md** — the F1 Decision-log entry was a single ~1KB row.
   Edit reported success; file-tool Read showed it intact; bash
   `tail -c 200` showed it truncated mid-word at "Con" (cut off
   "Content is About info..."). Lost ~252 bytes of the row plus the
   "## Open questions" closing section. Recovery via Python:
   truncate the mount file to the last verified-intact byte (the
   end of the previous decision row), then append the full new
   row + closing section via UTF-8 write, atomic `mv -f`. Final
   size 25460 bytes, last line is the v0-complete summary.

Lessons consistent with what's in [[feedback-wtree-mount-rules]]:
mount and file-tool views disagreeing remains a regular failure
mode for any non-trivial Edit, even on plain markdown. The two
existing protocols (`ast.parse` for `.py`, bash `tail` + `wc -c`
for prose) both caught these. Continue using them.

### Tests at end of session

**331/331 green.** Was 318/318 at v0 + tagging polish complete; 13 new
tests landed for the F1 Help work. Split run across three pytest
invocations (the full-suite run exceeds the bash 45s timeout — same
as the previous session):

* `test_app.py … test_packaging.py` chunk → 102 passed
* `test_ops_* … test_search.py test_status_keybar.py` chunk → 180 passed
* `test_tag_bulk_e2e.py … test_viewer.py` chunk → 49 passed

### State at end of session

- F-key cheat-sheet is fully truthful now — every F-key on the bar
  is wired to a real action. v0 keymap is complete.
- Help menu's About is the third top-level menu; F9 + `h` + `a`
  reaches it the long way around, F1 reaches it directly, `?` also
  reaches it.

### Recommended next pickup

The natural follow-ups from `todo.md`:

- **Tree-pane tagged-node visual style.** Contents-pane rows go
  bold-yellow when tagged; tree-pane nodes still render plain.
  Custom `render_label` override on `TreePane` would close the
  visual-consistency gap.
- **Tree-pane auto-refresh after ops.** Cleanest design needs the
  planners to emit a "touched paths" set on `OperationResult` so
  the refresh hook knows which `_loaded` memo entries to invalidate.
- **`Ctrl+F` find-across-tree.** Reuses the substring matcher and
  message bus from `/` search; the existing `_walk_subtree` is
  structurally adjacent and could share machinery.
- **Smart cursor placement in the rename modal.** Pre-select
  basename-without-extension so typing replaces the stem and the
  extension survives. Small UX win.


## 2026-05-23 (later) — Tree-pane tagged-node visual style (matches contents pane)

Picked the next item from `todo.md` after F1 Help landed: tree-pane
tagged nodes were still rendering plain while the contents pane went
bold-yellow on tagged rows (2026-05-22 polish pass). Closing that
gap is the "visual story is consistent across panes" follow-up
listed under Tagged-set-era follow-ups.

### Design call

Two implementation paths considered:

1. **Override `Tree.render_label`** — Textual's documented hook for
   per-node styling. Consults the tagged set on every paint and
   stylizes the rendered Rich `Text` with bold-yellow if the node's
   backing path is in the set.
2. **Rebuild each node's stored label on every tag mutation.**
   Mutates `node._label` per-node when a tag toggles, then triggers
   a refresh.

Went with (1). The decisive property: lazy-expanded subtrees. When
the user opens a folder for the first time, `_populate` creates new
`TreeNode`s on the fly; with `render_label`, those nodes consult the
live tagged set on their first paint and inherit the correct style
automatically. With (2), every mutation site would need to know about
every possible future subtree, which is structurally impossible — the
nodes don't exist yet at mutation time.

The cost of (1) is one set-membership check per visible node per
render. Negligible.

A symmetric API to the contents pane's `refresh_tag_markers` is still
useful for the mutation callsites that want to *force* a repaint
after changing the tagged set. The pane gets `refresh_tag_styles()`
which is just `self.refresh()` wrapped behind a descriptive name —
no logic, but a single named entry point that future tagged-set work
can hit instead of poking at Textual's refresh machinery directly.

### Implementation

* `wtree/widgets/tree_pane.py`:
  - New `TaggedSet` import + module-local `_TAGGED_STYLE = "bold yellow"`
    constant (matches the contents-pane convention; tested for drift).
  - `__init__` takes a new `tagged_set: TaggedSet` arg, stores it on
    `self._tagged`.
  - New `render_label(node, base_style, style) -> Text` override: calls
    `super().render_label`, then `text.copy().stylize(_TAGGED_STYLE)`
    when `node.data is not None` and `_tagged.contains(sid, node.data)`.
  - New `refresh_tag_styles()` method = `self.refresh()`. Public
    surface for app-level mutation callsites.
* `wtree/app.py`:
  - `compose()` now passes `self.tagged_set` to `TreePane`.
  - New `_refresh_tag_visuals()` helper that calls both
    `ContentsPane.refresh_tag_markers()` and
    `TreePane.refresh_tag_styles()`. Single source of truth for
    "tags changed; repaint".
  - Routed every bulk-mutation site through `_refresh_tag_visuals()`:
    `action_untag_all` (Ctrl+U), `action_tag_all` (Ctrl+A),
    `_tag_pattern_impl` (+ / -), `on_tree_pane_tag_requested`
    (recursive Space), `_finalise_plan` (after-op tagged-set clear).
  - `on_contents_pane_tags_changed` now calls
    `TreePane.refresh_tag_styles()` so single-row toggles flowing
    through `ContentsPane.action_toggle_tag` propagate to the tree
    pane via the existing `TagsChanged` message bus.

`ContentsPane._TAGGED_STYLE` left untouched as a module-local constant
in its own module — the styles match by convention and a test asserts
that they stay equal. Cross-module import would couple the two
widgets, which we'd rather not do for a one-string contract.

### Tests

`tests/test_tree_tag_style.py` — 11 new tests:

* Pure render: untagged renders plain, tagged renders bold-yellow,
  root node is taggable like any other (no special-casing).
* Integration: contents-pane Space toggle restyles the tree;
  `Ctrl+U` clears tree styling; `Ctrl+A` styles every visible tree
  row; recursive tree-pane Space styles the whole subtree.
* The motivating case: tag a path BEFORE expanding its parent
  subtree, then expand and verify the inner node renders bold-yellow
  on its first paint. Proves `render_label` was the right call.
* `refresh_tag_styles()` and `_refresh_tag_visuals()` smoke
  callability checks.
* `_TAGGED_STYLE` constant drift test against the contents-pane's
  constant.

Helper `_is_bold_yellow(text)` walks Rich `Text.spans` and looks for
a span whose `style` includes both `bold` and `yellow` — survives
the icon-style overlay that `Tree.render_label` adds for expand
arrows.

### Mount fights (more)

Hit two truncations this session:

1. **`app.py` mid-method.** After the Edits that swapped per-callsite
   `contents.refresh_tag_markers()` for `_refresh_tag_visuals()`, bash
   saw the file cut at byte 35326 — mid-`_on_plan_complete`, missing
   `_update_subtitle`, `_refresh_status`, `_refresh_panes_after_op`,
   and the `main()` entry point. File-tool Read showed all 1012 lines
   intact. Recovery: read the authoritative tail from file-tool,
   rebuild the file in `/tmp/wtree-stage/`, atomic `cp + sync + mv`.
   Final size 36628 bytes, `ast.parse` OK on both sides.

2. **`tests/test_tree_tag_style.py` mid-docstring.** After the
   `Tag(...)` -> `tagged_set.add(sid, path)` Edits, bash reported
   the file contained null bytes (cut mid-line with a NUL padding
   tail). Recovery: heredoc rebuild + atomic mv. Final size 9880
   bytes.

Pattern is consistent: the more separate Edit calls a file accumulates
across a session, the more likely the mount lies. Future sessions
that need multiple Edits on the same `.py` file inside the project
should default to "heredoc rebuild" rather than relying on the Edit
tool's success message.

### Tests at end of session

**342/342 green.** Was 331 at v0 + F1 Help complete; +11 from the
tree-pane styling work. Split run across three pytest invocations:

* `test_app.py … test_packaging.py` chunk → 102 passed
* `test_ops_* … test_status_keybar.py` chunk → 180 passed
* `test_tag_bulk_e2e.py … test_viewer.py` chunk → 60 passed

### State at end of session

- Tagged paths now render bold-yellow in both panes. Tag a folder
  from the tree, see the whole subtree light up — and the parent
  row in the tree itself.
- Lazy-expanded subtrees automatically pick up the correct style
  on first paint thanks to the `render_label` override.

### Recommended next pickup

From the remaining `todo.md` items:

- **Tree-pane auto-refresh after ops.** Contents pane already
  refreshes; tree doesn't. Cleanest design needs planners to emit a
  "touched paths" set on `OperationResult`.
- **`Ctrl+F` find-across-tree.** Reuses the matcher + message bus
  from `/` search; the existing `_walk_subtree` is structurally
  adjacent and could share machinery.
- **Tree-pane left/right arrow keys for expand/collapse.** Tripped
  on this writing the lazy-expand test — Textual 8.x's `Tree`
  doesn't ship `left`/`right` bindings, and ours only intercepts
  Left on the root. A direct user-keyboard expand/collapse path
  would round out the tree-pane keymap.
- **Smart cursor placement in the rename modal.** Pre-select
  basename-without-extension. Small UX win.


## 2026-05-23 (yet later) — Tree-pane auto-refresh after ops

Last item from the post-v0 Move-era follow-up was "tree-pane refresh
after ops." The contents pane already re-shows its `current_path`
after every plan completes; the tree pane didn't, so after a Move /
Delete / Make-new / Rename / Copy the user had to collapse + re-expand
the affected node to see the new state. Closing that gap.

### Design call

Two-stage shape: ops layer emits which directory listings changed
(`OperationResult.touched_paths`), UI layer consumes it to invalidate
the tree's lazy-load memo for the affected nodes.

**Where to compute touched paths.** Three options:

1. Inside each `_native_op` executor function — most flexible but
   spreads the logic across five branches with subtle "what does
   `dst_path` mean for delete?" footguns.
2. As a property on `OperationResult`, computed from the per-item
   results — every executor already records the `PlanItem` it
   processed and the status, which is exactly the data needed.
3. As a planner-side annotation on `Plan` — but planners don't know
   what actually succeeded, so this only works if every item succeeds.

Went with (2). The rule is uniform per op kind: COPY/MAKE_NEW touch
`dirname(dst)`, DELETE touches `dirname(src)`, MOVE touches both,
RENAME's planner guarantees `dirname(src) == dirname(dst)` so one
parent covers it. Restricted to `SUCCESS` items so partial-failure
cases don't claim disk state they didn't reach.

**Where the tree-pane refresh logic lives.** A new method
`TreePane.refresh_paths(paths)` that walks every tree node, finds
the ones whose `data` is in the target set, drops them from
`_loaded`, wipes their children, and re-populates the ones that were
expanded. Unloaded subtrees are left alone — the lazy-load on first
expand will pick up the up-to-date listing without any extra wiring.

Cursor preservation is best-effort: after `node.remove_children()`,
Textual decides where the cursor lands. A future polish pass could
snapshot the previous cursor's backing path and try to restore it
post-rebuild; parked.

The tagged-row styling ([[project-wtree]] 2026-05-23 work) self-heals
through this naturally — the rebuilt child nodes get rendered against
the live tagged set via the `render_label` override, so a tagged dir
that just moved keeps its bold-yellow marker without any extra
wiring.

### Implementation

* `wtree/ops/base.py`:
  - New `OperationResult.touched_paths` property. Walks `self.items`,
    skips non-SUCCESS, emits dst parents for COPY/MAKE_NEW, src
    parents for DELETE, both for MOVE, single src parent for RENAME.
    Returns a `set` for de-dup when many items share the same parent
    (typical for batch ops).
  - Added `import os` for `os.path.dirname`.
* `wtree/widgets/tree_pane.py`:
  - New `refresh_paths(paths: Iterable[str])` async method. Walks
    every tree node, matches against the targets, drops matches
    from `_loaded`, wipes their children, re-populates the ones
    that were expanded. Calls `self.refresh()` at the end to
    trigger a paint.
  - New `_walk_all_nodes(node)` helper — depth-first walk of every
    tree node, including the root. Used by `refresh_paths`.
  - Added `Iterable` import.
* `wtree/app.py`:
  - `_refresh_panes_after_op` extended: after the existing
    contents-pane refresh, query the tree pane and call
    `tree.refresh_paths(self.last_result.touched_paths)`. Each
    pane's refresh is its own try/except so a failure on one
    doesn't block the other.

### Tests

`tests/test_tree_refresh.py` — 13 new tests:

* **`touched_paths` pure-data coverage:** one test per op kind (COPY,
  MAKE_NEW, DELETE, MOVE, RENAME) verifying the right parent paths
  come out for a representative plan + all-success results. Plus a
  partial-failure case showing that failed items don't contribute
  paths, and an empty-result case yielding an empty set.
* **`refresh_paths` pane-level behaviour:** empty-set no-op; loaded +
  expanded node gets re-scanned (race: add a dir after initial scan,
  call refresh, verify both children appear); unloaded node is
  silently skipped.
* **End-to-end:** Make-new of a dir at the root (verifies the new
  subdir appears in the tree without collapsing + re-expanding);
  Delete of a subdir (verifies the row disappears); Move of a child
  from `src` to `dst` (verifies `src` loses its child in the tree
  while on-disk reality is correct).

The Move e2e test bypasses the contents-pane cursor-navigation dance
by tagging the source path programmatically and calling
`app.action_move()` directly — that exercises the auto-refresh path
without depending on letter-by-letter modal typing. Same trick
should be reusable for any future "drive an op without UI typing"
test.

### Mount fights (more, persistent)

Three truncations this session:

1. **`base.py` mid-docstring** after the Edit that added
   `touched_paths`. Bash saw 236 lines ending mid-`@property`
   docstring; file-tool saw 295 lines intact. Rebuilt the file
   whole via Python from a clean string, atomic `cp + sync + mv`.
   Final 9805 bytes, parse OK on both sides.
2. **`tree_pane.py` mid-docstring** after the Edit that added
   `refresh_paths`. Bash saw 428 lines truncated inside
   `focus_dir_under_cursor`'s docstring; file-tool saw 513 lines.
   Recovered by truncating the mount file to the last intact
   marker and appending the authoritative tail from file-tool.
3. **`app.py` mid-method** after the Edit extending
   `_refresh_panes_after_op`. Bash saw 999 lines (truncated at
   `await contents.show_path(contents.current_path)`); file-tool
   saw 1036 lines. Same recovery shape: marker truncate + tail
   append from file-tool.

The pattern is now well-documented: any Edit that grows a Python
file by more than a few lines in this project will eventually
truncate on the mount. The Python-based "marker truncate + tail
append from file-tool view" recipe used today is the fastest
recovery — beats heredoc for big files because heredoc encodes the
whole file as a string literal in the recovery script, which itself
gets long.

### Tests at end of session

**355/355 green.** Was 342 after tree-pane styling; +13 from this
session's tree-pane auto-refresh work. Split run:

* `test_app.py … test_packaging.py` → 102 passed
* `test_ops_* … test_status_keybar.py` → 180 passed
* `test_tag_bulk_e2e.py … test_viewer.py` (with new `test_tree_refresh.py`) → 73 passed

### State at end of session

- After any Move / Delete / Make-new / Rename / Copy the tree pane
  reflects the new on-disk state automatically. No more collapse +
  re-expand to see a new subdir.
- The targeted refresh only re-scans the affected nodes; the rest
  of the tree keeps its expansion state intact.

### Recommended next pickup

Remaining items from `todo.md`:

- **Tree-pane left/right arrow expand/collapse bindings.** Textual
  8.x's `Tree` doesn't ship `left`/`right` bindings; pressing right
  on a tree node is currently a no-op. Tests had to call
  `node.expand()` + `await _populate(node)` directly. Closing this
  gap would round out the tree-pane keymap.
- **`Ctrl+F` find-across-tree.** Substring matcher + message bus
  already exists for `/`; the new `_walk_all_nodes` walker is
  structurally adjacent.
- **Smart cursor placement in the rename modal.** Pre-select
  basename-without-extension. Small UX win.
- **Cursor preservation across tree-pane refresh.** Snapshot the
  previous cursor's backing path before `refresh_paths`, try to
  re-land on the same path post-rebuild. v0 accepts "cursor goes
  wherever Textual puts it" for now.


## 2026-05-23 (still later, last today) — Tree-pane Left / Right arrow bindings

Matthew called this out as the next pickup with the additional context
that WTree is going to be his daily-driver TUI and bake into Linux
boot images. That sharpens the priority — the tree pane's arrow keys
not working is friction that compounds across thousands of
keystrokes a day.

### The gap

Discovered earlier today while writing the lazy-expand test for
tree-pane tagged styling: Textual 8.x's `Tree` widget ships **no**
`left` / `right` bindings. Pressing right on a tree node was a
no-op. Up to now, expanding a node required pressing `enter` /
`space` (which we'd taken over for tagging), or drilling in via the
contents-pane's `enter_dir`. Both work but neither feels like a tree
view.

### Design call

Mapped the two keys to the pattern every other tree view in the wild
uses (Finder column view, Windows Explorer tree, GTK FileChooser):

* **Right on a collapsed expandable node** = expand + lazy-populate
  inline (`await _populate(node)`).
* **Right on an already-expanded node** = drill-in to the first
  child. XTree-style. No-op on an empty expanded dir.
* **Right on a non-expandable node** (error placeholder,
  `allow_expand=False`) = no-op.
* **Left on the root** = preserved — still posts `AscendRequested`
  for the existing XTree "widen the logged window" gesture.
* **Left on a non-root expanded node** = collapse in place. Cursor
  stays put.
* **Left on a non-root collapsed node** = jump cursor to parent
  (Textual's `shift+left` action, rebound to plain Left).

Net effect: Left twice walks the user out of a subtree (collapse,
then up one); Right twice drills two levels deep. Symmetric, fast,
and matches muscle memory from every other tree widget.

### Implementation

* `wtree/widgets/tree_pane.py` — extended `on_key`. The previous
  version only handled left-on-root and space; the new version owns
  all three keys with explicit per-state branches. Every branch
  `event.stop()` + `event.prevent_default()` so a future Textual
  version that grows a default doesn't double-fire. The docstring
  was rewritten to enumerate the full state matrix.

  The `right` branch awaits `_populate` inline — same pattern the
  existing `focus_dir_under_cursor` uses. The follow-up `cursor_line`
  assignment for the "descend on second right" branch goes through
  `await asyncio.sleep(0)` first so Textual's line indexer rebuilds
  before reading `child.line` (same idiom from `focus_child_of_root`).

* Public attribute: `TreeNode.allow_expand` (verified with a quick
  `dir()` probe, not the underscore-prefixed private form). Error
  placeholder leaves are added via `add_leaf` which sets
  `allow_expand=False`, so the "no-op on right" branch is a single
  attribute check.

### Tests

`tests/test_tree_arrows.py` — 8 new tests:

* `test_right_expands_collapsed_dir` — primary path.
* `test_right_on_expanded_dir_descends_to_first_child` — drill-in.
* `test_right_on_empty_expanded_dir_is_noop` — empty-children edge case.
* `test_right_on_error_leaf_is_noop` — error placeholder safety.
* `test_left_on_expanded_node_collapses` — primary path.
* `test_left_on_collapsed_node_jumps_to_parent` — walk-out gesture.
* `test_left_on_root_still_ascends` — regression for the existing
  left-on-root gesture.
* `test_space_still_posts_tag_request` — regression for the tagging
  gesture.

A quick interactive smoke before writing tests verified the round
trip: expand outer, descend to inner, left back to outer, left to
collapse outer, left to land on the root.

### Mount fights

Zero today on this session. The Edit on `tree_pane.py` swapped a
docstring + 30-line `on_key` body without truncation. Possible the
relatively small delta (Edit-grew the file by ~50 lines on top of an
already-stable file) is what kept it under the mount's apparent
limit; possible we got lucky. Continue to verify with `ast.parse`
after every Edit on this project anyway.

### Tests at end of session

**363/363 green.** Was 355 after tree-pane auto-refresh. +8 from
this session. Split run:

* `test_app.py … test_packaging.py` → 102 passed
* `test_ops_* … test_status_keybar.py` → 180 passed
* `test_tag_bulk_e2e.py … test_viewer.py` (incl. `test_tree_arrows.py`) → 81 passed

### State at end of session

The tree pane is now a full XTree/Finder-class tree view. Today's
four sessions together brought:

* F1 Help / About — every F-key wired.
* Tree-pane tagged-node bold-yellow — visual story consistent across
  both panes.
* Tree-pane auto-refresh after ops — both panes stay accurate
  without user intervention.
* Tree-pane Left / Right bindings — drill-in and walk-out without
  reaching for the Tab key.

### Recommended next pickup

- **`Ctrl+F` find-across-tree.** Search substring across the whole
  tree, not just visible nodes; reuses the `/` matcher + the
  `_walk_all_nodes` walker that landed earlier today.
- **Smart cursor placement in the rename modal.** Pre-select
  basename-without-extension. Small UX win.
- **Tree-pane cursor preservation across refresh_paths.** Snapshot
  the previous cursor's backing path, try to restore post-rebuild.
- **`L` log new source.** The XTree command for adding a new logged
  drive / path - opens a prompt; tagged set spans sources already.
  Closes another canonical keymap entry.


## 2026-05-23 (latest, fifth session today) — Ctrl+F find-across-tree + Ctrl+G next-match

Matthew chose Ctrl+F as the next pickup. Matches the design.md
canonical keymap entry that's been parked since day one — and it
slots cleanly on top of the `/` search infrastructure plus the
`_walk_subtree` walker added during the recursive-tag work.

### Design call

Ctrl+F is *not* the same as `/`. The two complement each other:

* `/`: incremental, modeless, **visible nodes only**, local to the
  focused pane. Type and watch the cursor jump.
* `Ctrl+F`: prompt-based, walks the **full source tree** under
  `_root_path` regardless of expansion state, builds a cached
  match list, jumps cursor to first match. Ctrl+G steps through
  the cache.

The two operate on different scopes for different reasons. `/` is
"find what's on screen", Ctrl+F is "find anywhere in the logged
tree." Mixing them — making `/` lazy-expand subtrees during typing —
was already rejected in 2026-05-22 for cost reasons.

For the v0 result UX: in-place cursor stepping rather than a
results-list modal. Matches XTree's feel and avoids the modal-
selection-then-jump dance. A results-modal variant is parked as a
follow-up; the cached `_tree_find_matches` list is already the right
shape to feed one.

The Ctrl+G "no active search" branch flashes a context-aware nudge:
if we've cached an empty result for a previous query, it tells the
user "no matches for `<query>`"; if there's no query at all, it
tells them to press Ctrl+F first. Better than silent no-op when the
user's been searching elsewhere.

### Implementation

* `wtree/widgets/tree_pane.py` — new `reveal_path(target)` method:
  walks the chain from `self.root` down to `target`, calling
  `node.expand()` + `await self._populate(node)` on each segment
  that isn't yet loaded, then drops the cursor on the final node.
  Returns `False` if the target lies outside the root or a segment
  is missing. Uses `os.path.relpath` + a normalised "/" split so
  it handles POSIX and Windows separators uniformly. `os.path.normpath`
  comparison handles the `target == root` short-circuit.

* `wtree/app.py`:
  - New state on `__init__`: `_tree_find_query: str | None`,
    `_tree_find_matches: list[str]`, `_tree_find_idx: int`.
  - `BINDINGS` grew two entries: `("ctrl+f", "find_tree", "Find tree")`
    and `("ctrl+g", "next_match", "Next match")`.
  - New `@work` `action_find_tree`: pushes a `PromptDialog`, walks
    via `_walk_subtree(self._root_path)`, filters basename
    substring case-insensitive (skipping the root itself), caches
    the list, jumps to first match via `tree.reveal_path()`.
  - New `@work` `action_next_match`: cycles through cached
    matches with wrap; flashes context-aware nudge when cache is
    empty.

* `wtree/widgets/menu_bar.py` — Commands menu grew two items:
  `Find tree` (accelerator `f`, shortcut `Ctrl+F`) and `Next match`
  (accelerator `n`, shortcut `Ctrl+G`). Menu items map 1:1 to
  keyboard shortcuts as usual.

* `wtree/widgets/help.py` — keymap reference's Search section
  updated. `Ctrl+G (in /)` line removed (it was misleading —
  Ctrl+G is the global next-match, not a search-bar binding);
  added `Ctrl+F` and global `Ctrl+G` lines.

### Tests

`tests/test_find_tree.py` — 14 new tests:

* `reveal_path`: expands chain root→target; target outside root
  returns False; missing segment returns False; target == root
  lands cursor on root.
* `action_find_tree`: walks the full tree (finds matches inside
  collapsed subtrees); case-insensitive; root excluded from
  matches; no-matches case still caches the query; empty/whitespace
  query cancels.
* `action_next_match`: steps + wraps through the cache; no-active-
  search is a flash-only no-op.
* Wiring: BINDINGS include both keys; Commands menu lists both
  items; Help content mentions Ctrl+F + Ctrl+G.

Two existing test fixes:
* `test_menu.py::test_menus_definition_has_expected_items` —
  Commands menu now `["Search", "Find tree", "Next match", "Untag all"]`.
* `test_menu.py::test_untag_all_from_commands_menu` — Untag all is
  now item 3 (was 1), so Down x3 to reach it.

### Mount fights

Four truncations this session, all caught by `ast.parse` and
recovered via "marker-truncate + tail-from-file-tool":

1. `tree_pane.py` mid-method (after the `reveal_path` Edit).
2. `app.py` mid-`action_menu_bar` (after the two action additions
   plus the new `__init__` state).
3. `help.py` mid-string-literal (after the Search section Edit).
4. `menu_bar.py` mid-method header (after the MENUS update).

The growing pattern: the more Edits a session accumulates against
the same file, the more reliably it truncates. The Python recovery
script is fast at this point (under 5 seconds per fix) so it
doesn't substantially slow the session, but it's a clear signal
that future Cowork integration on this project should prefer
whole-file heredoc writes from the start for any meaningful change
to a `.py` source file.

Two unrelated mount events this session:
* `tests/test_menu.py` got modified by someone else (probably a
  linter) mid-session per the system reminder; the Edit succeeded.
* Stale `.pyc` cache caused the menu tests to spuriously fail on
  first re-run; cleared via `rm -rf /tmp/pyc_wtree`. The
  `PYTHONPYCACHEPREFIX=/tmp/pyc_wtree` environment variable
  remains the right baseline for this project but the cache itself
  needs flushing when Edit deltas touch the test files' imports.

### Tests at end of session

**377/377 green.** Was 363 at tree-pane Left/Right end of session;
+14 from Ctrl+F + Ctrl+G work. Split run across three pytest
invocations:

* `test_app.py … test_packaging.py` (incl. `test_find_tree.py`) → 116 passed
* `test_ops_* … test_status_keybar.py` → 180 passed
* `test_tag_bulk_e2e.py … test_viewer.py` → 81 passed

### State at end of session

Five completed sessions today, all landing user-facing improvements:

1. F1 Help / About — every F-key wired.
2. Tree-pane tagged-node bold-yellow — visual story consistent.
3. Tree-pane auto-refresh after ops — both panes self-heal.
4. Tree-pane Left/Right — drill-in / walk-out without Tab.
5. Ctrl+F find-across-tree + Ctrl+G next-match — search across the
   full logged tree, not just visible rows.

WTree is materially closer to "useable as a daily driver." The
remaining keymap gaps are `G` (goto path), `H` (toggle hidden),
`O` (sort menu), `Ctrl+I` (properties), `Ctrl+R` (refresh source),
`L` (log new source), `!` (shell prompt), and the `--pick` flag.

### Recommended next pickup

- **`L` log new source.** Lets the user add another logged drive /
  path. The tagged set already spans sources; this just lets the
  user *navigate* into one.
- **`Ctrl+R` refresh source.** Forces a re-scan of the current
  view. Cheap follow-up.
- **Smart cursor placement in the rename modal.** Pre-select
  basename-without-extension. Small UX win.
- **Find-tree results-list modal.** Variant on Ctrl+F: instead of
  stepping in-place, show a list of all matches with arrow selection
  + Enter to jump. The cached `_tree_find_matches` is already the
  right shape.


## 2026-05-23 (sixth session today) — `L` log new source

Per the canonical keymap entry. XTree's "L" was "log a new drive" —
WTree generalises to "log a new path", and the architecture has
been ready for this since the tagged-set decision (tags are
absolute paths, source-agnostic).

### Design call

Single-source re-root rather than side-by-side multi-root tree. For
v0 that's the right scope — adding `L` as a sibling-tree mode
would mean a layout change (split panes, source switcher in the
chrome) that lives in a separate, larger design pass.

Path resolution: ``~`` expanded, absolute paths used as-is,
**relative paths resolve against the current root, not cwd**. The
XTree intuition is "I'm in a place, switch to a related place" —
``../sibling`` walks sideways from the current logged context;
``./child`` drills in. This is the smallest implementation that
makes the keystroke feel useful for the workflows the user
already has.

Per the 2026-05-22 design conversation: **blank Enter on the
prompt = ascend**. The existing Left-on-root tree gesture and the
blank-Enter L branch both express "widen the logged window", so I
factored the shared logic into ``WTreeApp._do_ascend()`` and made
``on_tree_pane_ascend_requested`` delegate to it. Both gestures
now stay locked at one source of truth — and the blank-Enter path
discovered an ascend for users who reached for L without first
thinking of Left.

### Implementation

* ``wtree/app.py``:
  - New BINDING: ``("l", "log_new_source", "Log new source")``.
  - New ``@work`` ``action_log_new_source``: pushes a
    ``PromptDialog`` with the current root in the title and a
    placeholder explaining absolute / relative semantics. Empty
    submission routes to ``_do_ascend``. Otherwise resolves the
    typed path (``expanduser`` + ``isabs`` check, ``normpath`` +
    ``join`` for relatives, ``abspath`` final), validates
    ``exists`` + ``isdir``, then ``re_root``s the tree. Flashes
    a status nudge per branch.
  - Factored ``_do_ascend()`` helper from
    ``on_tree_pane_ascend_requested``. Identical behaviour, now
    callable from both surfaces.

* ``wtree/widgets/menu_bar.py`` — Commands menu grew a "Log new
  source" item (accelerator ``l``, shortcut ``L``, action
  ``log_new_source``). Between Next match and Untag all.

* ``wtree/widgets/help.py`` — Navigation section grew an ``L``
  line: "Log new source (prompt for path; re-roots)".

### Tests

``tests/test_log_new_source.py`` — 16 new tests:

* Prompt opens; Esc cancels cleanly.
* Absolute path re-roots.
* Relative ``../sibling`` resolves against the current root.
* ``./child`` resolves to a child of the root.
* ``..`` alone resolves to the parent.
* ``~`` expansion via a monkey-patched ``$HOME``.
* Blank Enter ascends.
* Whitespace-only treated as blank.
* Nonexistent path errors without re-rooting.
* File-not-directory errors without re-rooting.
* Tagged set survives a re-root (absolute paths persist).
* Wiring: BINDINGS, Commands menu entry, Help screen mention.
* Regression: Left-on-root tree gesture still ascends after the
  ``_do_ascend`` extraction.

Existing ``test_menu.py`` tests updated for the new Commands-menu
shape:
* ``test_menus_definition_has_expected_items`` — Commands list is
  now ``["Search", "Find tree", "Next match", "Log new source",
  "Untag all"]``.
* ``test_untag_all_from_commands_menu`` — Untag all is now item 4
  (Down x4 from Search).

### Mount fights

Three Edit-truncations this session — by now the well-worn pattern:

1. ``wtree/app.py`` mid-method after the ``action_log_new_source``
   insertion. Recovered with marker-truncate + tail-from-file-tool.
2. ``wtree/widgets/menu_bar.py`` mid-method header.
3. ``wtree/widgets/help.py`` mid-string-literal.

All caught by ``ast.parse`` and recovered in under five seconds
each. The pattern is so reliable now that the recipe should be
templated for any future session: "if you're going to Edit a
``.py`` source in this project at all, expect to have to rebuild
the tail at least once."

### Tests at end of session

**393/393 green.** Was 377 after Ctrl+F; +16 from this session.
Split run:

* `test_app.py … test_packaging.py` (incl. `test_log_new_source.py`) → 132 passed
* `test_ops_* … test_status_keybar.py` → 180 passed
* `test_tag_bulk_e2e.py … test_viewer.py` → 81 passed

### State at end of session

The XTree-canonical navigation primitives are nearly complete: Tab,
arrows, Enter, Backspace, Space, Tag operations, file operations,
`/`, Ctrl+F, Ctrl+G, F-keys, F9 menu, F1 Help, and now L. Six
shipped features today across six sessions, all daily-driver
ergonomic improvements.

### Recommended next pickup

Remaining canonical-keymap entries (in approximate order of
day-to-day usefulness):

- **`Ctrl+R` refresh source.** Forces a re-scan of the current view
  without changing the root. Cheap follow-up — reuses the existing
  ``refresh_paths`` machinery; just hits all visible nodes.
- **`G` goto specific path.** Like ``L`` but instead of re-rooting,
  it walks the cursor (and contents pane) to the target. Reuses
  ``reveal_path`` from the Ctrl+F work.
- **`Ctrl+I` properties dialog.** Read-only modal showing size, kind,
  mtime, perms, owner, and (when tagged set non-empty) the result
  summary.
- **`H` toggle hidden.** Filter dot-files in / out of both panes.
- **`O` sort menu.** Modal listing sort keys (name, size, mtime,
  kind, ext). Default is current name + kind sort.
- **Smart cursor placement in rename modal.** Pre-select
  basename-without-extension.


## 2026-05-23 (seventh + final session today) — `Ctrl+R` refresh source

Last canonical-keymap entry before Matthew wraps for the day. Pure
follow-up on the auto-refresh infrastructure that landed earlier —
this just exposes a user-initiated full re-scan.

### Design call

Two-pane refresh, expansion-preserving. Contents pane re-runs
``show_path(current_path)``; tree pane runs a new
``TreePane.refresh_all()`` which snapshots the user's drilled-down
state, wipes the tree, and rebuilds it with that snapshot replayed.

The tricky part is preserving expansion across the rebuild. Three
candidate strategies:

1. Walk loaded nodes in place, ``remove_children`` + ``_populate``
   each. Doesn't work cleanly because wiping a parent destroys the
   grandchildren that were just rebuilt.
2. Walk depth-first, but with bottom-up ordering. Fragile and
   ordering-dependent.
3. **Snapshot the set of expanded paths, nuke the tree via
   ``re_root``, then walk down to re-expand each path via the
   ``_walk_to_node`` helper.** Predictable; each walk-down is
   independent. Picked this.

To make the walks share code with Ctrl+F's ``reveal_path``, I
factored the lazy-expand chain into ``_walk_to_node`` which
returns the matching node without moving the cursor. ``reveal_path``
is now just ``_walk_to_node`` + ``cursor_line = node.line``.
``refresh_all`` calls ``_walk_to_node`` for each previously-expanded
path, then calls ``node.expand()`` on the leaf to keep its own
expansion state. Finally ``reveal_path`` restores the cursor.

Paths that no longer exist on disk are silently skipped —
``_walk_to_node`` returns ``None`` for missing segments and the
loop falls through. The user gets a smaller tree without an error
toast.

Distinct from the existing post-op ``refresh_paths(touched_paths)``:
that hits only the dirs the op changed; Ctrl+R is "I think
everything might have drifted, redo everything I have loaded."

### Implementation

* ``wtree/widgets/tree_pane.py``:
  - New ``_walk_to_node(target) -> TreeNode | None`` helper that
    expands the chain root→target lazily without moving the cursor.
  - ``reveal_path`` refactored to a 3-line wrapper around
    ``_walk_to_node`` (preserves the existing public signature).
  - New ``refresh_all()`` async method: snapshots expanded paths +
    cursor backing path, sorts shallowest-first, ``re_root``s the
    tree, walks the snapshot via ``_walk_to_node`` re-expanding
    each leaf, finally ``reveal_path``s back to the cursor's old
    path.

* ``wtree/app.py``:
  - New BINDING ``("ctrl+r", "refresh_source", "Refresh source")``.
  - New ``@work`` ``action_refresh_source``: re-shows contents,
    calls ``tree.refresh_all``, flashes "Source refreshed." Each
    pane in its own try/except so one failure doesn't block the
    other.

* ``wtree/widgets/menu_bar.py`` — Commands menu grew a "Refresh
  source" item (accelerator ``r``, shortcut ``Ctrl+R``).
* ``wtree/widgets/help.py`` — Application section grew a Ctrl+R row.

### Tests

``tests/test_refresh_source.py`` — 12 new tests:

* Contents pane sees a new file added on disk after Ctrl+R.
* Contents pane drops a deleted file after Ctrl+R.
* Tree pane sees a new subdir added on disk.
* Tree pane drops a deleted subdir.
* Expansion state preserved across refresh (a drilled-into dir
  stays open).
* Cursor position preserved when the path still exists.
* Cursor on a deleted path falls through gracefully (refresh
  completes without crash; cursor lands somewhere valid).
* Wiring: BINDING, Commands menu, Help.
* Regression: ``reveal_path`` still works after the
  ``_walk_to_node`` refactor.
* New: ``_walk_to_node`` returns the matching node without moving
  the cursor (the key property that makes refresh_all work).

Existing ``test_menu.py`` tests updated for the new 6-item
Commands-menu shape.

### Mount fights

Four truncations this session — the standard pattern by now:

1. ``tree_pane.py`` mid-method after the ``_walk_to_node`` Edit.
2. ``app.py`` mid-method after the ``action_refresh_source`` Edit.
3. ``menu_bar.py`` mid-method header.
4. ``help.py`` mid-string-literal.

Plus one **silent test-passing regression** caught by paranoid
inspection: ``tests/test_menu.py`` had been mount-truncated mid-test
during an earlier session. Pytest kept reporting "passed" because the
truncation cut *before* the assertion — the test function silently
ended early. Discovered only by comparing bash file size vs file-tool
view. The lesson: **mount truncation can hide failed assertions**,
not just cause SyntaxError. Worth adding "bash-side wc -l + ast.parse
matches expected" as a routine check for any test file Edit going
forward.

All recovered via the marker-truncate + tail-from-file-tool recipe.

### Tests at end of session

**405/405 green.** Was 393 after `L`; +12 from this session. Split
run across three pytest invocations (the full-suite run still
exceeds the bash 45s timeout):

* `test_app.py … test_move_e2e.py` → 114 passed
* `test_native_source.py … test_ops_move.py` (incl. `test_refresh_source.py`) → 152 passed
* `test_ops_queue.py … test_viewer.py` → 139 passed

### State at end of session

Seven completed sessions today. Today's body of work:

1. F1 Help / About — every F-key wired.
2. Tree-pane tagged-node bold-yellow.
3. Tree-pane auto-refresh after ops (`touched_paths` + `refresh_paths`).
4. Tree-pane Left/Right drill-in/out.
5. Ctrl+F find-across-tree + Ctrl+G next-match (`_walk_subtree` reuse + `reveal_path`).
6. `L` log new source (with blank-Enter ascend via shared `_do_ascend`).
7. `Ctrl+R` refresh source (`refresh_all` + `_walk_to_node` factoring).

WTree's canonical keymap is now substantially populated. Remaining
unbound entries: `G` goto path, `H` toggle hidden, `O` sort menu,
`Ctrl+I` properties, `!` shell prompt, plus `--pick` CLI flag.

### Recommended next pickup

The remaining ergonomic gaps in approximate order of daily-driver
usefulness:

- **`G` goto specific path.** Like `L` but moves the cursor instead
  of re-rooting. Reuses ``reveal_path`` and the prompt machinery.
- **`H` toggle hidden.** Filter dot-files in/out of both panes.
  Wire on `ContentsPane` (filter the row list at scan time) +
  `TreePane` (filter `_populate`'s directory list).
- **Smart cursor placement in rename modal.** Pre-select
  basename-without-extension.
- **`Ctrl+I` properties dialog.** Read-only modal showing the
  cursor entry's full stat output, or the tagged set's size totals.
- **`O` sort menu.** Modal listing sort keys.


## 2026-05-25 - `Ctrl+I` Properties inspector + cross-platform owner lookup

First v0.x polish session. Implements the long-parked `Ctrl+I`
properties dialog from the tagged-set-era follow-ups, and ships
the cross-platform owner lookup helper that the skeleton-era
follow-up had reserved as a separate decision. 22 new tests
(21 in `tests/test_properties.py`, 1 in `tests/test_help.py`),
0 regressions. **427/427 green** (was 405/405).

### Design alignment (before code)

Two rounds of `AskUserQuestion` to lock the design before
writing anything:

1. Dir-mode size: **recursive walk, async with cancel** (not
   own-size-only). Tagged-set fields: **count breakdown + total
   file-size sum** (dropped source-id breakdown and newest/oldest
   pair as forward-looking noise). Shape: **ModalScreen** like
   ViewerScreen / HelpScreen. Menu: **File menu after Delete**
   with accelerator `i`.
2. Cancel gesture: **Esc cancels walk first, second Esc dismisses**
   (one-key escape was tempting but losing the partial result was
   the bigger cost). Source-mode fallback: **dropped** in favour of
   a flash on empty selection.

### Implementation

**`wtree/_owner.py`** (new, 2441 bytes): `lookup(stat) -> (owner, group)`.
POSIX path uses `pwd.getpwuid` + `grp.getgrgid`, each wrapped in its
own try/except so a `KeyError` on one ID (common on container images
and synthetic mounts where the local NSS DB is incomplete) falls back
to the numeric ID as a string. Windows path returns `("n/a", "n/a")`
since `pywin32`/`win32security.LookupAccountSid` is still off-limits.
The split is decided at import time (`HAS_PWD_GRP`) via an
`ImportError`-guarded import, so the cost is one try/except at module
load and nothing at call time.

**`wtree/widgets/properties.py`** (new, 16874 bytes):
`PropertiesScreen(ModalScreen[None])` with a discriminator constructor
(`"tagged"` / `"file"` / `"dir"`) plus the data each mode needs. CSS
mirrors HelpScreen (centered, 80% x 80%, `VerticalScroll` body,
docked header + hint labels). Three body builders are pure functions
(`_render_tagged`, `_render_file`, `_render_dir_initial`,
`_render_dir_complete`) so tests assert on Rich `Text` output without
instantiating the screen.

The dir-mode walk (`_walk_directory`) is iterative (stack-based via
`os.scandir`) so deep trees don't blow Python's recursion limit; polls
an `asyncio.Event` once per directory visited so Esc-during-walk takes
effect quickly; per-directory `await asyncio.sleep(0)` keeps Textual
painting. Symlinks are treated as leaves (cycle guard). Permission
errors and other OSErrors are counted and surfaced as a "Walk errors:
N (silently skipped)" row rather than aborting the inspection.

Cancel gesture: `action_escape_pressed` checks `_walk_done` -- if the
walk is still in flight, sets `_cancel_event` and returns (no dismiss);
otherwise dismisses. `_walk_done` flips True at the end of `on_mount`.
`action_dismiss_screen` (Q binding) always dismisses regardless. Hint
text re-renders after the walk completes so the user knows Esc has
flipped from "cancel walk" to "close dialog".

**`wtree/app.py`** -- new `("ctrl+i", "properties", "Properties")`
binding, new `action_properties` (sync, not `@work`: the async work
lives inside the screen). Mode picker: tags non-empty -> tagged mode;
else focused pane's cursor on a dir -> dir mode; else focused pane's
cursor on a non-dir -> file mode; else flash. TreePane cursor reading
uses `node.cursor_node` + `node.data` (always a dir path); ContentsPane
uses `cursor_entry()`.

**`wtree/widgets/menu_bar.py`** -- new "Properties" item inserted in
the File menu after Delete with accelerator `i`. File menu now has 10
items (+1 separator), the existing `test_down_skips_separator` test
flipped from `range(7)` to `range(8)` to compensate.

**`wtree/widgets/help.py`** -- new row in the Application section
documenting `Ctrl+I` -> "Properties (cursor entry or tagged-set
summary)".

### Tests

- `tests/test_properties.py` (new, 21 tests): owner lookup happy path
  / KeyError-fallback / Windows branch; file body field coverage;
  missing-path stat-failure rendering; dir body computing placeholder
  and post-walk totals; cancellation tag in body; walk-errors row;
  tagged-set kind breakdown and unreadable count; recursive walk sum
  + count correctness; cancel via the Event; end-to-end via
  `pilot.press("ctrl+i")` for tagged / file / dir / empty-selection
  paths; Esc and Q dismiss flows; bad-mode rejection at construction.
- `tests/test_help.py` (+1 test): `Ctrl+I` added to the
  `core_bindings` spot-check; new `test_help_content_documents_properties_row`
  to lock the row's presence.
- `tests/test_menu.py` -- updated `test_down_skips_separator` for the
  new 10-item File menu shape; `test_menus_definition_has_expected_items`
  asserts Properties is in `file_items`.

### Mount fights

Four Edit-truncations recovered via the marker-truncate + tail-append
recipe per [[feedback-wtree-mount-rules]]:

- `tests/test_menu.py`: truncated mid-`assert len(app.tagge` after
  the in-place `range(7)` -> `range(8)` Edit. Rebuilt via heredoc
  using the file-tool view.
- `wtree/widgets/help.py`: truncated at the closing parenthesis of
  the Selection-rule paragraph after the Edit that added the Ctrl+I
  row. Heredoc rebuild.
- `tests/test_help.py`: truncated mid `screen.active_menu == 2` after
  the Edit adding `test_help_content_documents_properties_row`.
  Heredoc rebuild.
- `design.md`: truncated mid the new Ctrl+I row's text, the "Open
  questions" section disappeared entirely. Recovered via marker
  truncate (last intact line was the Ctrl+R row from 2026-05-23)
  + Python-string append in a single bash call.
- `todo.md`: lost 6 lines of trailing "Lessons learned" bullets after
  the two `Edit`s ticking the cross-platform-owner and Ctrl+I lines.
  Marker-truncate at the previous intact line + Python-string append
  recovered them.

The pattern is consistent and now well-understood: file-tool view is
the authoritative pre-mount-flush state, bash sees the actual on-disk
state. `wc -c` after any non-trivial Edit + `python3 -c "import ast;
ast.parse(open('file').read())"` is the cheapest probe.

### Decisions captured in design.md

New Decision-log row (2026-05-25) covering the full Ctrl+I design:
three modes + the priority order, cancel gesture, dir-walk
implementation, owner lookup story, surface wiring, symlink
handling. Open-questions section updated with the 427/427 count
and explicit naming of the new feature.

### Next session

Pick from the same v0.x polish queue. The Ctrl+I session unblocked
two long-parked items (owner lookup + properties). The next
candidates from the conversation: smart cursor placement in the
rename modal (preserve `.ext`), progress dialog for large
copies/moves/deletes, or overwrite-policy unification across
Copy/Move/Rename. Each is a discrete session.

### Design session — progress dialog (no code)

Pure design pass, no implementation. Talked through the copy/move
progress modal Matthew has been dreaming about. Committed to
`design.md` as a new `### Progress dialog` subsection under User
interface plus a dated decision-log row; `todo.md` line 75
rewritten to point back at the design instead of re-asking the
questions.

Six-field readout grid: **Percent** (byte-weighted), **Elapsed**,
**Data**, **Rate**, **Drag** = `(1 − bytes_done/bytes_total) ×
elapsed_seconds` (Matthew's quirky readout — derived patience-tax
vibe number, *not* buffer depth; label `Drag` per his call), and
**Files** (preserves the existing N/M signal). **Zero
guard** added at Matthew's request and then unified across both
axes mid-session: any readout touching `elapsed_seconds` *or*
`bytes_done` skips the math and renders `—` while either is
zero. Catches divide-by-zero on Rate, the all-zero opening-paint
on Drag, *and* the big-file-just-opened case where elapsed has
ticked but no bytes have flowed yet (would otherwise produce a
flat `0.0 MB/s` Rate and a Drag spiked to its theoretical max at
second one — both false-meaningful).

Chunk size and redraw rate exposed as tunable module-level
constants in `wtree/ops/queue.py` (`COPY_CHUNK_SIZE = 256 * 1024`,
`PROGRESS_REDRAW_HZ = 10`) — Matthew flagged that rig-tuners will
want sector / cluster alignment and shouldn't have to grep for a
magic literal. `WTREE_COPY_CHUNK` env-var override deferred.
Delayed-show threshold is 4 MiB *or* 50 items *or* 400 ms so
tiny copies don't flash a modal. Cancel (Esc) returns `False`
from the chunk callback to break mid-copy and clean partial files;
completed items stay done.

Implementation pass is queued — needs the new
`on_bytes_progress(item, bytes_done_in_item, item_size, queue)`
callback wired through `apply_plan`'s chunked `copyfileobj`, plus
the `ProgressScreen(ModalScreen[None])` and its threshold gate.

**Concurrency footnote added** (separate decision-log row, same
date). Matthew raised re-entrancy on 128-core hardware and asked
whether Python has anything like C's `static` for the constants.
Answer documented: no real `static`; `typing.Final` is a type-
checker hint only; module-level immutable ints are the closest
thing and are safe to read from any number of threads on any
CPython build (GIL or no-GIL per PEP 703). v0 progress callbacks
fire single-threaded from the asyncio loop so re-entrancy is
impossible by construction. The trap that *would* open if anyone
later parallelizes per-item copy via `ThreadPoolExecutor` /
`ProcessPoolExecutor` is the cumulative counters (`bytes_done`,
`items_done`) and the redraw-throttle timestamp — `+=` is not
atomic, and worker threads can't touch event-loop-affine state
directly. Captured the mitigations (`threading.Lock`,
`itertools.count()`, or the preferred `asyncio.Queue` +
`loop.call_soon_threadsafe()` funnel) as a footnote inside the
Progress dialog subsection so future-contributor sees the rake
before reaching for it.

No tunable-redraw-rate guidance added to the README despite a
brief chuckle about 144 Hz — Matthew chose to let people discover
the diminishing-returns trap themselves.

### Implementation pass — progress dialog

Same day, after the design + concurrency-footnote conversation
landed. Built the whole thing in one session: 427 → 454 tests
green, no regressions.

**Constants** (`wtree/ops/queue.py`, module-level): `COPY_CHUNK_SIZE
= 256 * 1024`, `PROGRESS_REDRAW_HZ = 10`, plus the threshold-gate
trio `PROGRESS_MODAL_BYTES = 4 MiB`, `PROGRESS_MODAL_ITEMS = 50`,
`PROGRESS_MODAL_DELAY_SECONDS = 0.4`. All sit at the top of
queue.py under a `Tuning` comment block with the alignment
landmarks (4 KB sector, 64 KB shutil default, 256 KB SSD sweet
spot, 1 MB NTFS clusters, 4 MB 10GbE / NVMe) so anyone tuning for
their rig has the reference without grepping.

**Queue contract extension**: `OperationQueue.__init__` gained an
optional `on_bytes_progress` callback. New properties
`bytes_progress` (cumulative `(done, total)` summing completed-item
sizes + in-flight chunk progress), `elapsed_seconds` (monotonic
clock; 0.0 when idle), `cancel_requested` flag. New `request_cancel()`
method - sets the flag if a plan is running, no-op otherwise. Worker
loop resets all byte state per plan; `_progress` closure only
credits an item's bytes to the completed bucket on SUCCESS (cancelled
items don't fake-forward the cumulative readout). All single-int
attribute mutations are GIL-atomic, so the worker-thread chunk
callback writing `_bytes_done_current` while the event-loop dialog
reads `bytes_progress` is safe per the concurrency footnote.

**Chunked copy** (`wtree/ops/execute._chunked_copy`): pure-Python
read/write loop using `COPY_CHUNK_SIZE`, fires the new
`BytesProgressCb = Callable[[PlanItem, int, int], bool]` per
chunk (initial zero callback up front so subscribers learn the
size, then one per chunk). Callback returning False breaks the
loop and unlinks the partial dest. After successful copy,
`shutil.copystat` restores mtime/perms to mirror what
`shutil.copy2` would have produced. `_native_copy`'s FILE branch
uses the chunked path **only when** `bytes_progress` is supplied;
absent the callback it falls through to `shutil.copy2` which is
faster for small files and preserves the long-standing test
contract. Move executor left alone for v0 - `shutil.move`'s
cross-fs fallback uses `copy2` internally with no chunk-level
hook, so Rate / Drag render em-dashes for moves until the
underlying executor grows a chunked-cross-fs path.

**ProgressScreen** (`wtree/widgets/progress_screen.py`):
`ModalScreen[None]` mirroring `PropertiesScreen`'s shape. Polls
queue state via `set_interval(1/PROGRESS_REDRAW_HZ)` rather than
subscribing to `on_bytes_progress` - deliberate choice to keep
the dialog event-loop-affine and sidestep the per-chunk worker-
thread fan-out cost. Six-field readout grid (Percent / Elapsed /
Data / Rate / Drag / Files) with byte-weighted bar, unified
zero-guard rendering em-dash on Rate + Drag while either
`elapsed_seconds == 0` OR `bytes_done == 0`. Esc-cancel is
two-stage: first press calls `queue.request_cancel()` and flips
the header to `Cancelling...`; subsequent press dismisses
immediately (so a stuck cancel doesn't trap the user). Auto-
dismiss when the queue's `running` is no longer the plan we
were spawned for - the threshold gate then pushes a fresh
dialog for the next plan if it trips.

**Threshold gate** (`WTreeApp._maybe_push_progress_dialog` +
`_push_progress_dialog_if_running`): called from
`_on_plan_start`. Immediate push when `plan.total_bytes > 4 MiB`
or `len(plan.items) > 50`; otherwise an `asyncio.create_task`
sleeps `PROGRESS_MODAL_DELAY_SECONDS` and pushes only if the plan
is still running (`queue.running is plan` - identity check, not
equality - if the queue moved on, no dialog is warranted). The
push helper scans `self.screen_stack` for an existing
`ProgressScreen` to prevent double-push races between the
immediate branch and a stale delayed-show.

**Tests** (`tests/test_progress_dialog.py`, 27 new):
- constants match what design.md committed to;
- chunked_copy single-file success with monotone callbacks;
- multi-chunk monotone with full + partial-final read;
- mid-copy cancel cleans the partial dest;
- initial-zero cancel writes nothing;
- mtime preservation (via `shutil.copystat`);
- `apply_plan` threads bytes_progress through, fast-path absent
  the callback;
- queue.bytes_progress is None idle, valid running, monotone
  during plan, lands on total at completion;
- elapsed_seconds non-negative during plan, 0.0 after wait_until_idle;
- request_cancel no-op when idle;
- request_cancel mid-plan (deterministic - cancel fired from the
  first chunk callback to avoid wall-clock race on fast disk)
  lands as FAILED with "cancelled" message + cleans dest;
- cancel flag resets between plans (plan1 cancelled, plan2
  succeeds);
- pure helpers: _render_bar 0/50/100% + clamping out-of-range,
  _format_elapsed MM:SS / H:MM:SS, _format_bytes scaling,
  _format_rate scaling, _current_item dst-for-copy / src-for-
  delete / None for empty/overflow;
- zero-guard: elapsed=0 renders em-dash; bytes=0+elapsed>0 also
  renders em-dash (the big-file-just-opened case);
- zero-guard releases once both nonzero;
- Drag formula matches `(1 − fraction) × elapsed` at 25%/10s = 7.50;
- Percent is byte-weighted not item-weighted (470/1000 bytes with
  0/10 items reads as 47%).

**Mount-write protocol notes**: queue.py, execute.py, app.py, and
the new progress_screen.py + test_progress_dialog.py all needed
the stage-in-outputs → atomic-rename → verify-size protocol from
the project memory. Edit-tool writes truncated silently on the
mount each time. Whole-file Write to /sessions/.../mnt/outputs/
then `cp + sync + mv + sync` into the project tree with size
verification worked first try each time after I switched to it
(per `feedback_wtree_mount_rules`). The test file also got
truncated mid-tail through the Write tool itself; bash heredoc
append was the recovery path.

**Suite count**: 427 → 454, no regressions. Ran in four batches
because the sandbox times out the full suite > 30s; one transient
flake on `test_flash_clears_after_timeout` (50 ms flash timeout
losing to sandbox load) confirmed unrelated by clean re-run in
isolation.

## 2026-05-25 (continuation) - Smart cursor placement in Rename modal

Third session of 2026-05-25. v0.x polish item: pressing `R` on
`report.txt` should pre-select the stem `report` so typing
replaces it while `.txt` is preserved — Finder / Windows Explorer
muscle memory. Picked from the previous session's hand-off note
("recommended next pickups: smart cursor in Rename / overwrite
policy / cursor preservation across auto-refresh"). Discrete
session: pure helper + PromptDialog kwarg + one call site.

### Design call

Stem-detection rule, captured before any code (then in design.md
decision log after implementation):

- **Directories** — select whole name. Folders have no extension
  by convention; even `archive.zip/` (rare) selects all so typing
  replaces everything.
- **Files with no dot** (`Makefile`, `script`) — select all.
- **Leading-dot-only dotfiles** (`.bashrc`, `.gitignore`) — select
  all. The dot is identity, not an extension.
- **Trailing-dot** (`foo.`) — select all. Nothing meaningful
  after the dot.
- **Otherwise** — select `[0, rfind('.'))`. So `report.txt` →
  `report`, and `foo.tar.gz` → `foo.tar` (Finder / Explorer treat
  only the **last** `.X` as "the" extension).

Implemented as `wtree/ops/rename.py::select_range_for_rename(name,
kind) -> (start, end)`. Empty name returns `(0, 0)`; range is
always within `[0, len(name)]`. Non-FILE non-DIR kinds (`SYMLINK`,
`OTHER`) treated like files (a `link.txt` is still a `.txt`).

### Implementation

Three edits, all small:

1. **`wtree/ops/rename.py`** — added `select_range_for_rename`
   above `plan_rename`; imported `Kind` from `sources.base`.

2. **`wtree/widgets/prompt.py`** — `PromptDialog.__init__` grew a
   `select_initial: tuple[int, int] | None = None` kwarg.
   `on_mount` reads it after `inp.focus()`: clamps the range to
   `[0, len(self._initial)]` (defensive — buggy callers passing
   `(-1, 99)` shouldn't crash the modal) and assigns
   `inp.selection = Selection(start, end)`. **Critical trap
   avoided:** the first cut also wrote `inp.cursor_position = end`
   after the selection assignment — that immediately reset
   selection to `(end, end)` because `cursor_position.setter`
   calls `selection = Selection.cursor(value)`. Removing that one
   line fixed the failing test; left a comment in the source so
   future-me doesn't reintroduce it.

3. **`wtree/app.py`** — `action_rename` already had `path, _kind
   = cursor`; renamed `_kind` → `kind`, added `stem_range =
   select_range_for_rename(current_basename, kind)`, passed it as
   `select_initial=stem_range` to the PromptDialog. Imported the
   helper alongside `plan_rename` (also re-exported from
   `wtree.ops.__init__`'s `__all__`).

Default behaviour (`select_initial=None`) preserves the
long-standing "Save As" cursor-at-end UX for every other
PromptDialog caller (Copy/Move dest, glob prompts, `L`
log-new-source, Make-new name). Only `action_rename` opts in. The
zero-touch surface for non-Rename callers means no regressions in
the existing 454-test baseline.

### Tests

New file `tests/test_rename_smart_cursor.py`, 26 tests:

- **Pure helper, parametrised, 12 cases**: `report.txt`,
  `notes.md`, `a.b`, `foo.tar.gz`, `.bashrc`, `.gitignore`, `.`,
  `Makefile`, `README`, `foo.`, `weird.`, `archive.zip` (DIR),
  `my.project` (DIR), `mydir` (DIR), empty (FILE), empty (DIR).
- **Non-FILE-non-DIR kinds** (`SYMLINK`, `OTHER`) treated like
  file: `link.txt` → `(0, 4)`; `socket` → `(0, 6)`.
- **PromptDialog selection-on-mount** — push with
  `select_initial=(0, 6)` on `report.txt`, assert
  `tuple(inp.selection) == (0, 6)` and
  `inp.cursor_position == 6`.
- **Default behaviour** — `select_initial=None` lands cursor at
  end-of-text, selection empty.
- **Out-of-bounds clamping** — `select_initial=(-1, 99)` on
  `abc` becomes `(0, 3)`.
- **Empty initial** — `select_initial` ignored when
  `initial=""`.
- **Action layer, pilot** — pressing R on `report.txt` opens the
  modal with `(0, 6)` selected; on `.bashrc` selects whole name
  `(0, 7)`; on `Makefile` selects `(0, 8)`; on `mydir`
  (directory) selects `(0, 5)`.
- **End-to-end, real filesystem** — `tmp_path/src/report.txt`,
  press `R`, sanity-check selection, press `drft` (5 keys),
  assert `inp.value == "draft.txt"`, press Enter, drain queue,
  assert `draft.txt` is on disk with the original contents and
  `report.txt` is gone.

26 new tests; 454 → **480 / 480 green**. Existing rename suites
(`test_ops_rename.py`, `test_rename_e2e.py`) all pass unchanged —
their assertions on `inp.value == "readme.txt"` still hold; the
new selection behaviour doesn't change `value`, only `selection`.

### Mount fights

- **`wtree/ops/__init__.py`** truncated mid-string (`"plan_ren`)
  after the Edit. File-tool view showed correct 75 lines; bash
  saw 71. Recovered via heredoc-rewrite to `/tmp/wtree_init.py`
  then `cp` to the mount path (atomic rename failed
  cross-device).
- **`wtree/widgets/prompt.py`** truncated at line 105 after the
  second Edit (the cursor-position fix). Same heredoc-rewrite
  recipe.
- **`wtree/app.py`** mid-line truncation at `n = len(self.tagged_set`
  (line 1401). Used `head -1400` to keep the intact prefix +
  heredoc-appended the tail (13 lines), `cp` to the mount.
- **`wtree/ops/rename.py`** more devious: bash saw the new
  `select_range_for_rename` and import line correctly, **but**
  the trailing `_basename` / `_parent` helpers (originally lines
  160-176, now 193-208) had silently vanished. Caught by a real
  test failure: `NameError: name '_basename' is not defined`
  inside `plan_rename`. Full heredoc-rewrite recovered the file.
  Lesson: a successful `ast.parse` from bash isn't sufficient —
  it tells you the file is syntactically valid, not that all the
  symbols are still where they were. Run a smoke import of any
  module Edit-touched: `python -c "from wtree.ops.rename import
  plan_rename, _basename, _parent; print('OK')"` would have
  caught this in seconds.

Five truncation rounds total this session; all recovered per
[[feedback-wtree-mount-rules]].

### Decisions captured in design.md

One new Decision-log row (2026-05-25) covering the stem-detection
rule, the PromptDialog `select_initial` kwarg, the
cursor-position trap, and the test surface. Open-questions
section updated with the 480/480 count and explicit naming of the
feature.

### Next session

Same v0.x polish queue. From the previous hand-off plus this one:

- **Overwrite-policy unification across Copy/Move/Rename** —
  Copy clobbers via `shutil.copy2`; Move and Rename pre-check
  `lexists` and fail. A plan-time overwrite/skip/rename prompt
  would unify them. Touches `wtree/ops/copy.py`, `move.py`,
  `rename.py`, and the modal layer. Substantial.
- **Preserve cursor position across auto-refresh** —
  `show_path()` resets the cursor to row 0. Deleting row 5
  should leave the cursor near row 5, not row 0. Snapshot
  cursor path before refresh, try to restore to the same path
  if it still exists, else to a sibling.
- **Initial name suggestion in Make-new** (`New Folder` /
  `untitled.txt`) — small UX win, mirrors Finder / Explorer.
- **Pre-position the cursor on the new entry after Make-new** —
  the post-refresh hook is already in place; just needs to land
  the cursor on the newly-created basename.

Each is a discrete session. The cursor-position-across-refresh
item is the smallest and naturally pairs with the existing
refresh hook; probably the right next session.

## 2026-05-25 (fourth session) - Scan dialog for slow EntrySource.scan

v0.x polish, Matthew-requested. His ask, paraphrased: when he logs
a new folder containing 100k+ files, he doesn't want a frozen
screen - put a centred "subcontracting to <os utility>" dialog on
top, name the os function if easily reachable. After a long-form
design talk (committed as the new "Scan dialog" subsection under
User interface + decision-log row in design.md), implementation
landed in one session.

### Design call

Spec settled on a centred `ModalScreen` with delayed-show gate at
`SCAN_MODAL_DELAY_SECONDS = 0.25` (tighter than progress dialog's
0.4 - directory-entry freezes feel jankier than copy freezes
because the user expects copies to take time). Body shows path,
"via <method_label>" (e.g. "os.scandir"), live entry count, and
"Esc = Cancel". Each `EntrySource` declares its own
`scan_method_label` so the UI renders it verbatim - sources
self-document the syscall layer. Cancel is **immediate** (unlike
progress dialog which needs queue wind-down) because the
consumer checks `ctx.cancelled` between chunks and bails before
committing - no half-render state to clean up.

**Load-bearing prerequisite identified during the design talk**:
without chunked consume in `ContentsPane.show_path` /
`TreePane._populate`, the dialog would never visually appear
during the freeze it was meant to cover. `NativeSource.scan` is
an async generator that yields synchronously between
`os.scandir`'s readdir calls; the consumer's `async for` loop
drains the iterator in one tight burst with no
`await asyncio.sleep(0)` between entries. Event loop ticks, but
Textual gets no paint frames - screen freezes for the duration
of the scan. So chunked consume + atomic commit + dialog ship as
one change.

### Implementation

Files touched:

1. **`wtree/sources/base.py`** - new `scan_method_label` property
   on the ABC, default `"scan"`.
2. **`wtree/sources/native.py`** - overrides to return
   `"os.scandir"` (honest at the Python-API layer; the OS-level
   readdir / FindFirstFile would also be defensible but we name
   what our code actually calls).
3. **`wtree/sources/mock.py`** - overrides to `"mock source"`;
   rarely surfaced since mock scans never hit the threshold in
   practice.
4. **`wtree/widgets/scan_screen.py`** (new file, 231 lines) -
   constants `SCAN_MODAL_DELAY_SECONDS = 0.25` and
   `SCAN_CHUNK_SIZE = 500`; `ScanContext` dataclass (path,
   method_label, entries_seen, cancelled Event, completed Event);
   `ScanScreen(ModalScreen[None])` mirroring `ProgressScreen`'s
   shape, polling at `PROGRESS_REDRAW_HZ` to refresh the entry
   count + auto-dismiss on `completed`; `_truncate_path` helper
   for mid-string ellipsis on long paths.
5. **`wtree/widgets/contents_pane.py`** - `show_path` grew a
   `ctx: ScanContext | None = None` kwarg. When present:
   `await asyncio.sleep(0)` every `SCAN_CHUNK_SIZE` entries,
   writes `ctx.entries_seen = i`, polls `ctx.cancelled` between
   chunks AND at the end (for partial-chunk-at-end). The
   `self.clear()` calls moved from prologue to commit block so a
   cancelled scan leaves the pane on its previous listing.
6. **`wtree/widgets/tree_pane.py`** - same shape on `_populate`.
   On cancel: `self._loaded.discard(node.id)` so the next expand
   retries cleanly. `re_root` and `refresh_all` grew `ctx`
   kwargs that thread down to `_populate`.
7. **`wtree/app.py`** - new `_run_scan_with_dialog(path, source,
   do_work)` helper. Builds the ctx, `set_timer(0.25)` to push
   `ScanScreen` if work still running when it fires, awaits
   do_work, `ctx.completed.set()` in `finally`, dismisses any
   pushed ScanScreen explicitly. Wired at L (re_root), Ctrl+R
   (both panes), tree NodeHighlighted (cursor-onto-dir), and
   initial mount show_path.

### Tests

29 new tests in `tests/test_scan_dialog.py`:

- Constants exist with expected values.
- Per-source `scan_method_label`: native, mock, ABC default via
  a minimal bare subclass.
- `ScanContext`: defaults, cancel mutates, completed mutates,
  independent Event instances across two ctxs (catches accidental
  shared mutable defaults).
- `_truncate_path`: under-limit, over-limit mid-ellipsis,
  extreme-max-width.
- `ScanScreen`: mounts with body text containing path + method
  label + count; Esc sets cancel and dismisses; auto-dismiss when
  `completed` is set; singular "1 entry" vs plural "N entries".
- Chunked consume: `ctx.entries_seen` accumulates; works at
  exactly `SCAN_CHUNK_SIZE` boundary; legacy no-ctx call still
  works; cancel-mid-scan keeps previous listing; cancel-at-end
  keeps previous listing.
- `_run_scan_with_dialog`: fast scan no modal; `completed` set in
  finally; uses source's method_label; native label correct;
  dismisses dialog on completion after a slow do_work.
- Integration: L and Ctrl+R work with the gate (regression checks
  that wrapping doesn't break the normal path); full e2e cancel
  during `show_path` keeps previous listing.

**454 + 26 + 29 = 509 / 509 green.** All five batches clean.

### Mount fights

Massive. Every file I Edit-touched needed heredoc recovery:

- `wtree/sources/base.py` - default `scan_method_label` Edit
  applied; tail (the entire `entry_at` body) silently vanished.
  Caught only when `test_ops_make_new` failures showed "path
  already exists" - turned out `entry_at` was returning `None`
  for missing paths because the function body had been truncated
  to just the docstring + an `import posixpath` line. The bash
  `wc -l` showed 200 vs the file tool's 228. Heredoc recovery of
  lines 200-227 fixed all 12 cascading make-new failures. Lesson:
  ast.parse + simple import smoke aren't enough - **run an
  integration test** after non-trivial Edits.
- `wtree/sources/mock.py` - truncated mid-`scan` method;
  full-file heredoc recovery.
- `wtree/sources/native.py` - truncated in `_entry_from`;
  full-file heredoc recovery.
- `wtree/widgets/prompt.py` - truncated twice during the prior
  session's smart-cursor work (rolled in here for completeness).
- `wtree/widgets/contents_pane.py` - truncated after the
  chunked-consume Edit; full-file heredoc recovery (~420 lines).
- `wtree/widgets/tree_pane.py` - truncated twice; both times the
  marker-truncate + tail-append recipe was faster than full-file
  heredoc because the file is 776 lines.
- `wtree/app.py` - truncated mid-`_maybe_push_progress_dialog`
  docstring after the `_run_scan_with_dialog` insertion;
  marker-truncate + tail-append recipe recovered the trailing
  ~125 lines.

The atomic-rename approach in [[feedback-wtree-mount-rules]] is
not viable on this mount (`mv` from /tmp errors with
`inter-device move failed`). The working recipe is heredoc + `cp`,
or for big files: `head -N intact > /tmp/head.py; cat >> /tmp/head.py
<< 'EOF' ... EOF; cp /tmp/head.py mount/path`. Tracked the recipe
in the project memory's feedback file already.

### Decisions captured in design.md

Two additions:

1. New `### Scan dialog` subsection under `## User interface`,
   covering: why it's distinct from progress dialog; delayed-show
   threshold; chunked-consume load-bearing prerequisite; atomic
   commit on consumer side; `ScanContext` shape; per-source
   `scan_method_label`; layout box-art; cancellation semantics;
   application surfaces; future `BackgroundOperationScreen[T]`
   unification flagged.

2. New decision-log row (2026-05-25) summarising the above.

### Next session

Same v0.x polish queue. From the design call there were a few
follow-ups worth surfacing:

- **Tree-pane Right-arrow expand wrapped in the gate** - parked
  because the expand path runs through `on_tree_node_expanded`
  which is sync and triggers `_populate(event.node)`; threading
  the gate through that callback shape is a bit different from
  the four call sites we wired.
- **Enter-into-dir (`focus_dir_under_cursor`) wrapped** - same
  shape, parked.
- **Ctrl+F walker wrapped** - already `@work`'d so freezing isn't
  the same kind of problem, but a deep-tree walk could use the
  same dialog for "Scanning <root> for matches...".
- **Live count in scan dialog** is now working but is byte-poor:
  it counts entries-seen across all `_populate` calls during
  `refresh_all`, which is correct but reads like one big number
  rather than "currently scanning /a/b: 837 entries; total
  scanned 12,456". A richer two-line readout could split.
- **Cancel-during-refresh-all** stops at the current `_populate`
  boundary, leaving partial expansion state. Acceptable for v1
  but worth a flash hint ("refresh cancelled; tree may be partly
  expanded").
- **Overwrite-policy unification across Copy/Move/Rename** - the
  bigger v0.x item Matthew flagged earlier. Plan-time
  overwrite/skip/rename prompt. Substantial.

Reasonable next pickup: tree-pane Right-arrow + Enter-into-dir
wrap (small completion of the scan dialog story), or the
overwrite-policy work (bigger but well-scoped).


## 2026-05-26 — Progress dialog: minimize / resume

### What landed

Long copies no longer trap the user behind the progress modal.
`m` (lowercase) on the dialog dismisses without setting
`cancel_requested`; the `OperationQueue` keeps running in the
background. `Ctrl+P` (new global app binding) re-pushes a fresh
`ProgressScreen` bound to the same queue — the new screen polls
live state on first paint so it comes up at the actual percentage
the op has reached, not a stale snapshot.

Two-instinct split, not one toggle:

- **`m` = Minimize** on the dialog. Mnemonic; unbound on the
  modal because the app-level `m` for Move is blocked while a
  `ModalScreen` owns the keymap. Single action: `self.dismiss(None)`
  then `self.app.call_after_refresh(self.app._refresh_status)` so
  the StatusLine repaints with the `[Ctrl+P]` discovery hint
  after the screen is off the stack.
- **`Ctrl+P` = Show progress dialog** globally. If `op_queue.running`
  is `None`, flash `"No operation in progress"` (same idiom as
  `Ctrl+G` with an empty find-tree cache). If a `ProgressScreen`
  is already on `self.screen_stack`, no-op (spamming Ctrl+P
  doesn't double-stack). Else `push_screen(ProgressScreen(queue))`.

Cancel and minimize stay separate intents (stop the work vs. stop
showing me the work). Overloading Esc with state-dependent
meaning was on the table briefly and rejected — that's the kind
of UX that gets users to lose data once and never trust the
dialog again.

### Status-line affordance

`StatusLine._build_text` now appends `  [Ctrl+P]` to the existing
`Copy 3/12 items` readout iff the queue is running AND no
`ProgressScreen` is on `app.screen_stack`. Local import of
`ProgressScreen` inside the method keeps the widget→widget
import out of module load. While the dialog is up the suffix
disappears (no point hinting at a key the user can't usefully
press from inside the modal).

### Why no auto-restore on completion

`_on_plan_complete` already fires `notify()`, which surfaces a
toast even if focus has moved to another app. Auto-popping the
dialog after the user explicitly minimized would override their
express layout choice — especially painful if they minimized
*because* they wanted to keep working in the panes. Toast-only
is honest about the contract: the dialog is a view; the queue
runs whether or not you're looking at it.

### Surfaces touched

- `wtree/widgets/progress_screen.py` — `m` binding, `action_minimize`,
  hint label updated to `Esc = Cancel    m = Minimize`.
- `wtree/app.py` — `ctrl+p` binding, `action_show_progress`.
- `wtree/widgets/status_line.py` — `[Ctrl+P]` hint in `_build_text`.
- `wtree/widgets/menu_bar.py` — new "Progress dialog" item in
  Commands menu (accelerator `p`).
- `wtree/widgets/help.py` — `Ctrl+P` row under Application section.
- `design.md` — Progress dialog → Minimize / resume subsection,
  decision-log row (2026-05-26), Ctrl+P row in canonical bindings.

### Tests

`tests/test_progress_minimize.py` — 16 new tests.

- Static surface: `m` in `ProgressScreen.BINDINGS`, `ctrl+p` in
  `WTreeApp.BINDINGS`, `action_minimize` / `action_show_progress`
  present, Commands menu lists the new item, HelpScreen body
  mentions `Ctrl+P`, dialog hint includes `Minimize`.
- StatusLine: hint appears when running + dialog down; vanishes
  when a `ProgressScreen` is on the stack; idle queue gives no
  queue-line at all.
- Pilot integration: idle-`Ctrl+P`-flashes; running-`Ctrl+P`-pushes;
  triple-`Ctrl+P` never stacks twice; minimize dismisses without
  calling `request_cancel`; resume after minimize gives a new
  screen instance bound to the same queue; status text flips
  between hint-present and hint-absent across the minimize cycle.

### Mount fights

Two design.md truncations during initial Edit attempts — the
first wiped the closing prose and the second clipped the
Rename-smart-cursor decision-log row mid-sentence. Recovered
both via `git show HEAD:design.md > /tmp/design_orig.md` + python
rebuild + `cp /tmp/design_new.md mount/design.md`. After that I
went straight to the heredoc-then-cp pattern for every file
larger than ~10KB (app.py was 58KB, status_line.py was 8KB,
progress_screen.py was 12KB). All landed clean by size check.

One new wrinkle this session: `len(unicode_string)` in Python
counts code points, not bytes, so `print(len(src))` reports a
smaller number than `os.path.getsize()` when the file contains
non-ASCII chars (em-dashes in design.md, in the menu_bar
underline marker, etc.). The byte-comparison `mount_size ==
src_size` is the only reliable mount-write check. Updated the
mental model.

### Results

454 → 480 → 509 → **525 / 525** green. Took the full suite in
four batches (66 + 88 + 166 + 124 + 81 = 525 tests across all 41
test files). No flakes this run — `test_flash_clears_after_timeout`
was happy on its first attempt for a change.

### Notes for next session

Remaining ops/queue-era follow-ups from `todo.md`:

- **Conflict detection at plan time** — pre-stat destinations,
  tag PlanItem with overwrite/skip/rename, surface in modal.
  Move and Rename already do a runtime `lexists` pre-check;
  plan-time would be friendlier. Bigger v0.x item.
- **Cross-platform `dst_path` normalisation** for cross-source
  pairs. Small but specific.
- **Cancel a running plan** mid-plan via a cancellation token in
  `apply_plan`. `OperationQueue.stop()` already cancels the
  worker; the gap is per-item.
- **`Plan.apply` shorthand** vs free function — ergonomics call.
- **Move executor chunk hook** so progress dialog can show Rate
  / Drag for cross-fs moves (currently em-dashes). `shutil.move`'s
  cross-fs fallback uses `copy2` internally; replacing it with
  the existing `_chunked_copy` call site + a delete is the path.

Reasonable next pickup: the move-executor chunk hook — small,
self-contained, and closes the "Rate is em-dashed during moves"
gap that the original progress dialog landed without. Or the
plan-time conflict detection if Matthew wants to tackle the
bigger overwrite-policy unification.


## 2026-05-26 (later) — Progress dialog: move executor chunk hook

### What landed

Cross-filesystem moves now render `Rate` / `Drag` properly instead
of em-dashes. The original progress-dialog landing flagged this
explicitly — `shutil.move` does `copy2 + delete` internally on
cross-fs with no chunk-level hook, so byte progress was invisible
for any move that crossed a device. Today's pass unwinds that.

`_native_move` no longer delegates to `shutil.move` wholesale. The
dispatch:

1. **Always try `os.rename` first.** Atomic same-fs for files,
   dirs, and symlinks of any size — no bytes flow, so the progress
   dialog's zero-guard correctly em-dashes `Rate` / `Drag`. Fast
   rename-moves often complete before the 400ms delayed-show timer
   fires and the dialog never appears.
2. **On `OSError`** (cross-fs `EXDEV` on POSIX, `ERROR_NOT_SAME_DEVICE`
   on Windows — caught generically so the same code works on both
   platforms; matches `shutil.move`'s own pattern), dispatch by kind:
   - **FILE**: `_chunked_copy(item, src, dst, bytes_progress)` then
     `os.unlink(src)`. The existing copy chunk path; reused as-is.
   - **SYMLINK**: `os.readlink(src)` + `os.symlink(target, dst)` +
     `os.unlink(src)`. Three short syscalls; no cancel point; no
     bytes flow.
   - **DIR**: keeps `shutil.move`. Recursive walked-progress for
     cross-fs dir moves stays parked (rare case, real code,
     mid-walk cancel + mid-dir errors deserve their own pass).
   - **OTHER**: SKIPPED.

### Scope decision

Files + symlinks chunked, dirs keep `shutil.move`. Matthew picked
the recommended option. Reasoning: cross-fs file moves are the
common case (drag from `~` to `/mnt/backup`, move a video off
the SSD, etc.); cross-fs dir moves are genuinely rare and the
walker would add real code (per-file callback semantics across
PlanItem boundaries, mid-walk cancellation, mid-dir error
handling). The dir case is now documented as a known gap rather
than silently broken — `Rate` / `Drag` em-dash during the
copytree phase of cross-fs dir moves.

### Cancel semantics

Cancel-mid-copy on a cross-fs file move:
- The chunk callback returns `False`.
- `_chunked_copy` cleans the partial destination file.
- The source file is intact (we hadn't started the unlink yet).
- Item returns `FAILED("cancelled")`.

Data-safe by construction. If the user hits Esc halfway through a
big cross-fs move, they get their source file back, no half-state.

### Partial-failure semantics

Copy succeeds, `os.unlink(src)` fails (permissions, file in use,
race with another process): the file exists in both places, and
the item returns FAILED with a clear `"unlink source after copy:
<errtype>: <msg>"` message. Same semantics as `shutil.move` today
(which also leaves the source file behind on a failed final
remove), just with a more specific error.

### Test-contract preservation

When `bytes_progress is None` (headless test runs, scripts that
don't care about per-chunk progress), the FILE branch falls
through to `shutil.move` rather than instantiating a no-op chunk
loop. Same pattern `_native_copy` uses: callback present →
chunked path; callback absent → fast path. Existing move
executor tests are untouched — verified by running the original
46 move/execute tests before touching anything else.

### Surfaces touched

- `wtree/ops/execute.py` — `_native_move` grew the optional
  `bytes_progress` arg and the per-kind cross-fs dispatch. The
  `# Move doesn't take bytes_progress yet` comment in
  `_apply_item` is gone; the MOVE branch now passes
  `bytes_progress` through.
- `design.md` — new "Move executor chunk hook" subsection under
  Progress dialog; new decision-log row (2026-05-26).

No widget changes. No queue changes. No menu / help changes.
Pure executor work; the dialog reads the queue, the queue reads
the executor.

### Tests

`tests/test_move_chunk_hook.py` — 11 new tests.

- Signature: `_native_move` takes optional `bytes_progress`,
  default `None` so existing callers compile.
- Same-fs: rename fast path still works with and without
  callback; with callback the callback never fires (no bytes
  flow — zero-guard does the right thing).
- Cross-fs FILE simulated via `monkeypatch.setattr(os, 'rename',
  …)` raising `OSError(errno.EXDEV)`: with callback the chunk
  path runs and `fired[0] == 0`, `fired[-1] == src_size`; without
  callback the `shutil.move` fast path runs.
- Cross-fs FILE cancel mid-copy: source intact, partial dst
  cleaned, FAILED("cancelled") message.
- Cross-fs FILE unlink failure: source still exists, dst exists,
  FAILED("unlink source after copy: …") message.
- Cross-fs SYMLINK: target read + recreated + source unlinked,
  no bytes fired.
- Cross-fs DIR: keeps `shutil.move`, no bytes fired (documents
  the known gap as a test invariant rather than a footnote).
- `apply_plan` threading: callback reaches `_native_move` via
  `_apply_item`'s MOVE branch.
- Queue integration: `OperationQueue(registry=…).start()` +
  `enqueue(plan)` + `wait_until_idle()` drains correctly for a
  cross-fs move.

### Bug en route

The first cut of the queue-integration test called
`OperationQueue()` and `await queue.start(registry)` — wrong
signature (registry is a positional kwarg on `__init__`; `start`
is sync). And one test asserted `src.stat().st_size` after the
move had already unlinked the source. Both caught by the first
test-file run; fixed in-place; no more failures after.

### Mount fights

One Edit-truncation on the test file at line 438 (the file ended
mid-`assert`); recovered via the same heredoc-rebuild-then-cp
recipe. Confirmed once more: the Edit tool is unsafe on this
mount for any non-trivial change; default to heredoc + `cp /tmp`
for everything that isn't a one-line tweak.

### Results

525 → **536 / 536** green. Five batches: 107 + 84 + 156 + 134 +
55 = 536 across all 42 test files (was 41; +1 for
`tests/test_move_chunk_hook.py`).

### Notes for next session

The progress-dialog story is now feature-complete for the common
case. Files of any size, cross-fs or same-fs, render proper
`Rate` / `Drag` with `[Ctrl+P]` resume support. The remaining
ops/queue-era follow-ups:

- **Cross-fs dir moves with walked progress** — the documented
  gap from today's pass. Recursive walker, per-file callback that
  accumulates `bytes_done` across the PlanItem's contained files,
  mid-walk cancel handling, mid-dir error continuation. Real
  code but well-scoped.
- **Mid-plan cancel token in `apply_plan`** — the queue's
  `request_cancel()` flag is read by `_chunked_copy` per chunk
  but the executor's per-item loop doesn't currently honor it
  between items. Means a cancel during a 50-file plan finishes
  the current item and the rest of the plan; user expected
  cancel to take effect ASAP.
- **Plan-time conflict detection** — pre-stat destinations, tag
  each PlanItem with overwrite/skip/rename, surface in modal.
  The bigger v0.x item Matthew flagged earlier.
- **Cross-platform `dst_path` normalisation** — small but
  specific; needs the cross-source pair test matrix.

Reasonable next pickup: mid-plan cancel token. Small, closes a
real user expectation gap, falls naturally out of the existing
`request_cancel` infrastructure.


## 2026-05-26 (third pass) — Mid-plan cancellation in apply_plan

### What landed

Cancel now stops the rest of the plan, not just the in-flight
item. Before this pass, hitting Esc on a 50-file copy would cancel
the file currently being copied (via the chunk-callback returning
False) but the other 49 items would keep running because the
per-item loop in `apply_plan` never consulted `_cancel_requested`.

`apply_plan` grew an optional `is_cancelled: Callable[[], bool]`
parameter. The per-item loop polls it at the top of each
iteration. Once it returns True, every remaining `PlanItem`
short-circuits to `ItemStatus.SKIPPED` with message `"cancelled"`.

The queue's `_run` wires the closure:

```python
def _is_cancelled() -> bool:
    return self._cancel_requested

result = await apply_plan(
    plan, self._registry,
    progress=_progress,
    bytes_progress=_bytes_progress,
    is_cancelled=_is_cancelled,
)
```

The progress callback still fires for each skipped item so the
dialog's items counter stays consistent with `len(plan.items)`.
The bytes-progress accounting in the queue's `_progress` closure
only credits SUCCESS items, so the bar doesn't lie about what
landed.

### Status split (load-bearing decision)

Two ways an item can be "cancelled":

- **In-flight** — the chunk callback returned False mid-copy.
  `_chunked_copy` cleaned the partial dst, source intact. Item
  ends `FAILED("cancelled")`.
- **Not yet started** — `is_cancelled()` returned True before
  the per-item dispatch ran. Item ends `SKIPPED("cancelled")`.

The semantic distinction is real: one we tried and lost work on,
the other we never attempted. `OperationResult.summary()`
already breaks out `N ok M skipped K failed` so the difference
surfaces in the toast without any UI changes. A 50-file plan
cancelled at item 5 shows: `Copy done: 4 ok 45 skipped 1 failed`.

A future unified `ItemStatus.CANCELLED` is on the table for a
later refactor — it'd collapse the two cancelled-buckets into one
and let the summary read `Copy cancelled: 4 of 50 done`. Not
worth the enum-churn-across-base.py-and-callers cost this pass.

### Pre-item, not mid-item

The check runs *before* each `_apply_item` call. That's the
clean boundary: once cancel fires, no new items start. Mid-item
cancellation (the chunk callback returning False path) is
already wired and remains the only way to stop work *inside* an
item. The two layers don't overlap — pre-item handles the loop
boundary, mid-item handles the file boundary.

### Surfaces touched

- `wtree/ops/execute.py` — `apply_plan` signature + docstring +
  per-item loop. `_apply_item` untouched.
- `wtree/ops/queue.py` — `_run` constructs `_is_cancelled`
  closure and passes it as the new kwarg.
- `design.md` — new "Mid-plan cancellation" paragraph under
  Progress dialog → Cancellation; new decision-log row.

No widget changes. No menu / help / status-line changes. The
dialog reads queue state via its existing poll loop; nothing
about the visible UI moved.

### Tests

`tests/test_midplan_cancel.py` — 8 new tests:

- Signature: `is_cancelled` is an optional kwarg, default None.
- `is_cancelled` returning False always: behaviour matches the
  no-cancel path (regression guard); closure is polled once per
  item.
- Cancel before first item: all items SKIPPED("cancelled"), no
  SUCCESS, no files copied.
- Cancel mid-plan: items 0..N succeed, N+1..end SKIPPED, file
  landings match the SUCCESS/SKIPPED split.
- Per-item progress callback fires for every item (SUCCESS and
  SKIPPED both) — the dialog's items counter relies on this.
- `OperationResult.summary()` surfaces "2 skipped" alongside
  "2 ok" for a cancel-after-2-items run.
- Queue integration: `request_cancel()` from inside
  `on_item_progress` after the first SUCCESS drains the rest of
  the plan to SKIPPED; `queue.completed[0]` reflects the partial
  state.
- Cancel flag resets between plans: plan-1 cancelled mid-flight,
  plan-2 queued behind it runs clean (verifies the existing
  `_cancel_requested = False` reset at plan start).

### Bug en route

The cancel-flag-resets-between-plans test originally tried to
re-stage files in subdirs via `_make_copy_plan(tmp_path / "p1",
…)` without mkdir'ing the parent first. The helper assumed the
parent exists. Fixed by mkdir'ing each plan's parent dir
explicitly before staging. One-line fix; no surprises.

### Cleanup

Caught a deprecation warning while running batches:
`SyntaxWarning: invalid escape sequence '\\\`'` in
`action_show_progress`'s docstring (from the minimize-resume
session — I'd used `\\\`m\\\`` thinking it was Rich-markup, but it's a
Python string literal). Switched to `\`\`m\`\`` (RST inline code) to
match the rest of the docstring's style. Verified with
`python3 -W error::DeprecationWarning -c "from wtree.app import
WTreeApp"`.

### Mount fights

Zero this session. Every file write went through the heredoc +
cp pattern by default; no Edits, no recovery passes. Byte-size
checks all matched on first attempt.

### Results

536 → **544 / 544** green. Five batches: 115 + 84 + 156 + 134 +
55 = 544 across all 43 test files (was 42; +1 for
`tests/test_midplan_cancel.py`).

The known-flaky `test_flash_clears_after_timeout` flaked once
mid-batch (sandbox load vs 50ms flash timeout); reran clean in
isolation as documented in [[feedback-wtree-mount-rules]].

### Notes for next session

Cancel is now feature-complete. The remaining ops/queue-era
follow-ups from `todo.md`:

- **Cross-fs dir moves with walked progress** — the documented
  gap from the move-chunk-hook session. Recursive walker,
  per-file callback that accumulates bytes_done across a
  PlanItem's contained files, mid-walk cancel handling,
  mid-dir error continuation.
- **Plan-time conflict detection** — pre-stat destinations, tag
  each PlanItem with overwrite/skip/rename, surface in modal.
  The bigger v0.x item Matthew flagged earlier.
- **Cross-platform `dst_path` normalisation** — small but
  specific; needs the cross-source pair test matrix.
- **`Plan.apply` shorthand** vs free function — ergonomics call.

Reasonable next pickup: plan-time conflict detection. Chunky but
well-scoped, and it's the last big UX gap before the overwrite
policy can land. Or the cross-fs dir walked-progress for a
smaller, more contained session.


## 2026-05-27 — Bug fix: contents-pane Right-arrow drill-in

### The bug

Matthew reported: with a logged folder a few levels deep (say
`c:\foo\bar`), tab to the contents pane, Right descends into the
first folder fine. Press Right *again* on a folder shown in the
new listing — instead of going one level deeper, the cursor
jumps back to `c:\foo\bar`.

### Root cause

`TreePane.focus_dir_under_cursor(child_path)` is the method the
contents pane calls when the user presses Right on a directory
row. It expands the tree's current cursor node, populates its
children, then sets `cursor_line = matching_child.line`.

The problem: Textual's `Tree` widget rebuilds its line indexer
*lazily on next render*, not synchronously after `node.expand()`.
A child node that was added inside this method's `_populate` call
reports `line == -1` until the indexer catches up. Assigning
`cursor_line = -1` doesn't raise — it deselects the cursor,
which then falls back to row 0 (the logged root).

Why the **first** Right worked: the logged root is auto-expanded
+populated in `TreePane.on_mount` (`self.root.expand()`), so its
children are already laid out and their `.line` values are valid.
`focus_dir_under_cursor` on a root-cursored tree took the "node
is already expanded; populate is a no-op" path and never tripped
the stale-indexer condition.

Why the **second** Right failed: after the first Right, the tree
cursor sat on (say) `c:\foo\bar\test1`. test1 itself had never
been expanded in the tree — the user had only ever seen its
*contents* via the contents pane (which uses its own source scan,
not the tree). The second Right's `focus_dir_under_cursor` was
the first time test1 got expanded; the populate added test1's
children, and the immediate `child.line` read returned `-1`.

`focus_child_of_root` (used post-ascend) already had the same
trap documented in its docstring and fixed with a single
`await asyncio.sleep(0)` after the expand+populate. The yield
gives Textual one tick to rebuild the indexer before the
`.line` read. `focus_dir_under_cursor` was added later and
didn't inherit the fix.

### The fix

One line:

```python
await self._populate(node)
await asyncio.sleep(0)  # <-- new; let the line indexer rebuild
for child in node.children:
    if child.data == child_path:
        self.cursor_line = child.line
        return True
```

Plus an expanded docstring on `focus_dir_under_cursor` that
spells out the trap and points at `focus_child_of_root` as the
sibling that already handles it — so the next person who copies
this pattern doesn't have to rediscover the bug.

### Test

`tests/test_drillin_regression.py` — one focused pilot test:

1. Stage `root/alpha/a1/a1a` on disk.
2. Down-arrow to navigate the tree cursor from root to alpha.
3. Tab to contents pane (cursor at row 0 = a1).
4. Right → assert tree cursor moves to `alpha/a1` and contents
   refreshes to a1's listing with cursor at row 0 = a1a.
5. Right → assert tree cursor moves to `alpha/a1/a1a` (NOT
   back to root, which is what the bug produced).

Without the fix the test fails at step 4 (because in my repro
flow the cursor starts at alpha rather than root, so the bug
shows up one Right earlier than Matthew's flow but with the
same root cause).

### Surfaces touched

- `wtree/widgets/tree_pane.py` — one-line `await asyncio.sleep(0)`
  insertion + expanded docstring on `focus_dir_under_cursor`.
- `tests/test_drillin_regression.py` — new (8 lines of setup +
  five-step pilot).

No design.md change — this was a missed-yield bug, not a design
shift. The behaviour the docstring of `focus_child_of_root`
already commits to ("yield before reading child.line") is now
honoured by `focus_dir_under_cursor` too.

### Mount fights

One: `mv` and `rm` both came back "Operation not permitted" when
I tried to rename the test file from `test_drillin_repro.py` to
`test_drillin_regression.py`. Fixed via the Cowork
`allow_cowork_file_delete` tool — first time I've needed it in
this project. Worth remembering: file deletion on the project
mount needs explicit permission, even for files I just created.

### Diagnostic process worth noting

The first cut of the repro test added `print` statements around
each tab/right press, captured by `pytest -s`. The trace showed:

```
[BEFORE TAB] tree.cursor_node.data='.../alpha'
[AFTER  TAB] tree.cursor_node.data='.../alpha'    # tab didn't move tree cursor
[BEFORE 1st RIGHT] tree.cursor_node.data='.../alpha'
[AFTER  1st RIGHT] tree.cursor_node.data='...'    # tree cursor jumped to ROOT
```

That trace immediately pointed at `focus_dir_under_cursor`
returning "successfully" but landing the cursor on the wrong
node — which narrowed it to `cursor_line` assignment + line
indexer. Grepping for `asyncio.sleep(0)` in the same file found
the sibling method that already documented the exact bug. From
"bug confirmed" to "one-line fix" took less than five minutes.

The lesson: when a Textual cursor assignment seems to do the
wrong thing, suspect the line indexer first. `.line` on a
just-added node lies until the next render.

### Results

544 → **545 / 545** green. Five batches: 116 + 72 + 156 + 146 +
55 = 545 across all 43 test files (was 43; +1 for
`tests/test_drillin_regression.py`, but also -0 because
`test_drillin_repro.py` was renamed-via-delete-and-recreate).

### Notes for next session

This was a sniped-while-paused commit; back to the planned
queue:

- **Plan-time conflict detection** — pre-stat destinations, tag
  each PlanItem with overwrite/skip/rename, surface in modal.
  The bigger v0.x item.
- **Cross-fs dir moves with walked progress** — the documented
  gap from the move-chunk-hook session.
- **Cross-platform `dst_path` normalisation**.
- **`Plan.apply` shorthand** vs free function.

Same recommendation as last session: plan-time conflict
detection is the chunkier-but-load-bearing one; cross-fs dir
walked-progress is the smaller alternative.

## 2026-06-03 — Plan-time conflict detection + resolution

Took the chunky-but-load-bearing v0.x item off the queue: conflicts are
now detected when the plan is built, surfaced in a per-conflict modal,
and resolved by transforming the plan before it enqueues. Replaces the
old three-way inconsistency where Move/Rename hard-failed on an existing
destination and Copy silently clobbered files / merged directories.

Design conversation up front (per the project's design-first rule). Three
forks settled with Matthew via the question tool: detect **per-leaf**
(not just top-level), resolve **per-conflict** (not one policy for the
whole plan), and ship **Skip + Overwrite + Rename** (the full set). The
maximal combination, which created one genuinely sharp case I had to
resolve coherently before writing anything down — see below. Committed to
`design.md` as a new "Conflict resolution dialog" subsection plus a
dated decision-log row.

### The benign-merge insight

Per-leaf detection on Copy's flattened model threatened a nasty cascade:
if a tagged directory's destination already exists and the user picks
Skip/Rename, every descendant item has to follow. The thing that made it
tractable: for COPY, a directory landing on an existing directory is
**not a conflict** — that's the merge that already works correctly. Only
leaf file/other collisions and *type-mismatches* (copying a dir onto an
existing file) are blocking. So every blocking conflict sits on a
childless leaf — except the rare type-mismatch dir, which still cascades
by path-prefix and is the only place the cascade code runs. Move/Rename
emit one item per tag, so any existing dst is blocking and the cascade is
moot there.

### What landed

- **`ops/base.py`**: `ConflictKind` (NONE/FILE/DIR/OTHER) and `Resolution`
  (PROCEED/SKIP/OVERWRITE/RENAME) enums; two new defaulted `PlanItem`
  fields (`conflict`, `resolution`) so the undo-log wire format and old
  call sites are unaffected.
- **`ops/conflicts.py`** (new): `annotate_conflicts` (stats each item's
  dst via `entry_at`, applies the benign-merge rule), `suffixed_name`
  (extension-aware ` (n)` insertion, same last-`.X` rule as the rename
  smart cursor), and `resolve_conflicts` (the plan transform — Skip drops
  item + dir descendants by prefix, Rename rewrites to a collision-free
  name and cascades the prefix, Overwrite tags for the executor).
- **Planners** (`copy`/`move`/`rename`): one line each at the tail —
  `return await annotate_conflicts(plan, registry)`.
- **`ops/execute.py`**: new `_remove_existing_blocking` (unlink/rmtree);
  the Move and Rename `lexists` guards now fail only when the dst exists
  *and* resolution isn't OVERWRITE (they're TOCTOU race-nets now);
  OVERWRITE pre-removes the dst (replace, not merge). Copy's file
  overwrite already clobbered; its type-mismatch dir overwrite pre-removes
  the blocking file.
- **`widgets/conflict.py`** (new): `ConflictDialog` — one row per
  conflict, default Skip, `s/o/r` set the current row, `S/O/R` set all,
  Enter commits the `list[Resolution]`, Esc cancels the whole op.
- **`app.py`**: `_resolve_plan_conflicts` helper wired into
  `_plan_modal_enqueue` (Copy/Move) and `action_rename`; empty-after-skip
  flashes "nothing to do" rather than enqueuing a no-op.

### Mount fights (again)

The flaky project mount truncated writes badly this session — worse than
usual. File-tool Edits to `base.py`, `move.py`, `rename.py`, `app.py` and
`ops/__init__.py` all landed corrupted (mid-docstring truncation, often
in regions I hadn't even touched). `git` itself was unreliable too — an
`index.lock` unlink came back "Operation not permitted". The recovery
that worked: stop trusting live disk state, pull clean baselines via
`git show HEAD:<path>`, re-apply every edit in `/tmp` with a Python script
that asserts each anchor is unique and `ast.parse`s the result, then
`cp` into place and verify byte-for-byte with `wc -c`. The
`feedback_wtree_mount_rules` protocol earned its keep; the new wrinkle is
that the corruption hits *collateral* regions, so size+parse verification
after every single write is non-negotiable, not just on suspect files.

### Two pre-existing tests changed (legitimately)

`test_action_copy/move_uses_cursor/tagged_set` accept the default
destination, which is the item's *own directory* — so dst == src and the
new detector correctly fires a conflict where the old code silently
overwrote a file with itself. Updated the four to drive the dialog
(Overwrite-all, which preserves every item) so they still assert the same
plan shape. Bonus: free integration coverage of the dialog inside the
existing op test files.

### Results

545 → **579 / 579** green. 34 new tests in `tests/test_conflicts.py`:
detection across copy/move/rename incl. benign-merge and type-mismatch;
`suffixed_name` parametrised; `resolve_conflicts` skip/overwrite/rename
incl. dir cascade and the length-mismatch guard; executor OVERWRITE on
real files/dirs/rename/copy/type-mismatch plus the no-overwrite race-net;
`ConflictDialog` state machine; and three e2e app-wiring tests
(overwrite-all, skip-all-does-nothing, Esc-cancels).

### Notes for next session

- **Same-directory operations.** Copying/moving an entry into its own
  directory now always conflicts (dst == src). Overwrite on a *move*
  onto self is nonsensical (it would rmtree the source). A dedicated
  "same-dir / src==dst" check at plan time — no-op for move, auto-suffix
  for copy (the Finder "copy 2" idiom) — would be friendlier. Parked on
  `todo.md`.
- **Make-new** still uses its own exclusive-create path; folding it into
  the conflict flow is parked.
- Remaining v0.x queue items untouched: cross-fs dir walked-progress,
  cross-platform `dst_path` normalisation, `Plan.apply` shorthand,
  persist queue state.

## 2026-06-03 (later) — Recursive-tag scan-dialog feedback + cancellation

Matthew pressed Space on a folder and noticed no progress box appeared
before the worker started churning — "we don't have a delayed feedback
handler in there?" Correct. The tree-pane recursive tag/untag walk
(`on_tree_pane_tag_requested` → `_walk_subtree`) was `@work` so it didn't
hard-freeze the app, but it had **no feedback surface at all**: it
accumulated the whole subtree into a list and flashed a single count at
the end — no in-flight count, no cancel. Two open `todo.md` items
(recursive-tag progress feedback + cancellation).

### Root cause was twofold

Not just the missing dialog. `_walk_subtree` consumes
`self._source.scan()`, which yields *synchronously* between `os.scandir`
calls, and the consume loop had **no `await asyncio.sleep(0)`**. That's
the same load-bearing chunked-consume trap the scan dialog hit back on
2026-05-25: without yielding, Textual gets no paint frames during the
walk, so even if we'd pushed a dialog it wouldn't have rendered until the
walk already finished. So nothing would have popped up regardless. Both
halves had to be fixed together.

### Design call (Matthew picked "reuse the scan dialog")

Offered three shapes via the question tool: reuse the existing ScanScreen
gate, a lightweight periodic status-line flash, or just-diagnose. He took
the consistent option — reuse `_run_scan_with_dialog`. Committed to
`design.md` (Scan dialog → new surface bullet + configurable-header note,
plus a decision-log row).

### What landed

- **`ScanContext.header`** — new defaulted field (`"Scanning"`). `ScanScreen._header_text` now returns `self._ctx.header` instead of a hardcoded literal.
- **`_run_scan_with_dialog(..., *, header="Scanning")`** — threads the header into the ctx it builds. Navigation surfaces unchanged (default); the tag walk passes `"Tagging"` / `"Untagging"` so the modal names the actual operation. Body line still reads `via os.scandir`.
- **`on_tree_pane_tag_requested`** now computes the header and runs the walk under the gate.
- **`_recursive_tag_walk(path, sid, currently_tagged, ctx)`** — extracted as a *named method* (not an inline closure) so tests can drive it with a pre-cancelled ctx exactly the way the scan-dialog tests drive `show_path(ctx=...)`. Consumes `_walk_subtree` in `SCAN_CHUNK_SIZE` chunks, writes `ctx.entries_seen`, polls `ctx.cancelled`, and applies the tagged-set mutation **only after an un-cancelled completion** (atomic commit — Esc leaves the set untouched, mirroring the contents pane's atomic commit on scan cancel).

### design.md was found truncated — recovered

While going to add the design note I discovered the mount had **truncated
`design.md` during the earlier conflict-detection session**: the decision
log was cut off mid-row at the 2026-05-25 rename-smart-cursor entry, and
everything after it — the three 2026-05-26 rows, my 2026-06-03
conflict-detection row, *and the entire "Open questions" section* — was
gone. The "### Conflict resolution dialog" subsection edit had landed but
its sibling decision-log row hadn't, and the write had eaten collateral
content. Recovered by rebuilding from `git show HEAD:design.md` (clean
416-line baseline), re-applying the conflict subsection + row **and** the
new recursive-tag edits via an anchor-asserting Python script, then
byte-verifying. Lesson reinforced (now in [[feedback-wtree-mount-rules]]
rule 16): after any design.md write this session, grep for the tail
section (`## Open questions`) to confirm the file wasn't truncated — a
"successful" markdown write can silently drop everything past the edit.

### Results

579 → **587 / 587** green. 8 new tests in
`tests/test_recursive_tag_feedback.py`: `ScanContext.header` default +
custom, `ScanScreen` rendering the custom header, `_recursive_tag_walk`
tagging / untagging a subtree with `entries_seen` written, the
atomic-commit guarantee under a pre-cancelled ctx for both tag and untag,
and a handler-wiring test (monkeypatched gate) asserting the header is
`"Tagging"` then `"Untagging"` across two Space presses. Full suite
re-run in chunks, all green.

### Notes for next session

- The lightweight periodic-flash alternative is now moot (the modal
  covers it). The "future unified `BackgroundOperationScreen[T]`"
  (ScanScreen + ProgressScreen + Properties' recursive-total placeholder)
  is still the natural next consolidation if these three surfaces keep
  growing parallel features.
- Conflict-detection follow-ups from earlier today still parked: same-dir
  `src==dst` handling, folding Make-new into the conflict flow.

---

## 2026-06-03 (later) — Same-location (self-target) handling

Picked up the parked `src==dst` follow-up from the conflict-detection
session. The bug: aiming Copy/Move/Rename at the directory an entry
already lives in builds `dst_path == src_path`. The conflict detector
stats that destination, finds the entry's *own source*, and flags it as
a normal collision. The dangerous case was **Move**: a dir-into-own-parent
got flagged DIR, and choosing **Overwrite** ran the executor's
`_remove_existing_blocking(dst)` → `shutil.rmtree(dst)` — deleting the
source before the rename. Data loss.

### Design conversation (design-first, two calls put to Matthew)

Offered the two genuine choices via the question tool:

- **Move/Rename self-target** → *Drop + status nudge*. Silently drop the
  no-op item; if the plan empties, flash "already there — nothing to
  move". Mixed plans drop the self-targets quietly. (Rejected: silent
  drop with no feedback; a red PlanError which reads as alarming for a
  benign case.)
- **Copy self-target** → *Confirm via the dialog* (NOT a silent
  auto-suffix). Surface it in `ConflictDialog` defaulting to Rename so the
  user sees and can change it.

Committed both to `design.md` (new "Same-location (self-target) handling"
subsection under Conflict resolution dialog + a decision-log row).

### What landed

- **`ConflictKind.SELF`** — a fifth kind. `ConflictDialog` labels it
  "(existing: same location)" and **defaults SELF rows to Rename** (real
  collisions still default to the safe Skip).
- **`resolve_self_targets(plan)`** in `conflicts.py`, run in `plan_copy` /
  `plan_move` *before* `annotate_conflicts`. Pure data-in/out, no I/O.
  **Copy**: mark the *topmost* `_same_location` item `SELF` (descendants
  left `NONE` so the rename cascade / skip-prefix handles them — keeps the
  dialog to one row per tagged entry, not one per walked leaf). **Move /
  Rename**: drop the no-op item.
- **`annotate_conflicts` skips `_same_location` items** — a self-target's
  "existing" entry is itself, never a real collision. This keeps Copy
  descendants `NONE` (so the SELF root's Rename cascade rewrites them) and
  is defensive even if `resolve_self_targets` weren't called.
- **`resolve_conflicts` needed no change** — a SELF root resolves through
  the existing Rename path (`_free_dst` + prefix cascade → `name (1)`) or
  Skip (drops the subtree via skip-prefix). Overwrite on a SELF row stays
  technically reachable but is caught by the executor guard below.
- **Executor defence in depth**: `_remove_existing_blocking(dst, src)`
  now raises `ValueError` (→ `FAILED` item via `apply_plan`) when `dst`
  *is* or *contains* `src` (`_would_destroy_source` / `_norm`). The three
  OVERWRITE call sites (copy/move/rename) pass `src`. Belt-and-suspenders:
  the plan-time pass should make this unreachable, but a planner bug or a
  post-plan race must never rmtree the user's source.
- **App nudge**: `_plan_modal_enqueue` flashes "already there — nothing to
  <verb>" when the planner returns an empty (items+errors) plan — the only
  producer is the Move self-target drop. Copy self-targets survive as SELF
  items so Copy never hits it.

### Mount-flakiness war story (rule 16, again — worse this time)

The project mount silently truncated **multiple** `Edit`/`Write` results
this session: `wtree/ops/__init__.py` (lost the tail of `__all__`),
`wtree/widgets/conflict.py` (lost every method past `action_cursor_down`),
and the bash mount served *stale* content to Python's import machinery so
`import wtree.ops` failed even though `grep` found the new symbols. The
Read tool (Windows side) and the bash mount disagreed for minutes at a
time. **Resolution / hardened workflow**: stopped trusting in-place mount
edits entirely. Extracted the clean committed tree with
`git archive HEAD | tar -x -C /tmp/wt2` (reads git's object store, immune
to mount staleness), re-applied every edit there via anchor-asserting
Python scripts, ran the suite in the sandbox, then pushed each verified
file back to the mount with **atomic rename + `md5sum` equality check**
(staged `$f.wtmp` → `mv -f`). Confirmed every file md5-matched after copy,
re-ran the executor/conflict suites against the *mount's* actual files
(post-linter), and grepped the `## Open questions` tail of `design.md` to
confirm no truncation. Lesson, sharpened: for this mount, the reliable
authoring path is **sandbox-from-git → verify → atomic-copy-with-checksum**,
never iterative in-place `Edit` on the mount.

### Results

587 → **611 / 611** green. 24 new tests in `tests/test_self_target.py`:
`_same_location` (identity, dot/slash normalisation, cross-source);
`resolve_self_targets` (Copy file→SELF, Copy dir→root-only-SELF, Move
drop, empty); planner integration on a mock (Copy file/dir into own dir,
Move drop-all); `resolve_conflicts` (SELF Rename → `proj (1)` with
descendant cascade and no remaining self-target; SELF Skip → subtree
dropped); `ConflictDialog` (SELF defaults Rename, mixed rows, "same
location" label); the executor guard (`_would_destroy_source` parametrised
+ two real-FS OVERWRITE-onto-self refusals leaving the source intact); and
two app e2e (Move into own dir nudges "already there" with nothing
enqueued; Copy into own dir surfaces the dialog defaulting to Rename and
produces `a (1).txt` on disk). 2 pre-existing `test_ops_move.py` action
tests retargeted to a non-colliding dir — they had accepted the default
(own-dir) destination and relied on the old self-conflict path.

### Notes for next session

- Still parked from the conflict-detection era: **folding Make-new into
  the conflict flow** (it keeps its own exclusive-create + bespoke
  collision message). The SELF machinery is now a natural fit if Make-new's
  single-typed-name case gets generalised.
- New parking-lot item surfaced by Matthew's "confirm via dialog" choice:
  **inline editing / live preview of the suffixed name** in
  `ConflictDialog`. Today a SELF Rename row shows the resolution + the
  (pre-rename) dst path; the actual `name (1)` is computed later in
  `resolve_conflicts`. Showing/editing the concrete target needs either a
  plan-time precompute passed into the dialog or an in-row text input.
- **Cross-platform `dst_path` normalisation** (todo.md) now also matters
  for `_same_location`: a Windows-`\`-separator destination vs a POSIX-`/`
  source wouldn't compare equal under `posixpath.normpath`. v0 native
  paths are POSIX-style on both sides so it holds today; worth folding into
  that broader normalisation pass when it lands.

---

## 2026-06-04 — Fold Make-new into the conflict flow

Picked up after committing the parked self-target work (`c1d93fa`, authored
on top of `534d6fe`). Then closed the long-parked **Fold Make-new into the
conflict flow** item.

### What changed

Make-new previously hard-rejected a leaf collision: `plan_make_new`
pre-statted the leaf and returned `PlanError(cause="Exists")`, and the action
flashed a bespoke message. No Skip/Overwrite/Rename choice; the executor used
exclusive-create only. Now it routes through the same `ConflictDialog` as
Copy/Move/Rename.

- `plan_make_new`: dropped the Exists pre-stat/reject; emits the single
  `MAKE_NEW` item and returns `await annotate_conflicts(plan, registry)`.
  (Dropped the now-unused `ScanError` import; added `annotate_conflicts`.)
- `conflicts._annotate_item`: exempted `OperationKind.MAKE_NEW` from the
  `_same_location` short-circuit. Make-new's `src_path == dst_path` mirror is
  structural, not a duplicate-in-place — without the exemption annotate would
  skip the stat and never flag the collision. Benign dir-on-dir merge is
  COPY-only, so Make-new dir-onto-dir correctly flags `DIR`.
- `execute._native_make_new`: `OVERWRITE` pre-step → `_remove_existing_blocking(dst)`
  with **`src=None`** (passing the mirrored `src` would always trip the
  self-destruct guard; there's no real source to protect).
- `app.action_make_new`: reordered to flash genuine planner errors first,
  then `_resolve_plan_conflicts(plan, "Make-new")`.

**User design call**: full **Skip/Overwrite/Rename** for Make-new (not a
softer Skip/Rename-only), kept consistent with the other ops.

### Mount-flakiness, again (rule 16)

The project mount truncated `wtree/ops/make_new.py` to 230 lines in the bash
view *after* the Edit tool wrote the full file to the Windows side — the
Read tool (Windows) and the bash mount disagreed, exactly the worklog's
recurring gremlin. Since `git commit` runs through the bash view, committing
then would have committed a truncated file. Resolution: rebuilt the tree in a
pure sandbox via `git archive HEAD | tar -x`, re-applied all edits there with
anchor-asserting Python, ran the suite green, then pushed every changed file
back to the mount with **staged-tmp → atomic `mv -f` → `md5sum` equality
check** (all 6 matched), and re-ran the make-new slice against the *mount's*
actual files to confirm. Also cleared a stale `.git/index.lock` (and the
`HEAD.lock` / `master.lock` git left behind — the mount blocks unlink, so
each git write leaves a lock; `mv`-aside works where `rm` doesn't).

### Results

611 → **620 / 620** green. New/updated coverage: `tests/test_ops_make_new.py`
(annotate FILE/DIR/NONE, resolve Rename/Skip/Overwrite, real-FS apply
Overwrite-replaces-dir + Overwrite-replaces-file + Rename-suffixes, action
surfaces-dialog-and-cancels) and `tests/test_make_new_e2e.py` (clobber
Skip/Overwrite/Rename keystroke e2e, replacing the old clobber-refused test).

### Notes for next session

- Still parked from the conflict era: **inline edit / live preview of the
  suffixed `name (n)`** inside `ConflictDialog` (a Rename row shows the
  pre-rename dst path; the concrete `name (1)` is computed later in
  `resolve_conflicts`). Make-new now feeds this dialog too, so the payoff is
  a touch larger.
- **Cross-platform `dst_path` normalisation** (todo.md) still outstanding;
  matters for `_same_location` and now for Make-new's leaf comparison on a
  Windows-`\` vs POSIX-`/` destination.

---

## 2026-06-04 (cont.) — Live preview of the Rename target in ConflictDialog

Took the parked "inline live-preview of the suffixed name" item (Matthew's
pick; the dst_path normalisation is next after this).

### What changed

Before: a Rename row in `ConflictDialog` showed the *pre-rename* `dst_path`;
the concrete `name (1)` was only computed later inside `resolve_conflicts`,
so the user committed a Rename without seeing what they'd get. Now the row
shows `-> name (1)` inline the moment it's set to Rename.

- `conflicts.py`: new public `preview_renamed_dst(item, registry)` — a thin
  wrapper over the existing `_free_dst` suffix-hunt, so the preview reuses the
  exact logic `resolve_conflicts` uses (same per-item independence, no
  cross-row cascade). Previewed name == committed result when the FS is
  unchanged between dialog-open and apply. Exported via `wtree/ops/__init__`.
- `app._resolve_plan_conflicts`: precompute `previews = [await
  preview_renamed_dst(i, sources) for i in conflicts]` and pass
  `ConflictDialog(conflicts, previews=previews)`.
- `widgets/conflict.py`: `__init__` gains optional `previews` (parallel to
  items, length-guarded — items-only construction still works); `_row_text`
  appends `  -> {basename(preview)}` **only** on RENAME rows. Toggling a row
  re-renders through the existing `_refresh_row`, so the preview is live.
  SELF rows (default Rename) show their duplicate name immediately.

**Scope**: preview only. Inline *editing* of a custom target (an in-row text
input) stays parked — a bigger feature (validation, re-stat on every
keystroke) for another day.

### Workflow

Clean run — did the whole thing in the sandbox from `git archive HEAD` from
the start (no in-place mount Edits this time), anchor-asserting Python +
`ast.parse` after every patch, suite green in the sandbox, then pushed the 5
changed files back to the mount with atomic `mv -f` + `md5sum` equality
(all matched), and re-ran the slice against the mount's own files. No
truncation gremlin surfaced this session.

### Results

620 → **626 / 626** green. 6 new tests in `tests/test_conflicts.py`:
`preview_renamed_dst` matches the resolve result; dialog rendering
(rename-row-shows / overwrite-row-hides / self-row-shows-duplicate /
items-only-unchanged); and an e2e copy-collision that sets the row to Rename,
asserts `-> a (1).txt` in the row, commits, and confirms the duplicate landed
while the original stayed put.

### Notes for next session

- **Cross-platform `dst_path` normalisation** (todo.md) — Matthew's stated
  next pick. Matters for `_same_location` (Windows `\` vs POSIX `/`), the
  Make-new leaf comparison, and now the preview's basename split.
- Inline *editing* of the suffixed name in the dialog remains parked.

---

## 2026-06-04 (cont.) — Cross-platform dst_path normalisation

Matthew's chosen follow-up after the preview work. Closes the long-parked
`dst_path` normalisation item and the recurring `_same_location` caveat
("a Windows-`\`-separator destination vs a POSIX-`/` source wouldn't compare
equal under `posixpath.normpath`").

### Design (two decisions, both confirmed with Matthew)

1. **Identity comparison = separator + case-insensitive on Windows.** New
   shared `canonical_path(path, *, case_insensitive=os.name=='nt')` in
   `ops/base.py` flips `\`->`/` (`to_posix`), collapses dots/redundant
   slashes (`posixpath.normpath`), and `.lower()`s when case-insensitive
   (NTFS default; POSIX case-sensitive; macOS treated case-sensitive, a known
   soft spot). The flag is a *parameter* so the Windows behaviour is unit-
   testable on this POSIX sandbox.
2. **Normalise at the boundary too.** Typed Copy/Move destinations are
   `to_posix`'d in `_plan_modal_enqueue` before becoming the destination
   `Tag`, so stored `dst_path`, the dialog row, and the rename preview's
   `posixpath.basename` stay single-separator.

### Code

- `ops/base.py`: `to_posix` + `canonical_path` + `_PATHS_CASE_INSENSITIVE`.
- `conflicts._same_location`: now `canonical_path(src) == canonical_path(dst)`.
- `execute`: dropped `_norm`; `_would_destroy_source` routes through
  `canonical_path` and its ancestor test switches `+ os.sep` -> `+ "/"`
  (canonical form is `/`-separated). Both guards now judge "same location"
  identically to plan time.
- `make_new`: private `_to_posix` folded into the shared `to_posix`.
- `app`: typed destination canonicalised via `to_posix`; `to_posix` re-
  exported from `wtree.ops`.

### Workflow

Clean again — whole change built in a sandbox from `git archive HEAD`,
anchor-assert + `ast.parse` per patch, suite green, atomic `mv -f` +
`md5sum` push-back, re-ran the slice against the mount's files. One linter
touch split a `from wtree.ops.base import` in two on the mount; merged it
back into a single block (re-verified md5) so the import stays tidy. No
truncation gremlin.

### Results

626 → **641 / 641** green. 15 new tests in `tests/test_path_norm.py`:
`to_posix` (flip / noop / mixed); `canonical_path` (dot collapse, separator
unify, case-sensitive keeps case, case-insensitive folds case+separators,
os-default-flag wiring); `_same_location` separator unification + still-
distinguishes-real-difference; `_would_destroy_source` identity/ancestor/
unrelated across separators; and an app e2e typing a `\`-separator own-dir
destination that surfaces the SELF/duplicate dialog.

### Notes for next session

- **Cross-*source* path translation** (native<->archive, native<->remote)
  is the remaining normalisation work, deferred until a second source type
  exists — `canonical_path` is single-convention (POSIX-flavoured) and
  assumes one filesystem's identity rules.
- macOS case-insensitivity is still treated as case-sensitive
  (`_PATHS_CASE_INSENSITIVE = os.name == 'nt'`); revisit if/when macOS
  becomes a daily-use platform (would need per-volume detection, not just
  os.name).
- Inline *editing* of the suffixed name in `ConflictDialog` remains parked.

---

## 2026-06-04 (cont.) — Inline editing of the Rename target in ConflictDialog

The last parked conflict-flow item, and the close of the whole arc
(self-target -> Make-new fold -> rename preview -> path normalisation ->
inline edit).

### Design (two decisions, confirmed with Matthew)

1. **Verify-free with re-prompt.** Pressing `e` on a row pops a PromptDialog
   pre-filled with the current effective target. The typed value is validated
   by the shared `resolve_relative_leaf` and **re-stat'd** via an async
   `name_exists(item, path)` checker the app supplies; an invalid or
   already-existing target re-prompts with the reason on the hint line, so the
   accepted name is guaranteed collision-free.
2. **Relative subpath allowed** (`sub/leaf`), Make-new-style — not
   basename-only. Intermediate dirs are created by the executor at apply.

### Code

- `ops/base.py`: extracted `resolve_relative_leaf(parent, typed) ->
  (leaf | None, error | None)` — the lenient segment-walk (flip separators,
  reject absolute / `..` / empty, build leaf under parent). **Make-new now
  delegates to it** (wrapping the error as an `InvalidName` PlanError;
  asserted substrings - "empty"/"absolute"/".." - preserved), killing the
  duplicate validation.
- `widgets/conflict.py`: `e` -> `@work action_edit_name` (push_screen_wait
  must run off the message pump) loops PromptDialog -> validate -> re-stat ->
  store or re-prompt. New `name_exists` ctor arg; `_custom` per-row list;
  row shows `-> custom (edited)` (relative to parent so subpaths stay
  legible). **Return type changed** from `list[Resolution]` to
  `(list[Resolution], list[str | None])`.
- `conflicts.resolve_conflicts`: keyword `custom_dsts=`; a RENAME row with a
  custom dst seeds `rename_map` with it verbatim, so the existing
  prefix-cascade rewrites descendants onto the custom target. Length-mismatch
  raises (wiring guard).
- `app`: `_conflict_target_exists` (stats the dst source); `_resolve_plan_
  conflicts` passes `name_exists=`, unpacks the tuple, threads `custom_dsts`.

### Workflow

Clean. Whole change in a sandbox from `git archive HEAD`; the dialog was a
full-file rewrite (too many touch-points for safe anchors) written via
heredoc since the Write tool can't reach the sandbox path; everything else
anchor-asserted + `ast.parse`d. Suite green, atomic `mv -f` + `md5sum`
push-back (all 8 matched), re-ran the slice against the mount's files.

### Results

641 -> **659 / 659** green. 18 new tests: 11 `resolve_relative_leaf` units in
`tests/test_path_norm.py`; 4 `resolve_conflicts` custom-dst + 3 inline-edit
e2e (edit-to-custom-name, reject-existing-then-accept, relative-subpath) in
`tests/test_conflicts.py`.

### Notes for next session

- **Cross-*source* path translation** (native<->archive/remote) is the only
  remaining normalisation/path item, deferred until a second source type
  exists.
- The conflict-resolution UX is now feature-complete for v0.x: Skip /
  Overwrite / Rename (auto-suffix), live preview, and custom-name editing
  with verify-free, across Copy / Move / Rename / Make-new.

---

## 2026-06-04 (cont.) — Destination browser for Copy/Move (BUILT)

Built the picker designed earlier today (committed `c6807c4`). Lets the user
browse to a Copy/Move destination instead of typing it.

### What shipped

- **`wtree/widgets/dir_picker.py`** (new): `DirPickerScreen(ModalScreen[str |
  None])` + `_PickerTree(Tree[str])`. Dir-only navigable tree; Enter on a dir
  dismisses with its path; Esc -> None. `_PickerTree` ports TreePane's lazy
  `_populate` (dir-only), Left/Right (collapse/parent, expand/drill),
  Backspace-to-parent, and `reveal_path`/`_walk_to_node`, dropping tagging /
  ascend. `n` is a `@work` worker: prompt -> shared `resolve_relative_leaf`
  (relative subpath OK) -> verify-free re-prompt -> create via the executor's
  `_make_new_blocking` (intermediate dirs) -> repopulate + reveal + select.
  Footer shows the dir under the cursor as the prospective target + the
  tagged-item count.
- **`PromptDialog`**: `browse=True` mode + `BROWSE` sentinel; **Ctrl+B**
  (refined from the design's `b` — a plain `b` would be eaten by the focused
  `Input`; Ctrl+B is not an `Input` binding) dismisses with `BROWSE`. Inert
  for every other PromptDialog caller.
- **`ops/base.drive_anchor(path)`**: drive/share root (`/` POSIX, `C:\` etc.
  via `splitdrive`), re-exported from `wtree.ops`.
- **`app._plan_modal_enqueue`**: the destination prompt is now a loop —
  Ctrl+B -> push `DirPickerScreen(start_root=drive_anchor(current),
  reveal_target=current)` -> picked dir becomes the prompt's prefill ->
  reopen. Single confirm point; everything downstream (same-location,
  conflicts, preview, inline-edit, normalisation, executor) is untouched
  because the picker only supplies a path.

### Scope realised vs designed

- **Ctrl+B not `b`** (Input would swallow `b`). Noted in design.md.
- **Scan-*dialog* (cancel UI for slow dirs) deferred to phase 2** for the
  picker: the first cut populates inline like TreePane's drill-in; the
  chunked async scan keeps it responsive. Avoided pushing a ScanScreen over
  the picker modal for now. Still phase 2: drive/share switching, type-to-
  filter, files-greyed-for-context, and extracting a shared dir-populate
  helper so picker/TreePane don't drift.

### Testing notes

`tests/test_dir_picker.py` drives the picker through a real `WTreeApp` +
`NativeSource` on `tmp_path`, pushed via `app.push_screen(..., callback=...)`
(no worker dance) with `start_root=tmp_path` for deterministic small trees;
the app browse-loop wiring test goes through the real Copy flow (Ctrl+B opens
the picker, Esc returns to the prompt). 8 picker tests + 2 `drive_anchor`
units.

### Results

659 -> **669 / 669** green (the lone parallel-run red is the known
`test_flash_clears_after_timeout` timing flake; passes serially).

### Notes for next session

- Picker **phase 2**: drive/share switching (enumeration; ties to the parked
  Network-discovery item), type-to-filter (reuse `/`-search), scan-dialog
  cancel-UI for huge dirs, files-greyed-for-context.
- **Cleanup**: extract the dir-populate loop shared by `_PickerTree` and
  `TreePane` into one helper so they don't drift.

---

## 2026-06-04 (cont.) — Picker scan-dialog cancel-UI for slow expands

Closed the phase-2 item deferred when the destination browser shipped: a
slow directory expand now shows a cancellable scan dialog instead of a
silent inline populate.

### What changed (`wtree/widgets/dir_picker.py`)

- `_PickerTree._populate` gained the ctx-chunked cancel path (mirrors
  `TreePane._populate`): with a `ScanContext` it writes `entries_seen`,
  yields every `SCAN_CHUNK_SIZE` entries, polls `ctx.cancelled`, and on
  cancel drops the `_loaded` marker and returns **before** adding children
  (atomic - the node stays empty + re-expandable). Without a ctx it's the
  legacy one-shot drain (reveal walk, tests).
- New `_expand_with_dialog(node)`: routes an interactive expand through
  `WTreeApp._run_scan_with_dialog` (the gate already used by the contents
  pane / L / Ctrl+R), so a still-scanning dir shows a `ScanScreen` after the
  short delay; fast expands never flash it. Falls back to a bare populate
  when `self.app` has no gate (keeps `_PickerTree` usable standalone).
- `on_tree_node_expanded` now calls `_expand_with_dialog`; the Right-key
  handler just `node.expand()`s (which posts `NodeExpanded`) and no longer
  populates inline, so the gate gets its chance. `reveal_path` + the initial
  root populate stay bare - the cancel-UI is for *interactive* expands.

The gate's own docstring already listed "tree-pane Right-arrow expand" as an
intended future caller, so this is the sanctioned pattern (and the picker now
gets cancel-UI that even `TreePane`'s own expand doesn't have yet).

### Results

669 -> **672 / 672** green. 3 new tests in `tests/test_dir_picker.py`:
pre-cancelled `_populate` leaves the node empty + marker dropped; a ctx scan
counts `entries_seen` and commits children; an interactive expand routes
through a spied `_run_scan_with_dialog`.

### Notes for next session

- Picker **phase 2** remaining: drive/share switching (enumeration; ties to
  Network-discovery), type-to-filter (reuse `/`-search), files-greyed-for-
  context.
- **Cleanup**: extract the dir-populate loop now duplicated by `_PickerTree`
  and `TreePane` into one shared helper - they've drifted a little further
  apart with this change (the picker's ctx path is a near-copy of TreePane's).

---

## 2026-06-04 (cont.) — Cleanup: extract the shared dir-populate helper

`TreePane._populate` and the picker's `_PickerTree._populate` had become
near-identical (the scan-dialog cancel-UI work copied the ctx-chunked path
into the picker). Removed the duplication.

### What changed

- New `scan_screen.populate_dir_node(node, source, loaded, *, ctx=None)` -
  the dir-only scan-into-tree-node body, lifted verbatim and parked next to
  `ScanContext` / `SCAN_CHUNK_SIZE` (its natural home). Idempotent via the
  caller's `loaded` set; ctx-chunked atomic cancel as before.
- `TreePane._populate` and `_PickerTree._populate` are now one-line
  delegations to it. The dir-scan logic lives in exactly one place; the two
  widgets can't drift again.
- Trimmed now-unused imports (`Entry`/`Kind`/`ScanError` from `tree_pane`,
  `Entry` from `dir_picker`), pyflakes-confirmed. (`scan_screen` gained
  `os` + a `from wtree.sources.base import Entry, Kind, ScanError` + a
  TYPE_CHECKING `TreeNode`/`EntrySource`.)

Pure refactor - behaviour identical, proven by the unchanged TreePane
navigation / scan-dialog / refresh / recursive-tag and picker suites all
passing untouched.

### Results

672 -> **673 / 673** green (1 direct `populate_dir_node` test added; the lone
parallel-run red is the known `test_flash_clears_after_timeout` flake,
serial-passes). Installed `pyflakes` in the sandbox to verify the import trim.

### Notes for next session

- Picker **phase 2** remaining: drive/share switching, type-to-filter,
  files-greyed-for-context.
- Pre-existing pyflakes nits noticed (not touched, out of scope): `app.py`
  `Resolution`, `properties.py` `field`/`Sequence`, `copy.py` `walked_iter`,
  `execute.py` `exc`, `sources/base.py` `field`. A future lint-sweep could
  clear these.

## 2026-06-05 — ScanScreen double-dismiss crash fixed (HALTED for user's pending update)

**Bug.** Launching `wtree` crashed with `ScreenStackError: Can't pop screen; there must be at least one screen on the stack` from `ScanScreen._refresh` → `self.dismiss(None)` → `app.pop_screen`, with only `Screen(id='_default')` left on the stack.

**Root cause.** Three callers race to close the scan modal: the redraw timer (`_refresh`, on `ctx.completed`/`ctx.cancelled`), the Esc handler (`action_cancel`), and the gate's `finally` block (`WTreeApp._run_scan_with_dialog`). Textual's `dismiss` pops the stack unconditionally, so whichever caller fires second pops the base `_default` screen and raises. The screen's polling timer (PROGRESS_REDRAW_HZ) had a callback already queued when the gate's `finally` dismissed, so it fired post-pop.

**Fix.** Added `ScanScreen.safe_dismiss()` — idempotent (`self._dismissing` flag) and stack-membership-guarded (`if self in self.app.screen_stack`), wrapped in the existing torn-down `except`. Routed all three callers through it: `_refresh`, `action_cancel`, and the gate's `finally` in `app.py` (`screen.safe_dismiss()`). Files: `wtree/widgets/scan_screen.py`, `wtree/app.py`.

**Verification.** `tests/test_scan_dialog.py` → 29/29 pass. Full suite NOT run to completion: the sandbox mount went flaky again (truncated/stale reads — pyflakes saw a `main()`→`ma` truncation and phantom unused imports that grep disproved) and `pytest-asyncio` had to be reinstalled in the sandbox. **Halted at user's request to apply a pending update before running the full suite.**

**Pickup for next session.**
- Matthew to run the full suite locally (Python 3.14, Windows) — expect 673 prior + scan-dialog still green; confirm no regression.
- Consider the same `safe_dismiss` guard for `ProgressScreen` (`progress_screen.py` lines ~166, 182, 195) — same double-dismiss shape, not yet observed crashing but latent.
- Crash handler: WTree has NO global handler; the Rich traceback seen is Textual's built-in default. Matthew has one in another project to potentially drop in — not yet wired.

## 2026-06-05 — Crash reporter dropped in (vendored `describe_error`)

Design-first pass (signed off by Matthew), then built. WTree had no crash handler — unhandled in-loop exceptions surfaced as Textual's default Rich dump.

**Grounding finding (drove the design):** Textual 8.2.7's `App.run()`/`run_async` does **not** re-raise in-loop exceptions. `_handle_exception` stashes the error, prints its own Rich traceback to the exit screen, sets return code 1, and `run()` returns *normally*; only the test-pilot context re-raises. So a `main()` wrapper alone would miss every event-loop crash (e.g. the same-day `ScanScreen` one). The only reliable hook is `_handle_exception`.

**Built:**
- Vendored `error_handler.py` → `wtree/error_handler.py` verbatim (pure stdlib, zero deps) with a provenance header (synced from `Python ErrorHandler` @ `23af8d3`).
- `wtree/crash.py` glue: `install_crash_redactors()` (default scrub set: `sk-…`, `password=…`, `token=…`), `build_report()` (locals gated by `WTREE_DEBUG=1`, off by default), `write_crash_log()` → `~/.wtree/crashes/crash-<UTC>-<pid>.log` (for_claude text + to_dict JSON; never raises, never writes into the flaky project mount).
- `WTreeApp._handle_exception` override: build+persist report, stash `self._crash_report`/`self._crash_log_path`, then `super()._handle_exception` (Textual's dump + teardown kept — design decision: keep dump + add logfile + pointer; owning the exit screen is parked phase-2).
- `main()` two nets: outer `try/except` for construction/teardown errors; post-`run()` check surfaces a stashed in-loop crash with a one-line `full report written to <path>` pointer + `sys.exit(1)`.
- 11 tests in `tests/test_crash_handler.py` (redaction, WTREE_DEBUG locals gate, log write + never-raise, `_handle_exception` stash+delegate, both `main()` nets, clean-exit). **673 → 684/684 green** (run in quarters; the full all-at-once run flaked one timing-sensitive dialog test under the 44s sandbox timeout — green every time in isolation).
- design.md: new "Error handling and crash reporting" section + dated decision-log entry.

**Follow-ups (next session):**
- `ProgressScreen` has the same latent double-dismiss shape as the fixed `ScanScreen` — apply the same `safe_dismiss` guard (`progress_screen.py` ~166/182/195).
- Pre-existing lint (NOT from this work): `wtree/app.py` imports `Resolution` from `wtree.ops` but never uses it — single occurrence at HEAD too. Trivial trim when convenient.
- Phase-2 option: fully own Textual's exit screen (replace the Rich dump with a clean "WTree hit an error — report at <path>" panel) instead of keeping both.

## 2026-06-05 (later) — Cancellable O(n) Copy/Move planning (big-tagged-set freeze fix)

Matthew hit a hard freeze copying a recursively-tagged tree (~349,773 tags) to E:. Diagnosed two causes; fixed both. Ctrl+Break/faulthandler freeze-dump was considered but dropped at his call (may not need it).

**Cause 1 — plan-build not gated/chunked.** `plan_copy`/`plan_move` (walk + `annotate_conflicts`) ran to completion off the event loop: no dialog, no Esc. Fix: the planners take optional `on_progress`/`should_cancel` plain callables (NOT a `ScanContext` — the ops layer must not import `widgets`), `await asyncio.sleep(0)` every `PLAN_CHUNK_SIZE` (new constant in `ops/base.py`), and raise `ScanCancelled` (new, `ops/base.py`) when `should_cancel` fires at a chunk boundary. `_plan_modal_enqueue` now runs the planner under `_run_scan_with_dialog` (header "Planning copy/move"; `on_progress`→`ctx.entries_seen`, `should_cancel`→`ctx.cancelled.is_set`) and catches `ScanCancelled` → flash "cancelled", nothing enqueued (atomic). `_run_scan_with_dialog` made generic (returns whatever `do_work` returns — here the `Plan`).

**Cause 2 — O(n²) hotspot.** `plan_copy` called `_entries_for_tag(walk.entries, tag)` once per tag, each a full linear scan of the flat walk = O(tags × entries); at 349k tags that dominated. `walk_tags` now records `WalkSummary.entries_by_tag` (index-parallel sublists, populated as it walks) and `plan_copy` zips `tags`↔groups directly. `_entries_for_tag` deleted. Semantics unchanged for non-overlapping tags; cleaner for overlapping ones (no cross-prefix contamination).

**Declined this pass (Matthew):** collapsing redundant tagged descendants (overlapping-tag dedup) — copying a fully-tagged tree still emits one item per already-included descendant; parked as a separate semantics call. Also trimmed a pre-existing unused `Resolution` import in `app.py`.

Files: `wtree/ops/base.py` (`PLAN_CHUNK_SIZE`, `ScanCancelled`, `WalkSummary.entries_by_tag`), `copy.py`, `move.py`, `conflicts.py`, `ops/__init__.py` (export `ScanCancelled`), `app.py` (gate wiring + generic `_run_scan_with_dialog` + import trim), `design.md`. 8 new tests in `tests/test_plan_cancellable.py`. **684 → 692/692 green** (run in quarters). Clean mount session: git-archive baseline (HEAD `645aae9`) + atomic-md5 pushback.

**Note:** this makes the 349k copy *cancellable and progress-reporting*, and removes the quadratic — but it can still take a while at that scale (it's genuine I/O over a third of a million entries). If that proves annoying in daily use, the parked descendant-collapse is the next lever.

## 2026-06-07 — ProgressScreen safe_dismiss guard (flagged follow-up)

Closed the 2026-06-05 follow-up: `ProgressScreen` had the same three-racer double-dismiss shape that crashed `ScanScreen` (Esc path in `action_cancel_or_dismiss`, `m` minimize, and `_refresh`'s plan-moved-on auto-dismiss — any two firing in the same frame and the loser pops the base `_default` screen → `ScreenStackError`). Mirrored the fix: `_dismissing` flag + `safe_dismiss()` (idempotency + `self in self.app.screen_stack` membership + try/except), all three callers routed through it. `Ctrl+P` resume unaffected — the gate always constructs a fresh instance. First-Esc-cancels branch untouched (guard only wraps the actual pop).

Drive-by: deduped the doubled "Push immediately if the plan trips the size or item-count" docstring line in `app.py` `_maybe_push_progress_dialog` — real in HEAD (an old mount glitch that got committed), not a stale read.

8 new tests in `tests/test_progress_safe_dismiss.py`: static surface (method exists; source-level pin that the only bare `self.dismiss(None)` in the module lives inside `safe_dismiss`), pilot races (double safe_dismiss; Esc+timer; minimize+timer — each asserts exactly one pop and base screen intact; minimize race also pins queue.request_cancel not called), first-Esc-cancels regression, and 2 pins for `ScanScreen.safe_dismiss` itself (shipped untested at `645aae9`). **692 → 700/700 green** (quarters; lone `test_flash_clears_after_timeout` flake green in isolation per usual).

Clean session per mount rules: git-archive baseline (HEAD `312a84a`) → anchor-asserted edits + ast.parse in sandbox → suite green → atomic push-back + md5 verify → slice re-run against mount files. NOT committed — Matthew commits Windows-side.

## 2026-06-07 (cont.) — Pyflakes sweep + lint gate

Cleared the Code-health backlog item. Package: `sources/base.py` unused `field`, `properties.py` unused `field`+`Sequence`, `execute.py` `except FileExistsError as exc:` -> bare `except FileExistsError:`. Tests: 41 nits across 20 files — unused imports (autoflake --remove-all-unused-imports, diff-reviewed line by line) + 3 unused locals fixed by hand (`test_log_new_source` dropped the `tree =` binding but kept the `query_one` existence check; `test_midplan_cancel` dropped the `result =` binding, kept the awaited call; `test_tree_arrows` function-level `TreeNode` import removed by autoflake). **`wtree/error_handler.py` deliberately untouched** — vendored verbatim (provenance header); its 4 typing nits belong upstream in Python ErrorHandler.

New `tests/test_lint.py`: pyflakes gate over `wtree/` + `tests/` excluding the vendored file, `pytest.importorskip("pyflakes")` so the suite gains no hard dependency. Nits can't silently accrue between sessions now.

**700 → 701/701 green** (quarters, zero flakes this round). Same clean-mount protocol; push-back md5-verified. NOT committed — Matthew commits Windows-side.

## 2026-06-07 (cont.) — Picker drive / share switching (phase-2 item BUILT)

Design pass first (4 AskUserQuestion forks, all recommendations accepted): **(1)** Ctrl+D **chooser modal** over a drives pseudo-root (synthetic top level would special-case `reveal_path`/anchor logic) and over XTree bare drive-letter keys (letters reserved for the parked type-to-filter; no POSIX analogue); **(2)** POSIX "drives" = `/`, `~`, + existing one-level children of `/mnt`, `/media/$USER`, `/run/media/$USER`, `/Volumes` (full mount-table parsing rejected: noisy, distro-dependent); **(3)** picker-only this pass — app-level Ctrl+D (browsable cousin of `L`) split out as a follow-up; **(4)** per-location cursor memory, session-lifetime. design.md: rooting paragraph updated, new "Drive / share switching" paragraph, decision-log row.

**Key design wrinkle:** memory + identity key on the picker's **root path**, NOT `drive_anchor()` — on POSIX every path's splitdrive anchor is `/`, which would collapse `~` and `/mnt/usb` into one key.

Code: new **`wtree/_drives.py`** platform shim (sibling of `_owner.py`) — `list_drive_anchors(current, *, windows=None, media_bases=..., home=...)`; Windows = `os.listdrives()` (3.12+) → ctypes `GetLogicalDrives()` bitmask (`_bitmask_to_anchors` pure + testable) → exists-probe, no pywin32; POSIX = `_posix_anchors` with parameterised bases/home for tmp-tree testing; order-preserving dedupe; current root prepended when missing. **`dir_picker.py`**: `_PickerTree.re_root` (mirrors TreePane's — bare populate, programmatic like initial root); `DirPickerScreen` gains ctrl+d binding, `_per_root_cursor` dict, `@work action_switch_drive` (push_screen_wait chooser → record outgoing cursor → re_root → reveal remembered), footer hint "Ctrl+D drives"; new **`DriveChooserScreen(ModalScreen[str | None])`** (KindChooser-style minimal: Static list, Up/Down/Enter/Esc, initial cursor on current).

17 new tests in `tests/test_drive_switching.py` (bitmask units, POSIX layout vs tmp tree incl. missing-base skip + file filtering + home=/ dedupe, current-inclusion/dedupe, windows= shape via monkeypatched listdrives, chooser cursor/Enter/Esc, picker Ctrl+D binding, switch-reroots-and-remembers round-trip, same-root no-op, Esc-keeps-state, footer hint). One test-only fix en route: `app.source` → `app._source` (matched test_dir_picker.py convention). **701 → 718/718 green** (eighths — two 13-file batches together now exceed the 45s bash timeout). Same clean-mount protocol. NOT committed — Matthew commits Windows-side.
