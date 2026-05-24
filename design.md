# WTree — Design Document (v0)

A keyboard-driven file manager for the terminal, in the spirit of XTree Gold, ytree, and Midnight Commander. Cross-platform from day one. Optimized for fast tree navigation, persistent tagging across directories, and clean composition with external tools.

## Status

This is a **living design document**. Open questions are marked `OPEN` and need to be resolved before implementation begins; confirmed decisions are stated declaratively. Update as the design evolves.

## Lineage

WTree borrows from three traditions:

- **XTree / XTree Gold / ytree** — single hierarchy view, persistent tagged set across directories, the "logged" disk metaphor.
- **Norton / Midnight Commander / Far** — dual-pane TUI layout, function-key conventions, modal dialogs.
- **Modern Unix (ranger, nnn, lf, yazi)** — composition with external tools, sane defaults, lazy I/O.

The goal: XTree's feel and tagging power, MC's visual layout, and a modern Unix selection-as-output escape hatch underneath.

## Core architecture

### EntrySource abstraction

The disk is read through a pluggable `EntrySource` interface. Anything that can yield directory entries for a path qualifies. Implementations in v0:

- **NativeSource** — direct OS calls (`readdir`, `FindFirstFile`, `std::fs::read_dir`, equivalent).
- **ShellSource** — shells out to `dir /b /s /a` on Windows or `find … -printf` on Unix and parses output. Used as a fallback when native readdir is unreliable (bad sectors, weird drivers, frozen network mounts).
- **MockSource** — scripted contents and scripted errors, for tests.

Future (post-v0): **ArchiveSource** (`.zip`, `.tar`, `.tar.gz` as virtual directories), **RemoteSource** (SSH/SFTP). The interface shape is designed to admit these without core changes.

The rest of WTree never knows what kind of source it is looking at.

### Entry shape

Each entry contains at minimum: name, kind (file/dir/symlink/other), size, mtime. Optional fields (permissions, owner, link target) are nullable. A source advertises its *capability* — what fields it can supply — so the UI can render "permissions unknown" gracefully when a source can't provide them.

Dates are stored canonically as **`YYYY-MM-DD HH:MM:SS`** (ISO 8601 with seconds resolution). All sources convert their native date formats into this on the way in; nothing locale-flavored is ever passed to the UI. Display formatting (e.g. relative — "2h ago") is a presentation-layer config knob, applied only at render time.

### Errors as data

A scan yields `Result<Entry, ScanError>` per item. Permission-denied or I/O failure on one subdirectory does **not** abort the scan — the broken node appears in the tree as a damaged entry (visually distinct, navigable past). This is the correct behavior on healthy disks too; it's also what makes the unreliable-disk story work end-to-end.

### Traversal strategy

The `EntrySource` interface is **lazy per directory**: you ask for a path, you get its immediate children. "Log the whole tree" semantics live one layer up as composable strategies:

- **LogAll** — eager full-tree scan (XTree mode). Best for SSDs and small trees.
- **LogOnDemand** — scan as the user navigates. Default.
- **LogDepth(n)** — eager scan to depth `n`, lazy below.
- **LogPersist** — scan once, persist to disk, restore on next launch.

An unreliable-disk source can refuse `LogAll` if it knows the device is fragile.

### Tagged set

The tagged set is the central object — files tagged anywhere in the tree persist as the user navigates. Single keystroke to tag/untag. Operations (copy, move, delete, etc.) apply to the tagged set when one exists; otherwise to the entry under the cursor.

This is what makes XTree's model powerful and what differentiates WTree from a plain dual-pane file manager.

## User interface

### Layout

**Decided: Explorer-style coupled panes.** Left pane is a navigable folder tree; right pane shows the contents of whichever folder is selected in the tree. The panes are coupled — two views of one selection. The MC source-and-destination ergonomic is replaced by the tagged set (below), which makes cross-directory operations first-class regardless of where the panes are pointing.

### Tagged set scope

