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

Pane focus determines arrow-key behavior. In the tree pane, ← collapses / → expands the node under cursor. In the contents pane, ← goes to parent dir / → enters the highlighted dir.

Modal dialogs (typing a destination path, typing into a search prompt) re-bind letter keys to text input. Same principle as `/` putting you in incremental-search mode: while a dialog or search is active, letters are text, not commands. The status line shows the active mode.

#### Selection rule

Commands operate on the tagged set if it is non-empty; otherwise on the entry under the cursor. **Rename is the exception**: it is a single-entry operation in v0. If `R` is pressed while the tagged set is non-empty, the operation is rejected with a status-line nudge ("rename works on one entry; clear tags first"). Batch rename is parking-lot material for post-v0.

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

## Open questions

No architectural blockers for v0. Implementation can begin.
