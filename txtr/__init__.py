#!/usr/bin/env python3
"""CLI txt reader with bookmarks, search highlighting, and auto-resume."""

import curses
import json
import os
import re
import sys
from pathlib import Path
from wcwidth import wcswidth, wcwidth

DATA_DIR = Path.home() / ".txtreader"
PROGRESS_FILE = DATA_DIR / "progress.json"
BOOKMARKS_FILE = DATA_DIR / "bookmarks.json"
SPACING_FILE = DATA_DIR / "spacing.json"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path, default=None):
    if default is None:
        default = {}
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def split_by_display_width(text, max_width):
    """Split text into chunks each fitting within `max_width` display columns."""
    if max_width <= 0:
        return [""]
    chunks = []
    cur = ""
    cur_w = 0
    for ch in text:
        cw = wcwidth(ch) if wcwidth(ch) > 0 else 1
        if cur_w + cw > max_width:
            chunks.append(cur)
            cur = ch
            cur_w = cw
        else:
            cur += ch
            cur_w += cw
    chunks.append(cur)
    return chunks


def highlight_chunk(chunk, term):
    """Split chunk into (text, is_highlight) segments for inline highlighting."""
    if not term or not chunk:
        return [(chunk, False)]
    flags = re.IGNORECASE if term.islower() else 0
    parts = []
    pos = 0
    for m in re.finditer(re.escape(term), chunk, flags):
        if m.start() > pos:
            parts.append((chunk[pos:m.start()], False))
        parts.append((m.group(), True))
        pos = m.end()
    if pos < len(chunk):
        parts.append((chunk[pos:], False))
    return parts or [(chunk, False)]