The tagged set is **per-session, source-agnostic.** A tag is a `(source_id, absolute_path)` tuple, not a bare path. The set can simultaneously hold entries from:

- different drive letters on Windows (`C:\foo`, `D:\bar`)
- UNC / network paths (`\\server\share\baz`) — these work for free via NativeSource on Windows because Win32 handles UNC transparently
- different mount points on Unix
- post-v0 sources (archive contents, remote/SFTP) once those land

**Operation semantics vary by source pairing.** Cross-drive move is copy-then-delete, not rename. Cross-source operations (e.g. copy from a future SFTP source to local) need explicit transfer logic. Every operation asks the relevant sources "can you do X" before promising the user it will happen instantly; the UI shows a progress dialog for non-trivial transfers.

**Enumeration of network shares** (browsing what exists on a network, as opposed to typing `\\server\share` directly) is parking-lot material — see below. v0 supports UNC paths you can address; it does not browse for servers or shares.

### Editing files

Shell out to the user's editor. Resolution order:

1. `$VISUAL`
2. `$EDITOR`
3. Platform default — `notepad` on Windows, `nano` then `vi` on Unix-like.

An inline editor is **explicitly out of scope for v0** — see Parking lot.

### Modular yield mode

A flag (working name `--pick`) makes WTree behave as a selector: launch, navigate, tag, press a confirm key, WTree prints the tagged set to stdout and exits with status 0 (or non-zero if cancelled). This lets it compose with shell pipelines and external scripts:

```
wtree --pick | xargs cp -t /backup
```

Common operations (copy, cut, paste, move, delete, rename) remain single-key and internal for the snappy keystroke feel. `--pick` is the escape hatch for everything else.

### Keymap

**Decided: XTree single-letter commands as primary, MC function keys as aliases.** Same operation, two bindings. Vim modal explicitly rejected. Alt-modifier accelerators added as an optional layer for Windows-style menu navigation; Alt is never required for any action.

#### Canonical bindings

| Operation | Letter | F-key | Notes |
| --- | --- | --- | --- |
| Cursor up/down | ↑ ↓ | | |
| Page up/down | PgUp PgDn | | |
| Top/bottom of list | Home End | | |
| Switch pane focus | Tab | | Tree ↔ Contents |
| Open / enter dir | Enter | | |
| Go to parent dir | Backspace | | |
| Go to specific path | `G` | | Opens prompt |
| Tag / untag (toggle) | Space, `T` | | |
| Untag entry | `U` | | |
| Tag by pattern | `+` | | Opens glob prompt |
| Untag by pattern | `-` | | Opens glob prompt |
| Tag all in current dir | `Ctrl+A` | | |
| Untag all (clear set) | `Ctrl+U` | | |
| Copy | `C` | F5 | |
| Move | `M` | F6 | |
| Rename | `R` | F2 | Single entry only; see Selection rule |
| Delete | `D`, Del | F8 | |
| View | `V` | F3 | Built-in pager |
| Edit | `E` | F4 | Shells out to `$EDITOR` |
| Make new (dir or file) | `N` | F7 | Sub-prompt asks dir or file |
| Toggle hidden | `H` | | |
| Sort / order menu | `O` | | |
| Properties | `Ctrl+I` | | |
| Refresh source | `Ctrl+R` | | |
| Incremental search | `/` | | Local to current pane |
| Find across tree | `Ctrl+F` | | |
| Next match | `Ctrl+G` | | |
| Log new source | `L` | | Drive/path prompt |
| Shell prompt | `!` | | `$TAGGED` env available |
| Open menu bar | | F9 | MC convention; arrows navigate |
| Menu accelerator | `Alt+letter` | | Optional Windows-style direct jump |
| Confirm in `--pick` mode | `=` | | |
| Cancel dialog | Esc | | |
| Quit | `Q` | F10 | |
| Help | `?` | F1 | |

An MC-style key bar across the bottom of the screen displays the F-key bindings as a permanent visual cheat sheet.

