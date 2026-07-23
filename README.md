# tmux-tui

`tmux-tui` is a friendly, keyboard-and-mouse terminal manager for tmux. It is
the **Tmux Manager** shipped by Kilix 95, Pleb, and Plebian-OS.

The UI does not implement a second tmux control layer. Every read and mutation
goes through the bundled [`tmux-cli`](https://github.com/itsmygithubacct/tmux-cli)
submodule and its stable `tb --json` contract.

## Features

- session list with window, client, and idle indicators
- per-session pane list with command, working directory, and active state
- live scrollback preview of the selected pane, fetched with the state snapshot
- create, rename, kill, filter, and send-command actions
- attach to an exact pane; detaching returns to the manager
- stable pane selection across tmux window/pane reordering
- filtering across session names, window names, commands, and working directories
- keyboard navigation and mouse selection
- cell-aware Unicode rendering and a responsive colored curses layout
- no Python package dependencies

## Run

Requirements are Python 3.10+, tmux, and a terminal:

```bash
git clone --recursive https://github.com/itsmygithubacct/tmux-tui.git
cd tmux-tui
./tmux_tui.py
```

If the submodule is not initialized, a sibling `../tmux-cli/tb.py` or an
installed `tb` command is used as a developer fallback. Select another
entrypoint explicitly with `--tb /path/to/tb.py`. An explicit `--tb` or
`TMUX_CLI` value must resolve to a runnable entrypoint; a typo fails clearly
instead of silently selecting a different backend.

With a current tmux-cli, each steady-state refresh combines session state,
stable pane IDs, and the selected-pane preview into one process invocation.
The manager automatically falls back to the original snapshot/capture sequence
when used with an older tmux-cli.

## Keys

| Key | Action |
|---|---|
| `↑`/`↓`, `j`/`k` | move through sessions or panes |
| `←`/`→`, `Tab` | switch between the session and pane lists |
| `Enter`, `o` | attach to the selected pane |
| `n` | create a session |
| `e` | rename a session |
| `d` | kill a session after confirmation |
| `s` | type a command and Enter into the selected pane |
| `/` | filter sessions and pane details |
| `p` | toggle live preview |
| `r` | refresh now |
| `?` | show in-app help |
| `q` | quit |

## Install

```bash
make install PREFIX="$HOME/.local"
```

This atomically links `tmux-tui` into `$PREFIX/bin`. It refuses to overwrite a
regular file, an unmanaged symlink, or a symlinked `bin` directory. To publish
tmux-cli's conventional short command too:

```bash
make install-tb PREFIX="$HOME/.local"
```

Pleb and Plebian-OS install both commands. Standalone Kilix downloads the
pinned source closure on first `kilix tmux` launch; `kilix tmux --with-tb`
also installs the `tb` alias.

## Test

```bash
make check
```

`make check` runs the unit and installer-safety tests, compiles the entrypoint,
and verifies that the source and `VERSION` file agree.

## License

[MIT](LICENSE)
