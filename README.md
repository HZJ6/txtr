# txtr

A terminal-based txt file reader with bookmarks, search highlighting, and auto-resume. Supports Linux and macOS.

## Features

- **Auto-resume** — quits at any time, reopens at the last reading position
- **Search highlighting** — `/keyword` searches with inline yellow highlighting, `n`/`N` for next/prev match
- **Bookmarks** — `b` to bookmark, `B` to manage (jump, delete)
- **Line spacing** — `+`/`-` to adjust spacing dynamically (0–5 blank rows between lines)
- **Auto word-wrap** — long lines wrap correctly, CJK double-width characters supported
- **Chinese search** — UTF-8 input works for CJK characters
- **Persistent settings** — bookmarks, spacing, and reading position saved per file

## Installation

### pip (recommended)

```bash
pip install txtr
```

### from source

```bash
git clone https://github.com/HZJ6/txtr.git
cd txtr
pip install .
```

### requirements

- Python ≥ 3.9
- `wcwidth` (auto-installed by pip)

## Usage

```bash
txtr novel.txt          # open a file
txtr                    # reopen the last file you read
```

## Key bindings

### Navigation

| Key | Action |
|-----|--------|
| `↑` / `↓` | Scroll one screen row up/down |
| `PgUp` / `PgDn` | Scroll one page up/down |
| `g` / `G` | Jump to first / last line |
| `Home` / `End` | Jump to first / last line |

### Search

| Key | Action |
|-----|--------|
| `/` | Enter search mode |
| `Enter` | Execute search |
| `n` / `N` | Next / previous match |
| `c` | Clear search highlights |
| `Backspace` (empty input) | Cancel search |

Searched keywords are highlighted in **yellow on black**. Works with Chinese and any UTF-8 text.

### Bookmarks

| Key | Action |
|-----|--------|
| `b` | Add / remove bookmark at current line |
| `B` | Open bookmark list |

In the bookmark list:

| Key | Action |
|-----|--------|
| `j` / `k` | Move selection up/down |
| `Enter` | Jump to selected bookmark |
| `d` | Delete selected bookmark |
| `q` | Return to reading |

### Display

| Key | Action |
|-----|--------|
| `+` / `=` | Increase line spacing |
| `-` / `_` | Decrease line spacing |
| `r` | Reload file from disk |

### Quit

| Key | Action |
|-----|--------|
| `q` | Quit (progress auto-saved) |

## Data storage

All state is saved under `~/.txtreader/`:

| File | Content |
|------|---------|
| `progress.json` | Last reading position per file |
| `bookmarks.json` | Bookmarks per file |
| `spacing.json` | Line spacing preference per file |

## License

MIT