#### Modality

Pane focus determines arrow-key behavior. In the tree pane, ← collapses / → expands the node under cursor — with one exception: **← on the root node ascends**, re-rooting the tree at the parent directory ("log the directory above"). At the filesystem root the action no-ops with a status-line nudge. In the contents pane, ← goes to parent dir / → enters the highlighted dir.

Modal dialogs (typing a destination path, typing into a search prompt) re-bind letter keys to text input. Same principle as `/` putting you in incremental-search mode: while a dialog or search is active, letters are text, not commands. The status line shows the active mode.

**Incremental search (`/`)** is modeless inline, not modal. Pressing `/` swaps the StatusLine row for a SearchBar in the same slot. While the SearchBar holds focus: printable characters extend the query and the focused pane's cursor jumps to the first match (substring, case-insensitive) at or after the original cursor position; Backspace shrinks the query; Down or `Ctrl+G` step to the next match (wrap); Up steps to the previous match. Enter commits — the cursor stays at the current match, search exits. Esc cancels — the cursor restores to the pre-search position, search exits. Empty query is a no-op (cursor doesn't move). No-match is indicated visually (the bar text turns red, shows `(no match)`). Search is local to whichever pane had focus at `/`-press time; the other pane is unaffected.

#### Selection rule

Commands operate on the tagged set if it is non-empty; otherwise on the entry under the cursor. **Rename is the exception**: it is a single-entry operation in v0. If `R` is pressed while the tagged set is non-empty, the operation is rejected with a status-line nudge ("rename works on one entry; clear tags first"). Batch rename is parking-lot material for post-v0.

**Make-new is also exceptional**: it doesn't consume the tagged set or the cursor entry. The parent directory is `ContentsPane.current_path` — whatever the user is *looking at*. Tagged set is silently ignored (mirrors View / Edit). The sub-prompt is two screens: a kind chooser (D/F/Esc) then a name PromptDialog; the typed name may contain forward-slash separators, in which case intermediate directories are created on apply (lenient mode).

#### Cross-platform modifier strategy

The choice of which modifier keys WTree binds is constrained by what TUI applications actually *receive* on each platform. Three layers diverge here — physical key, OS keycode, and what the terminal forwards to the app — and the design honors the terminal layer.

**Win / Super / Cmd is never bound.** This key occupies the same physical position across PC and Mac keyboards but does not reach terminal applications on any major platform. Windows reserves it for the shell (`Win+E`, `Win+R`); macOS routes `Cmd+anything` to the active app's menu bar before the terminal sees it; Linux window managers typically grab Super for tiling and workspace operations. A design that depends on this key is dead on arrival in a terminal.

**Ctrl is the bedrock.** Reliable across every terminal on every platform; used for system-level and meta operations (`Ctrl+A` tag-all, `Ctrl+U` untag-all, `Ctrl+R` refresh, `Ctrl+F` find-across-tree, `Ctrl+G` next match, `Ctrl+I` properties).

