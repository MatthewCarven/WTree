# WTree

A keyboard-driven file manager for the terminal, in the spirit of XTree Gold,
ytree, and Midnight Commander. Cross-platform (Windows, Linux, macOS).

> **Status:** v0 in progress. Two-pane layout, navigation, tagging, and the
> core file operations (Copy, Move, Delete, Rename) all work. View, Edit,
> Make-new, Menu, and incremental search are next on the list. See
> [`todo.md`](todo.md) for the current roadmap and [`design.md`](design.md)
> for the architecture.

## The XTree idea

The thing that makes WTree feel different from a plain dual-pane file
manager is the **tagged set**: tag entries anywhere in the tree, navigate
freely while the tags persist, then apply an operation to everything you've
tagged. Tags are `(source, path)` pairs, so a single tagged set can span
drives, UNC paths, and (post-v0) archive contents or remote sources.

When no tags are set, operations apply to the entry under the cursor. Rename
is the one exception — it's single-entry only and rejects when the tagged
set is non-empty (clear with `Ctrl+U`).

## Install (development)

```bash
git clone <repo>
cd wtree
pip install -e .[dev]
```

Python 3.10 or newer is required.

## Run

```bash
wtree
```

The app launches in the current working directory. Press `q` or `F10` to
quit, `?` or `F1` for in-app help (placeholder for now).

## Keymap

WTree uses XTree-style single-letter commands as the primary bindings, with
Midnight Commander F-keys as equivalent aliases. Same operation, two keys.

| Action | Letter | F-key |
| --- | --- | --- |
| Quit | `Q` | `F10` |
| Switch pane focus | `Tab` | |
| Navigate | `↑` `↓` `PgUp` `PgDn` `Home` `End` | |
| Enter directory | `Enter` or `→` (contents pane) | |
| Parent directory | `Backspace` or `←` (contents pane) | |
| Tag / untag toggle | `Space` or `T` | |
| Untag all (clear set) | `Ctrl+U` | |
| Copy | `C` | `F5` |
| Move | `M` | `F6` |
| Delete | `D` or `Del` | `F8` |
| Rename | `R` | `F2` |

The bottom-of-screen MC-style key bar shows F-key labels at all times;
keys that aren't wired yet appear dimmed.

The full canonical keymap (including bindings not yet implemented) lives
in [`design.md`](design.md) § Keymap.

## Testing

```bash
pytest
```

Current suite: 454 tests, covering planners, executor adapters
(including the chunked-copy progress path), queue semantics,
ProgressScreen helpers, and end-to-end pilot runs.

## Documentation

- [`design.md`](design.md) — the canonical, living design document.
  Architecture, full keymap, cross-platform modifier strategy, decision log.
- [`worklog.md`](worklog.md) — chronological session notes.
- [`todo.md`](todo.md) — concrete next steps and post-v0 parking lot.

## Terminal notes

- **Windows:** point at Windows Terminal or WezTerm. `cmd.exe` / legacy
  conhost work but look rougher.
- **macOS:** to use the `Alt`-letter menu accelerators, enable
  *"Use Option as Meta key"* in your terminal's preferences
  (iTerm2: Profiles → Keys; Terminal.app: Profiles → Keyboard). Without it,
  Alt-bindings won't reach the app — but every action also has a non-Alt
  binding, so the app is fully usable either way.
- **Linux:** any modern terminal (Alacritty, Kitty, WezTerm, GNOME Terminal,
  Konsole) works out of the box.

## Tuning

`wtree/ops/queue.py` exposes two module-level constants that govern
copy throughput and the live progress readouts:

- `COPY_CHUNK_SIZE` (default `256 KB`) — bytes per read/write chunk
  during copies. Aligning to your storage's cluster or sector
  geometry helps: roughly `4 KB` for a physical sector, `64 KB` for
  the Python `shutil` default, `1 MB+` for NTFS large-volume
  clusters or SMB/NFS shares, `4 MB+` for 10 GbE → NVMe sequential
  bulk transfers. **Note:** the per-chunk constant is tunable, but
  copy time (and the progress dialog's update rate) is only as
  granular as how often the per-chunk callback is called — bigger
  chunks mean faster bulk throughput on fast hardware but coarser
  progress steps.
- `PROGRESS_REDRAW_HZ` (default `10`) — caps progress-dialog
  repaints per second, coalescing the per-chunk callbacks so the
  UI doesn't thrash on fast transfers.

No environment-variable override yet; edit the constants and
reinstall. An `WTREE_COPY_CHUNK` env-var override is on the
backlog.

## License

MIT.