class Reader:
    def __init__(self, filepath):
        self.filepath = os.path.abspath(filepath)
        self.source_lines = []
        self.search_results = []       # list of source line indices
        self.current_search_idx = -1
        self.search_term = ""
        self.bookmarks = set()
        self.top_source_line = 0       # first visible source line
        self.scroll_row_offset = 0     # chunk offset within top_source_line
        self.spacing = 0
        self.running = True
        self.message = ""
        self.message_timer = 0
        self.input_mode = None         # "search" | None
        self.input_buffer = ""
        self.utf8_buf = b""           # accumulate raw bytes for CJK input

        self._load_file()
        self._load_state()

    def _load_file(self):
        try:
            with open(self.filepath, encoding="utf-8") as f:
                self.source_lines = f.read().splitlines()
        except (IOError, UnicodeDecodeError):
            try:
                with open(self.filepath, encoding="gbk") as f:
                    self.source_lines = f.read().splitlines()
            except Exception:
                self.source_lines = ["[Error: cannot read file]"]
        if not self.source_lines:
            self.source_lines = ["[Empty file]"]

    def _load_state(self):
        ensure_data_dir()
        progress = load_json(PROGRESS_FILE)
        self.top_source_line = progress.get(self.filepath, 0)
        self.top_source_line = min(self.top_source_line, max(0, len(self.source_lines) - 1))

        bookmarks = load_json(BOOKMARKS_FILE)
        self.bookmarks = set(bookmarks.get(self.filepath, []))

        spacing = load_json(SPACING_FILE)
        self.spacing = spacing.get(self.filepath, 0)

    def _save_progress(self):
        progress = load_json(PROGRESS_FILE)
        progress[self.filepath] = self.top_source_line
        save_json(PROGRESS_FILE, progress)

    def _save_bookmarks(self):
        bookmarks = load_json(BOOKMARKS_FILE)
        bookmarks[self.filepath] = sorted(self.bookmarks)
        save_json(BOOKMARKS_FILE, bookmarks)

    def _save_spacing(self):
        spacing = load_json(SPACING_FILE)
        spacing[self.filepath] = self.spacing
        save_json(SPACING_FILE, spacing)

    def _flash(self, msg):
        self.message = msg
        self.message_timer = 3

    # ── layout ────────────────────────────────────────────────────

    def _chunks_for_line(self, src_idx, content_w):
        line = self.source_lines[src_idx]
        if not line:
            return [""]
        return split_by_display_width(line, content_w)

    def _build_screen_rows(self, start_src, start_offset, usable, content_w):
        """Build screen-row list starting from (start_src, start_offset).
        Each row: (src_idx, chunk_idx, chunk_text, is_first_chunk, y).
        Returns (rows, last_src).
        """
        rows = []
        y = 0
        for si in range(start_src, len(self.source_lines)):
            chunks = self._chunks_for_line(si, content_w)
            start_ci = start_offset if si == start_src else 0
            for ci in range(start_ci, len(chunks)):
                if y >= usable:
                    return rows, si
                rows.append((si, ci, chunks[ci], ci == 0, y))
                y += 1
                for _ in range(self.spacing):
                    if y >= usable:
                        return rows, si
                    rows.append((si, ci, None, False, y))
                    y += 1
        return rows, len(self.source_lines)

    # ── screen-row-based navigation ────────────────────────────────

    def _scroll_down(self, count=1, content_w=40):
        for _ in range(count):
            chunks = self._chunks_for_line(self.top_source_line, content_w)
            if self.scroll_row_offset + 1 < len(chunks):
                self.scroll_row_offset += 1
            elif self.top_source_line < len(self.source_lines) - 1:
                self.top_source_line += 1
                self.scroll_row_offset = 0

    def _scroll_up(self, count=1, content_w=40):
        for _ in range(count):
            if self.scroll_row_offset > 0:
                self.scroll_row_offset -= 1
            elif self.top_source_line > 0:
                self.top_source_line -= 1
                chunks = self._chunks_for_line(self.top_source_line, content_w)
                self.scroll_row_offset = max(0, len(chunks) - 1)

    def _scroll_page_down(self, usable, content_w):
        y = 0
        for _ in range(usable):
            chunks = self._chunks_for_line(self.top_source_line, content_w)
            if self.scroll_row_offset + 1 < len(chunks):
                self.scroll_row_offset += 1
            elif self.top_source_line < len(self.source_lines) - 1:
                self.top_source_line += 1
                self.scroll_row_offset = 0
            y += 1 + self.spacing
            if y >= usable:
                break

    def _scroll_page_up(self, usable, content_w):
        y = 0
        for _ in range(usable):
            if self.scroll_row_offset > 0:
                self.scroll_row_offset -= 1
            elif self.top_source_line > 0:
                self.top_source_line -= 1
                chunks = self._chunks_for_line(self.top_source_line, content_w)
                self.scroll_row_offset = len(chunks) - 1
            y += 1 + self.spacing
            if y >= usable:
                break

    def _jump_to_src_line(self, src_idx):
        self.top_source_line = max(0, min(src_idx, len(self.source_lines) - 1))
        self.scroll_row_offset = 0

    def _current_display_src_line(self):
        """The source line currently shown at top-left of screen."""
        return self.top_source_line

    # ── search ────────────────────────────────────────────────────

    def _search(self):
        self.search_results = []
        term = self.search_term
        if not term:
            self.current_search_idx = -1
            return
        flags = re.IGNORECASE if term.islower() else 0
        try:
            pattern = re.compile(re.escape(term), flags)
        except re.error:
            return
        for i, line in enumerate(self.source_lines):
            if pattern.search(line):
                self.search_results.append(i)
        if self.search_results:
            self.current_search_idx = 0
            self._jump_to_src_line(self.search_results[0])
            self._flash(f"Match 1/{len(self.search_results)}")
        else:
            self.current_search_idx = -1
            self._flash("No match")

    def _search_next(self, delta=1):
        if not self.search_results:
            return
        cnt = len(self.search_results)
        self.current_search_idx = (self.current_search_idx + delta) % cnt
        self._jump_to_src_line(self.search_results[self.current_search_idx])
        self._flash(f"Match {self.current_search_idx + 1}/{cnt}")

    # ── bookmarks ─────────────────────────────────────────────────

    def _toggle_bookmark(self):
        line = self.top_source_line
        if line in self.bookmarks:
            self.bookmarks.remove(line)
            self._save_bookmarks()
            self._flash(f"Bookmark removed at line {line + 1}")
        else:
            self.bookmarks.add(line)
            self._save_bookmarks()
            self._flash(f"Bookmark added at line {line + 1}")

    # ── run loop ──────────────────────────────────────────────────

    def run(self, stdscr):
        curses.use_default_colors()
        curses.curs_set(0)
        stdscr.timeout(100)

        curses.init_pair(1, curses.COLOR_CYAN, -1)       # status bar
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_YELLOW)  # search highlight
        curses.init_pair(3, curses.COLOR_GREEN, -1)       # bookmark marker
        curses.init_pair(4, curses.COLOR_RED, -1)         # flash message

        while self.running:
            self._draw(stdscr)
            key = stdscr.getch()
            self._handle_key(key, stdscr)
            if self.message_timer > 0:
                self.message_timer -= 1
                if self.message_timer == 0:
                    self.message = ""

    # ── drawing ───────────────────────────────────────────────────

    def _draw(self, stdscr):
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        usable = height - 1
        content_w = max(10, width - 3)

        if self.input_mode == "search":
            bar = f"Search: {self.input_buffer}"
            try:
                stdscr.addstr(height - 1, 0, bar[:width], curses.A_REVERSE)
            except curses.error:
                pass
            stdscr.refresh()
            return

        rows, _ = self._build_screen_rows(
            self.top_source_line, self.scroll_row_offset, usable, content_w
        )

        for si, ci, chunk, is_first, y in rows:
            if y >= usable:
                break

            if chunk is None:
                try:
                    stdscr.addstr(y, 0, " ·")
                except curses.error:
                    pass
                continue

            is_bookmarked = si in self.bookmarks
            is_hit = bool(self.search_results and si in self.search_results)

            marker = " ◆ " if (is_bookmarked and is_first) else "   "
            marker_attr = curses.color_pair(3) if (is_bookmarked and is_first) else curses.A_NORMAL

            try:
                stdscr.addstr(y, 0, marker, marker_attr)
            except curses.error:
                pass

            # Determine row-level attributes: only first chunk of a bookmarked
            # line gets the marker + color, not the entire paragraph.
            row_bookmarked = is_bookmarked and is_first

            if is_hit and self.search_term:
                segments = highlight_chunk(chunk, self.search_term)
                x = 3
                for seg_text, seg_hl in segments:
                    if not seg_text:
                        continue
                    attr = curses.color_pair(2) if seg_hl else (curses.color_pair(3) if row_bookmarked else curses.A_NORMAL)
                    try:
                        stdscr.addstr(y, x, seg_text, attr)
                    except curses.error:
                        pass
                    sw = wcswidth(seg_text) if wcswidth(seg_text) >= 0 else len(seg_text)
                    x += sw
            else:
                attr = curses.color_pair(3) if row_bookmarked else curses.A_NORMAL
                try:
                    stdscr.addstr(y, 3, chunk, attr)
                except curses.error:
                    pass

        # Status bar
        pct = round((self.top_source_line + 1) / max(1, len(self.source_lines)) * 100)
        left = os.path.basename(self.filepath)
        mid = f"L{self.top_source_line + 1}/{len(self.source_lines)} ({pct}%)"
        right = f"spacing:{self.spacing}"
        if self.search_term:
            right += f"  /{self.search_term}"

        if self.message and self.message_timer > 0:
            bar = self.message[:width]
            bar_attr = curses.color_pair(4) | curses.A_REVERSE
        else:
            lw = wcswidth(left) if wcswidth(left) >= 0 else len(left)
            mw = wcswidth(mid) if wcswidth(mid) >= 0 else len(mid)
            rw = wcswidth(right) if wcswidth(right) >= 0 else len(right)
            gap = width - lw - mw - rw - 3
            if gap >= 1:
                bar = f"{left} {' ' * gap}{mid}  {right}"
            else:
                bar = f"{left} {mid} {right}"
            bar = bar[:width]
            bar_attr = curses.color_pair(1) | curses.A_REVERSE

        try:
            stdscr.addstr(height - 1, 0, bar, bar_attr)
        except curses.error:
            pass

        stdscr.refresh()

    # ── input ─────────────────────────────────────────────────────

    def _handle_key(self, key, stdscr):
        if self.input_mode == "search":
            if key in (curses.KEY_ENTER, 10, 13):
                self.search_term = self.input_buffer
                self.input_mode = None
                self.input_buffer = ""
                self.utf8_buf = b""
                self._search()
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if self.input_buffer:
                    self.input_buffer = self.input_buffer[:-1]
                    self.utf8_buf = b""
                else:
                    # Backspace on empty input = cancel search
                    self.input_mode = None
                    self.utf8_buf = b""
            elif key > 31 and key != 127:
                if key <= 255:
                    # Raw byte (possibly part of a UTF-8 multi-byte sequence)
                    self.utf8_buf += bytes([key])
                    try:
                        char = self.utf8_buf.decode("utf-8")
                        self.input_buffer += char
                        self.utf8_buf = b""
                    except UnicodeDecodeError:
                        pass  # wait for more bytes
                else:
                    # Direct Unicode code point (some terminals)
                    self.utf8_buf = b""
                    self.input_buffer += chr(key)
            return

        height, width = stdscr.getmaxyx()
        usable = height - 1
        content_w = max(10, width - 3)

        if key == ord("c"):
            if self.search_results or self.search_term:
                self.search_results = []
                self.current_search_idx = -1
                self.search_term = ""
                self.message = "Search cleared"
                self.message_timer = 3
        elif key == ord("q"):
            self._save_progress()
            self.running = False
        elif key == ord("/"):
            self.input_mode = "search"
            self.input_buffer = ""
        elif key == ord("n"):
            self._search_next(1)
        elif key == ord("N"):
            self._search_next(-1)
        elif key == ord("b"):
            self._toggle_bookmark()
        elif key == ord("B"):
            self._bookmark_list(stdscr)
        elif key in (ord("+"), ord("=")):
            prev = self.spacing
            self.spacing = min(5, self.spacing + 1)
            self._save_spacing()
            self._flash(f"Spacing: {prev} -> {self.spacing}")
        elif key in (ord("-"), ord("_")):
            prev = self.spacing
            self.spacing = max(0, self.spacing - 1)
            self._save_spacing()
            self._flash(f"Spacing: {prev} -> {self.spacing}")
        elif key == ord("g"):
            self._jump_to_src_line(0)
        elif key == ord("G"):
            self._jump_to_src_line(len(self.source_lines) - 1)
        elif key == curses.KEY_UP:
            self._scroll_up(1, content_w)
        elif key == curses.KEY_DOWN:
            self._scroll_down(1, content_w)
        elif key == curses.KEY_PPAGE:
            self._scroll_page_up(usable, content_w)
        elif key == curses.KEY_NPAGE:
            self._scroll_page_down(usable, content_w)
        elif key == curses.KEY_HOME:
            self._jump_to_src_line(0)
        elif key == curses.KEY_END:
            self._jump_to_src_line(len(self.source_lines) - 1)
        elif key == ord("r"):
            old = self.top_source_line
            self._load_file()
            self._jump_to_src_line(min(old, len(self.source_lines) - 1))
            self.search_results = []
            self.current_search_idx = -1
            self.search_term = ""
            self._flash("Reloaded")

    # ── bookmark list ─────────────────────────────────────────────

    def _bookmark_list(self, stdscr):
        if not self.bookmarks:
            self._flash("No bookmarks")
            return

        bm_list = sorted(self.bookmarks)
        idx = 0
        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()

            for i, bm in enumerate(bm_list):
                if i >= height - 2:
                    break
                prefix = " > " if i == idx else "   "
                preview = self.source_lines[bm][:width - 25] if bm < len(self.source_lines) else ""
                line_text = f"{prefix}L{bm + 1}: {preview}"
                try:
                    a = curses.A_REVERSE if i == idx else curses.A_NORMAL
                    stdscr.addstr(i, 0, line_text[:width], a)
                except curses.error:
                    pass

            bar = " j/k:nav  Enter:jump  d:delete  q:back"
            try:
                stdscr.addstr(height - 1, 0, bar[:width], curses.color_pair(1) | curses.A_REVERSE)
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key == ord("q"):
                break
            elif key in (ord("j"), curses.KEY_DOWN):
                idx = min(len(bm_list) - 1, idx + 1)
            elif key in (ord("k"), curses.KEY_UP):
                idx = max(0, idx - 1)
            elif key in (curses.KEY_ENTER, 10, 13):
                self._jump_to_src_line(bm_list[idx])
                self._flash(f"Jumped to line {bm_list[idx] + 1}")
                break
            elif key == ord("d"):
                self.bookmarks.remove(bm_list[idx])
                self._save_bookmarks()
                bm_list = sorted(self.bookmarks)
                if not bm_list:
                    break
                idx = min(idx, len(bm_list) - 1)


def main():
    if len(sys.argv) < 2:
        ensure_data_dir()
        progress = load_json(PROGRESS_FILE)
        if not progress:
            print("Usage: txtr <file>")
            sys.exit(1)
        recent = None
        recent_mtime = 0
        for fp in progress:
            if os.path.exists(fp):
                mtime = os.path.getmtime(fp)
                if mtime > recent_mtime:
                    recent_mtime = mtime
                    recent = fp
        if not recent:
            print("No recent files. Usage: txtr <file>")
            sys.exit(1)
        filepath = recent
    elif sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        print("""
Usage: txtr [file]

Key bindings:
  ↑/↓       Scroll one screen row
  PgUp/PgDn Scroll one page
  g/G       Jump to first/last line
  /         Search (Enter to confirm, Backspace on empty to cancel)
  n/N       Next/previous match
  c         Clear search highlights
  b         Toggle bookmark
  B         Bookmark list (j/k:nav Enter:jump d:delete q:back)
  +/-       Adjust line spacing
  r         Reload file
  q         Quit (auto-save position)

Data: ~/.txtreader/
""")
        sys.exit(0)
    else:
        filepath = sys.argv[1]

    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    reader = Reader(filepath)
    try:
        curses.wrapper(reader.run)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