**Alt is an optional accelerator layer, never a requirement.** It reaches the terminal as `meta+letter` (Textual's term) on Windows and Linux without configuration. macOS users must enable "Use Option as Meta key" in their terminal preferences (iTerm2: Profiles → Keys; Terminal.app: Profiles → Keyboard) — this is called out in the README. AltGr on European keyboards is a typed-character key, not a modifier; never use right-Alt-only bindings.

Every Alt-based action has at least one non-Alt path: F9 opens the menu bar (MC convention) and arrow keys navigate it; single-letter and F-key commands cover every primary operation. A user who never presses Alt loses nothing.

## Language and toolkit

**Decided: Python with [Textual](https://textual.textualize.io/).**

Target platforms: **Windows, Linux, macOS** — all three.

Why this works:

- **Async-native.** Textual's event loop is `asyncio`. `EntrySource.scan(path)` is an `async` generator; long scans yield to the UI naturally without threading.
- **Cross-platform.** Textual renders cleanly on Windows Terminal, WezTerm, iTerm2, and modern Linux terminals. Legacy `cmd.exe` / old conhost work but look rougher — Windows users will be pointed at Windows Terminal in the README.
- **Standard-library leverage.** `pathlib`, `shutil`, `subprocess`, `asyncio.create_subprocess_exec` cover most needs. ShellSource is a near-trivial wrapper.

**Distribution strategy** — decided later. Candidates: `pip install wtree` for users with Python; PyInstaller or Nuitka for standalone binaries. Not a v0 blocker.

**Considered and rejected** — Rust (ratatui), Go (bubbletea), C (ncurses). All viable; Python wins on dev velocity for this project's scope and on author preference.

## Parking lot — post-v0

- **Inline editor.** Big scope. MC ships mcedit; vifm wisely shells out. Revisit only if shelling out to `$EDITOR` proves friction-laden in practice.
- **Archive browsing.** `ArchiveSource` — `.zip`, `.tar`, `.tar.gz` as virtual directories.
- **Remote sources.** SSH/SFTP, possibly WebDAV.
- **Network discovery.** SMB / Bonjour / mDNS browsing — enumerate available servers and shares rather than requiring the user to type `\\server\share` from memory.
- **Live filesystem watching.** `inotify` / `FSEvents` / `ReadDirectoryChangesW` for auto-refresh.
- **Bookmarks and history.** Persistent named locations, MRU directory list, jump-to-recent.
- **Batch rename.** Pattern-based rename across the tagged set.
- **Themes.** Color schemes, including a faithful XTree green-on-black.
- **Mouse support.** Decision pre-committed for when this lands: click = navigate only (move cursor / select row / expand-collapse tree node); wheel scrolls both panes; F-key bar and MenuBar entries become clickable proxies for their key bindings; tagging stays keyboard-only (Space / T / + / - / Ctrl+A / Ctrl+U); Textual's `ModalScreen` scopes events per screen so existing modals (PromptDialog, MenuScreen, kind-chooser) intercept clicks for free without extra guards. **Drag-to-range-tag and Ctrl/Shift-click-to-tag are rejected on principle, not deferred by default.** The principle: *mouse adds discoverability, not new semantics.* Tagging behaviour must not depend on input device because the tagged set is persistent across directory navigation, source-agnostic (multi-drive, post-v0 multi-source), and decoupled from the cursor — Shift+click range-extend has no clean meaning when the anchor row lives in a different directory or on a different drive than the current view; scoping it to current-view-only creates a confusing two-tier model (Space tags anywhere, Shift+click only tags here). Drag-tag adds a contiguous-only gesture whose keyboard equivalents (`Ctrl+A` for the whole dir, `Space ↓ Space ↓ …` for a run, `+ glob` for a pattern) are already atomic and viewport-independent. Both gestures also import a stack of UX traps — auto-scroll-on-drag, drag-out-of-widget, click-jitter threshold, cross-pane drag boundaries, modifier-state-changes-mid-drag — that bloat scope for zero new capability. The TUI-context framing matters too: a mouse pointer in a text-only GUI is inherently a tacked-on accelerator, not a primary modality, and the design should reflect that. README will note: requires a modern terminal (Windows Terminal, iTerm2, WezTerm, recent Linux terms); tmux users need `set -g mouse on`; hold **Shift** to bypass app mouse reporting and use the terminal's native click-to-select-text for copy.

## Decision log

| Date | Decision |
| --- | --- |
| 2026-05-19 | EntrySource abstraction: NativeSource + ShellSource + MockSource for v0 |
| 2026-05-19 | Dates stored canonically as `YYYY-MM-DD HH:MM:SS` |
| 2026-05-19 | Errors-as-data: damaged nodes navigable, scans never abort |
| 2026-05-19 | Editing files: shell out to `$VISUAL` → `$EDITOR` → platform default |
| 2026-05-19 | Layout: Explorer-style coupled panes (tree left, contents right) |
| 2026-05-19 | Tagged set is per-session, source-agnostic, holds `(source, path)` tuples |
| 2026-05-19 | Keymap philosophy: XTree single-letter primary, MC F-keys as aliases; vim rejected |
| 2026-05-19 | Language: Python; toolkit: Textual |
| 2026-05-19 | Full canonical keymap table finalised |
| 2026-05-19 | Make-new (dir or file) bound to `N` (with dir/file sub-prompt) and F7 |
| 2026-05-19 | Rename rejects when tagged set non-empty; batch rename deferred to post-v0 |
| 2026-05-19 | Menu bar opens on F9; Alt+letter accelerators are optional, never required |
| 2026-05-19 | Win/Super/Cmd never bound (unavailable to terminal apps); Ctrl is bedrock |
| 2026-05-22 | Edit (E / F4) is single-entry on cursor, not Selection-rule (mirrors View); multi-file editor semantics vary too much for a v0 contract |
| 2026-05-22 | Editor helpers live in top-level `wtree/editor.py`, not `ops/` — Edit is a UI shell-out, not a Plan-producing operation |
| 2026-05-22 | Make-new sub-prompt: two-step (chooser modal D/F/Esc → name PromptDialog). One-step combined modal and trailing-slash convention both rejected — each step is unambiguous and reuses PromptDialog unchanged |
| 2026-05-22 | Make-new is lenient on path separators: `foo/bar/baz` creates intermediate directories on apply (`os.makedirs(parent, exist_ok=True)` + exclusive-create on the leaf). Differs from Rename (basename-only) because Make-new starts from "no existing entry" |
| 2026-05-22 | Make-new parent is `ContentsPane.current_path` (the pane's displayed dir); tagged set and cursor entry are silently ignored. Make-new is a "create here" op, not Selection-rule |
| 2026-05-22 | Left-on-root in the tree pane ascends and re-roots at the parent dir (XTree "widen the logged window" idiom). Left on any non-root node keeps Textual's default collapse-or-cursor-to-parent behaviour. At the filesystem root the action no-ops with a status nudge. Tags survive (absolute paths). After ascend the tree cursor lands on the old-root row, contents pane stays on the old root's contents (working context stable); user presses Up to see the new parent's contents |
| 2026-05-22 | Incremental search (`/`) is **substring, case-insensitive**, matched against the entry basename in the contents pane and the visible node label in the tree pane. Prefix-only (XTree-strict) and regex (Vim-style) both rejected for v0 — substring is the modern default and easier to predict. Regex / prefix toggles can layer on later behind explicit syntax (e.g. `/^foo` for prefix, `/\foo` for regex) |
| 2026-05-22 | Search is **local to the focused pane**. Tree-pane scope is **visible nodes only** — collapsed subtrees are NOT walked. Auto-expand-to-find is rejected for v0: would require eager subtree scans during typing and conflicts with sources that refuse `LogAll`. Find-across-tree is what `Ctrl+F` is reserved for (post-v0) |
| 2026-05-22 | Search UI replaces the StatusLine inline while active — same row, no layout shift. SearchBar shows `/<query> (idx/total)` or `/<query> (no match)` in red. Modal PromptDialog explicitly rejected: would break the modeless "type and the cursor moves" feel that defines incremental search |
| 2026-05-22 | Two-tier feedback: **`StatusLine.flash(msg, timeout=3.0)`** for user-immediate nudges ("X rejected", "Logged: NEW", "X cancelled") — the user just pressed a key and is looking right now; **`App.notify()`** for queue-completion and other async messages — may fire when the user's looked away, so the toast layer queues them visibly. Default 3 second timeout; replaces (cancels) any active flash. Flash holds through `refresh_from()` so cursor moves don't clobber it before the timer fires |
| 2026-05-22 | Pane auto-refresh after op completion — `_on_plan_complete` fires `asyncio.create_task(self._refresh_panes_after_op())` which re-shows the contents pane's `current_path`. Fires unconditionally (even on partial-success) since some items may have touched disk. Tree-pane auto-refresh parked — `_loaded` memo invalidation is non-trivial |
| 2026-05-22 | F9 menu bar: always-visible passive `MenuBar` row docked at top; F9 pushes interactive `MenuScreen` modal. MENUS module-global (in `menu_bar.py`) is the single source of truth for both surfaces. Two menus for v0 (File, Commands); only implemented items shown — unimplemented keymap operations omitted until they land. First-letter accelerators highlighted (underline); inside an open menu, letter activates the item directly. Left/Right rotates top-level menus with wrap; Up/Down navigates dropdown (skipping separators). Enter dismisses with the item's `action` string; the app dispatches via `getattr(self, f"action_{name}")()`. Help menu deferred — no About modal yet |
| 2026-05-22 | Mouse support deferred to post-v0 (see Parking lot), but the **semantics are pre-decided** so the future patch is purely a wiring pass: click = navigate only (move cursor / select / tree expand-collapse); wheel scrolls; F-key bar and MenuBar entries become clickable proxies for their key bindings; tagging stays keyboard-only. Drag-tag and Ctrl/Shift-click-tag rejected — keyboard remains the single tagging surface to preserve XTree-style "tag is deliberate" feel. Modals intercept clicks for free via Textual's per-`ModalScreen` event scoping; no guards needed |
| 2026-05-22 | Mouse tagging gestures (drag-to-range-tag, Ctrl/Shift+click-to-tag) rejected on principle, not deferred. Principle: **mouse adds discoverability, not new semantics** — tagging behaviour must not depend on input device because the tagged set is persistent, source-agnostic, and decoupled from the cursor. Shift+click range-extend has no clean cross-directory meaning; drag-tag adds a contiguous-only gesture whose keyboard equivalents (`Ctrl+A`, `Space + arrows`, `+ glob`) are already atomic and viewport-independent. Mouse in a TUI is an accelerator, not a primary control surface — design should reflect that |
| 2026-05-22 | Tagging polish pass landed: `Ctrl+A` tag-all-in-current-dir, `+` / `-` glob tagging with fnmatch (platform-default casing — case-sensitive on POSIX, case-insensitive on Windows; consistent with `fnmatch.fnmatch` defaults), recursive Space on tree-pane nodes. `TaggedSet` gained `add_many` / `remove_many` returning the actual delta (not the iterable length) so callers flash accurate counts |
| 2026-05-22 | Tagged-row visual style: **bold yellow** applied to every cell of a tagged row, with the `*` marker rendered in the same style. Implementation: each cell stored both as raw string in `ContentsPane._row_cells` and as Rich `Text(value, style="bold yellow")` in the DataTable. `refresh_tag_markers` restyles the whole row (not just the marker column) on bulk mutations. Alternatives rejected: cyan-name-only (too subtle), reverse-video row (clashes with cursor highlight), marker-glyph-only (no row-level signal) |
| 2026-05-22 | Tree-pane Space recursive semantics: toggle by the **directory's own tagged state** — if the dir entry is currently tagged, recursive untag of the whole subtree; otherwise recursive tag. Predictable from the cursor and inverse-able by pressing Space again. Always-additive and "any-descendant-tagged-means-untag" both rejected (the cursor-tagged-state-is-the-signal model is the easiest to reason about). Walk treats symlinks as leaves (no follow — cycle guard); `ScanError` branches are silently skipped per errors-as-data. Walk runs under `@work` so big subtrees don't freeze the UI |
| 2026-05-22 | Tree-pane Space intercepted via `TreePane.on_key` (same pattern as Left-on-root), posting new `TreePane.TagRequested(path)` message. App's `on_tree_pane_tag_requested` handler is `@work`-decorated; the walker `_walk_subtree(root)` is a stack-based async generator on `WTreeApp`. Error-placeholder nodes (`data is None`) don't post the message — they're non-taggable, matching ContentsPane error-row semantics |
| 2026-05-23 | F1 / `?` Help: a single combined `HelpScreen` modal serves both the F1 cheat-sheet role and the menu-bar Help → About item. Read-only `ModalScreen[None]` mirroring `ViewerScreen`'s shape (`VerticalScroll` body, dismiss on Esc / Q). Content is About info (name, version, attribution, one-line description) followed by a categorised keymap reference (Navigation / Tagging / File operations / Search / Application / Selection rule). Keymap content is hand-curated in `_help_content()` rather than derived from `WTreeApp.BINDINGS` — design.md's table groups by concept, the binding table is flat; the readability gain outweighs the duplicated-source cost at v0 scale. Help menu added as the third top-level (after File, Commands) with a single `About` item — a future `Keymap` item can layer in by adding one `MenuItem`. KeyBar `_WIRED` set is now `{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}` — the full F-row is wired, closing the v0 keymap loop. The old `action_noop` placeholder removed |

| 2026-05-23 | Tree-pane tagged-node visual style: bold-yellow applied via :meth:`TreePane.render_label` override (Textual's documented hook for per-node styling) — consults the live `TaggedSet` on every render and stylizes the rendered `Text` on top of the default. Alternatives rejected: (1) rebuild each node's stored label on every mutation — would silently miss lazy-expanded subtrees until they were re-built; (2) a separate `tagged_paths: reactive[set]` watch — extra state sync surface with no upside. With `render_label` the rule is simply "ask the tagged set when painting", so newly-added nodes inherit the correct style on first paint with no per-callsite refresh wiring. The pane gets `refresh_tag_styles()` (= `self.refresh()` behind a descriptive name) for the mutation callsites that need to force a repaint. A new `WTreeApp._refresh_tag_visuals()` helper is the single source of truth for "tags changed; repaint" — every bulk mutation site (`Ctrl+A`, `Ctrl+U`, `+`/`-`, recursive tree-pane Space, after-op tagged-set clear) routes through it. Single-row contents-pane toggles refresh the tree via the existing `TagsChanged` message handler. `TreePane.__init__` now takes a `tagged_set` arg (passed by `WTreeApp.compose()`); tests query the pane via `query_one(TreePane)` so no test-side construction needed to update |
| 2026-05-23 | Tree-pane auto-refresh after ops: planners don't change; `OperationResult` grows a computed `touched_paths: set[str]` property that walks the per-item results and returns the directory paths whose listings changed (parent of dst for COPY / MAKE_NEW, parent of src for DELETE, both parents for MOVE, single parent for RENAME since the planner forbids cross-parent renames). Only SUCCESS items contribute — partial failures don't claim disk state they didn't reach. `TreePane.refresh_paths(paths)` walks every tree node, finds the ones whose `data` is in the set, drops them from `_loaded`, wipes their children, and re-populates the ones that were expanded. Unloaded subtrees are left alone — their lazy-load on first expand will pick up the up-to-date listing without any extra wiring. Tagged-row styling self-heals via the `render_label` override since the rebuilt nodes consult the live tagged set on first paint. Wired in `WTreeApp._refresh_panes_after_op` alongside the existing contents-pane refresh; each pane's refresh is its own try/except so one failure doesn't block the other. Cursor preservation across the refresh is best-effort for v0 (parked as a follow-up) |
| 2026-05-23 | Tree-pane Left / Right arrow bindings: Textual 8.x's `Tree` ships no `left` / `right` bindings, so we own both keys in `TreePane.on_key`. **Right on collapsed expandable** = expand + `await _populate(node)` (lazy-load inline so children land before next paint). **Right on already-expanded** = descend to first child (XTree drill-in; no-op on empty). **Right on non-expandable** (error leaf, `allow_expand=False`) = no-op. **Left on root** = existing `AscendRequested` (preserved). **Left on non-root expanded** = collapse in place (cursor stays). **Left on non-root collapsed** = jump cursor to parent. Net effect: Left twice walks the user out of a subtree (collapses, then ascends one level); Right twice drills two levels deep. Matches Finder column-view and Windows Explorer tree muscle memory. Every branch `event.stop()` + `event.prevent_default()` so a future Textual default doesn't double-fire. Space still owns the recursive-tag gesture (regression test in `test_tree_arrows.py`) |
| 2026-05-23 | Find-across-tree (Ctrl+F) + Next-match (Ctrl+G): distinct from `/` incremental search. `/` searches *visible* rows in the focused pane (modeless inline bar). Ctrl+F walks the *full* tree under `_root_path` via the existing `_walk_subtree` async generator, filters basename substring case-insensitive (same defaults as `/`), caches the result list + query on the app, jumps the tree cursor to the first match via new `TreePane.reveal_path(target)` which expands the chain root→target lazily (auto-populates each ancestor that wasn't loaded yet). Ctrl+G steps through the cached list with wrap; with no cached matches it flashes a nudge rather than no-op'ing silently. Both actions are `@work`-decorated. Empty / whitespace query is treated as cancellation. The root itself is excluded from matches (it's the parent, not a result). Errors mid-walk are silently skipped per `_walk_subtree`'s existing errors-as-data behaviour — a partial match list is better than refusing to search. Results modal (listing all matches with arrow selection) is parked as a follow-up; v0 in-place stepping mirrors XTree's feel. `/`-style next-match-after-commit (Ctrl+G operating on a remembered `/` query) remains a separate parked follow-up |
| 2026-05-23 | `L` log new source: replaces the tree's root with a typed path. v0 is single-source re-root (not side-by-side multi-root tree). Path resolution: `~` expanded, absolute paths used as-is, relative paths resolved against the **current root** (not cwd) — matches the XTree "I'm in a place, switch to a related place" intuition; `../sibling` walks sideways, `./child` drills in. Validation: must exist and must be a directory; flash on error without re-rooting. Tags survive (absolute paths). **Blank-Enter = ascend** (per the 2026-05-22 layered-discoverability note): same as Left-on-root, factored into shared `WTreeApp._do_ascend()` helper. `on_tree_pane_ascend_requested` now delegates to `_do_ascend` so both gestures stay locked. Esc cancels with a flash. New `L` binding in `WTreeApp.BINDINGS`; new "Log new source" item in Commands menu (accelerator `l`); new entry in HelpScreen Navigation section. Multi-root / side-by-side panes parked for post-v0 |
| 2026-05-23 | `Ctrl+R` refresh source: forces a re-scan of both panes against the live source. Contents pane re-runs `show_path(current_path)`; tree pane runs new `refresh_all()` which snapshots expanded paths + cursor backing path, calls `re_root(current_root)` to wipe and repopulate, then walks the snapshot via the new `_walk_to_node` helper to re-expand each previously-expanded path, finally restoring the cursor via `reveal_path`. Paths that no longer exist on disk are silently skipped — the user sees a smaller tree, not an error. `reveal_path` refactored on top of `_walk_to_node` (same behaviour, the cursor-line assignment moved up to the caller) so the walk-down logic is shared with refresh_all. Both panes' refresh wrapped in their own try/except so a failure on one doesn't block the other. New `ctrl+r` binding, new "Refresh source" Commands menu item (accelerator `r`), new entry in HelpScreen Application section. Sorted shallowest-first so a child's expand follows its parent's. Distinct from the auto-refresh after ops (which uses `refresh_paths` targeted at `touched_paths`); Ctrl+R is the user-initiated full re-scan |
## Open questions

v0 is **functionally complete** as of 2026-05-23 (405/405 tests green; today added F1 Help / About, tree-pane tagged-node bold-yellow, tree-pane auto-refresh after ops, tree-pane Left / Right bindings, Ctrl+F find-across-tree + Ctrl+G next-match, `L` log new source, and `Ctrl+R` refresh source). Remaining design work for v0.x and beyond is parked on `todo.md` under the per-era follow-up sections. No architectural blockers.
