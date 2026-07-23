# tmux-tui

`tmux-tui` is a friendly, keyboard-and-mouse terminal manager for tmux. It is
the **Tmux Manager** shipped by Kilix 95, Pleb, and Plebian-OS.

The UI does not implement a second tmux control layer. Every read and mutation
goes through the bundled [`tmux-cli`](https://github.com/itsmygithubacct/tmux-cli)
submodule and its stable `tb --json` contract.

## Features

- session list with window, client, and idle indicators
- per-session pane list with command, working directory, and active state
- live scrollback preview of the selected pane
- create, rename, kill, filter, and send-command actions
- attach to an exact pane; detaching returns to the manager
- keyboard navigation and mouse selection
- responsive, colored curses layout with no Python package dependencies

## Run

Requirements are Python 3.10+, tmux, and a terminal:

```bash
git clone --recursive https://github.com/itsmygithubacct/tmux-tui.git
cd tmux-tui
./tmux_tui.py
```

If the submodule is not initialized, a sibling `../tmux-cli/tb.py` or an
installed `tb` command is used as a developer fallback. Select another
entrypoint explicitly with `--tb /path/to/tb.py`.

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
| `/` | filter sessions |
| `p` | toggle live preview |
| `r` | refresh now |
| `?` | show in-app help |
| `q` | quit |

## Install

```bash
make install PREFIX="$HOME/.local"
```

This links `tmux-tui` into `$PREFIX/bin`. To publish tmux-cli's conventional
short command too:

```bash
make install-tb PREFIX="$HOME/.local"
```

Pleb and Plebian-OS install both commands. Standalone Kilix downloads the
pinned source closure on first `kilix tmux` launch; `kilix tmux --with-tb`
also installs the `tb` alias.

## Test

```bash
make test
```

## License

[MIT](LICENSE)
