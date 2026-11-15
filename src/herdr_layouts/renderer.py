"""Pure frame rendering: the picker state in, a list of screen lines out."""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .catalog import MAX_PANES
from .library import describe
from .model import Preset
from .planner import expected_rectangles
from .theme import Palette

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
MIN_TILE_WIDTH = 20
MAX_TILE_WIDTH = 58
MIN_TILE_HEIGHT = 5
# Terminal cells are about twice as tall as wide: 0.36 looks like a wide
# terminal; up to 0.5 is used when the popup has spare height.
TILE_ASPECT = 0.36
TILE_ASPECT_TALL = 0.5
GAP = 3
MARGIN = 2
# Blank, title and tabs, subtitle, blank above; hint, detail, blank, keys below.
CHROME_ABOVE = 4
CHROME_BELOW = 4

DESCRIPTIONS = {
    "2-columns": "Two equal columns.",
    "2-rows": "Two equal rows.",
    "3-columns": "Three equal columns.",
    "3-rows": "Three equal rows.",
    "3-main-left": "Your pane takes the left 60%, two panes stacked on the right.",
    "3-main-right": "Your pane takes the right 60%, two panes stacked on the left.",
    "3-main-top": "Your pane takes the top half, two panes side by side below.",
    "3-main-bottom": "Your pane takes the bottom half, two panes side by side above.",
    "4-grid": "Four equal quadrants.",
    "4-columns": "Four equal columns.",
    "4-rows": "Four equal rows.",
    "4-main-left": "Your pane takes the left 62%, three panes stacked on the right.",
    "4-main-right": "Your pane takes the right 62%, three panes stacked on the left.",
    "4-main-top": "Your pane takes the top 60%, three panes in a row below.",
    "4-main-bottom": "Your pane takes the bottom 60%, three panes in a row above.",
    "4-wide-left": "A wide left pair over 65% of the width, a narrow pair on the right.",
    "4-wide-right": "A narrow pair on the left, a wide pair over 65% of the width.",
    "4-focus-grid": "Your pane gets the largest quadrant, three smaller panes around it.",
    "5-main-left": "Your pane takes the left 60%, four panes stacked on the right.",
    "5-main-right": "Your pane takes the right 60%, four panes stacked on the left.",
    "5-main-top": "Your pane takes the top 60%, four panes in a row below.",
    "5-main-bottom": "Your pane takes the bottom 60%, four panes in a row above.",
    "5-focus-grid": "Your pane takes the left 55%, a 2 x 2 grid on the right.",
    "5-grid": "Two panes on top, three below.",
    "5-columns": "Five equal columns.",
    "6-grid": "Three columns of two panes each.",
    "6-grid-tall": "Two columns of three panes each.",
    "6-main-left": "Your pane takes the left 55%, five panes stacked on the right.",
    "6-main-right": "Your pane takes the right 55%, five panes stacked on the left.",
    "6-main-top": "Your pane takes the top 55%, five panes in a row below.",
    "6-main-bottom": "Your pane takes the bottom 55%, five panes in a row above.",
    "6-focus-grid": "Your pane takes the left half, five panes in two rows on the right.",
}


@dataclass(frozen=True)
class Group:
    key: str  # "favorites", "2", "3", "4" or "saved"
    title: str
    presets: Tuple[Preset, ...]
    empty_hint: str = ""


@dataclass(frozen=True)
class View:
    pane_count: int
    groups: Tuple[Group, ...]
    group_index: int = 0
    selected: int = 0
    current_id: Optional[str] = None
    recent_id: Optional[str] = None
    favorites: Tuple[str, ...] = ()
    user_ids: Tuple[str, ...] = ()
    pane_labels: Tuple[str, ...] = ()  # logical order: index 0 is your pane
    message: str = ""
    message_kind: str = "info"  # info, error, busy or success
    prompt: str = ""  # a question shown in place of the detail line
    prompt_value: Optional[str] = None  # text being typed, when the prompt takes input
    zoomed: bool = False
    auto_ids: Tuple[str, ...] = ()  # the auto layout for each pane count
    pattern: str = ""  # display name of the auto pattern, if one is set

    @property
    def group(self) -> Group:
        return self.groups[self.group_index]

    @property
    def presets(self) -> Tuple[Preset, ...]:
        return self.group.presets

    def selected_preset(self) -> Optional[Preset]:
        if not self.presets:
            return None
        return self.presets[min(self.selected, len(self.presets) - 1)]


@dataclass(frozen=True)
class Grid:
    columns: int
    tile_width: int
    tile_height: int
    visible_rows: int
    total_rows: int
    spacing: int = 1  # blank line under each label; dropped when space is tight

    @property
    def row_height(self) -> int:
        return self.tile_height + 1 + self.spacing

    @property
    def width(self) -> int:
        return self.columns * self.tile_width + (self.columns - 1) * GAP


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)


def availability(layout_panes: int, pane_count: int) -> Tuple[str, str, bool]:
    """What applying a layout does to the current tab: text, colour token, allowed."""
    if pane_count > MAX_PANES:
        return "tab has more than %d panes" % MAX_PANES, "red", False
    difference = layout_panes - pane_count
    if difference == 0:
        return "ready", "green", True
    if difference > 0:
        return "adds %d pane%s" % (difference, "" if difference == 1 else "s"), "yellow", True
    amount = -difference
    return "close %d pane%s first" % (amount, "" if amount == 1 else "s"), "overlay1", False


def _legible(presets: Sequence[Preset], width: int, height: int) -> bool:
    """Your pane needs an interior row for its mark; every pane needs its own borders."""
    for preset in presets:
        for index, (left, top, right, bottom) in _boxes(preset, width, height).items():
            if bottom - top < (2 if index == 0 else 1) or right - left < 2:
                return False
    return True


def grid_for(width: int, height: int, presets: Sequence[Preset]) -> Grid:
    """Choose columns for the largest legible tiles that keep the whole group on screen.

    Popups are landscape, so arrangements that leave a column of the popup
    empty are discarded unless nothing else fits: two layouts sit side by
    side rather than stacked.
    """
    usable = max(1, width - MARGIN * 2)
    available = max(1, height - CHROME_ABOVE - CHROME_BELOW)
    item_count = max(1, len(presets))
    widest = max(1, min(item_count, 5, (usable + GAP) // (MIN_TILE_WIDTH + GAP)))
    for spacing in (1, 0):
        candidates = []
        for columns in range(1, min(item_count, 5) + 1):
            tile_width = min(MAX_TILE_WIDTH, (usable - GAP * (columns - 1)) // columns)
            rows = -(-item_count // columns)
            # Multi-row groups may grow taller to use spare height; a single row stays
            # terminal-shaped so two big squares do not dominate the popup.
            aspect = TILE_ASPECT_TALL if rows > 1 else TILE_ASPECT
            tile_height = min(round(tile_width * aspect), available // rows - 1 - spacing)
            if tile_width < MIN_TILE_WIDTH or tile_height < MIN_TILE_HEIGHT:
                continue
            if not _legible(presets, tile_width, tile_height):
                continue
            candidates.append((columns, Grid(columns, tile_width, tile_height, rows, rows, spacing)))
        if candidates:
            # Fill the width first (single-row groups use all columns), then maximise area.
            wide = [grid for columns, grid in candidates if columns >= min(widest, item_count, 3)]
            pool = wide or [grid for _, grid in candidates]
            return max(pool, key=lambda grid: (grid.tile_width * grid.tile_height, grid.columns))
    # Too small to fit everything: keep legible tiles and scroll by rows.
    columns = widest
    tile_width = max(MIN_TILE_WIDTH, min(MAX_TILE_WIDTH, (usable - GAP * (columns - 1)) // columns))
    rows = -(-item_count // columns)
    tile_height = next((h for h in range(MIN_TILE_HEIGHT, 40) if _legible(presets, tile_width, h)),
                       MIN_TILE_HEIGHT)
    visible = max(1, min(rows, available // (tile_height + 2)))
    return Grid(columns, tile_width, tile_height, visible, rows)


def move(selected: int, key: str, columns: int, count: int) -> int:
    """Spatial movement that clamps at edges instead of wrapping unexpectedly."""
    if count <= 0:
        return 0
    row, column = divmod(selected, columns)
    if key in ("right", "l"):
        return min(selected + 1, count - 1) if column < columns - 1 else selected
    if key in ("left", "h"):
        return selected - 1 if column > 0 else selected
    if key in ("down", "j"):
        return min(selected + columns, count - 1) if (row + 1) * columns < count else selected
    if key in ("up", "k"):
        return selected - columns if row > 0 else selected
    if key == "home":
        return 0
    if key == "end":
        return count - 1
    return selected


def first_visible_row(selected: int, grid: Grid, previous: int = 0) -> int:
    row = selected // grid.columns
    if row < previous:
        return row
    if row >= previous + grid.visible_rows:
        return row - grid.visible_rows + 1
    return min(previous, max(0, grid.total_rows - grid.visible_rows))


def _boxes(preset: Preset, width: int, height: int) -> Dict[int, Tuple[int, int, int, int]]:
    boxes = {}
    for index, (x, y, pane_width, pane_height) in expected_rectangles(preset).items():
        boxes[index] = (
            round(x * (width - 1)), round(y * (height - 1)),
            round((x + pane_width) * (width - 1)), round((y + pane_height) * (height - 1)),
        )
    return boxes


LIGHT = {
    frozenset(): " ", frozenset("EW"): "─", frozenset("NS"): "│",
    frozenset("ES"): "╭", frozenset("WS"): "╮", frozenset("EN"): "╰", frozenset("WN"): "╯",
    frozenset("NSE"): "├", frozenset("NSW"): "┤", frozenset("EWS"): "┬",
    frozenset("EWN"): "┴", frozenset("NSEW"): "┼",
}
# Heavy outer edges mark the selection without relying on colour (NO_COLOR, 256-colour).
# Where a light inner divider meets the heavy edge, use the mixed junction so the
# divider visibly connects instead of stopping short.
HEAVY_EDGE = {"─": "━", "│": "┃", "╭": "┏", "╮": "┓", "╰": "┗", "╯": "┛"}
HEAVY_JUNCTION = {"top": {"┬": "┯"}, "bottom": {"┴": "┷"}, "left": {"├": "┠"}, "right": {"┤": "┨"}}


def _diagram(preset: Preset, width: int, height: int, heavy: bool) -> List[List[str]]:
    """Draw the split tree on a width x height canvas that is also the tile border."""
    canvas: List[List[Set[str]]] = [[set() for _ in range(width)] for _ in range(height)]

    def link(row: int, column: int, direction: str) -> None:
        if 0 <= row < height and 0 <= column < width:
            canvas[row][column].add(direction)

    for left, top, right, bottom in _boxes(preset, width, height).values():
        for column in range(left, right):
            for row in (top, bottom):
                link(row, column, "E")
                link(row, column + 1, "W")
        for row in range(top, bottom):
            for column in (left, right):
                link(row, column, "S")
                link(row + 1, column, "N")

    cells = [[LIGHT.get(frozenset(cell), " ") for cell in row] for row in canvas]
    if heavy:
        for row in range(height):
            for column in range(width):
                sides = [side for side, on in (("top", row == 0), ("bottom", row == height - 1),
                                               ("left", column == 0), ("right", column == width - 1)) if on]
                if not sides:
                    continue
                character = cells[row][column]
                for side in sides:
                    character = HEAVY_JUNCTION[side].get(character, character)
                cells[row][column] = HEAVY_EDGE.get(character, character)
    return cells


def _pane_word(role: str, label: str, room: int) -> str:
    """The text inside a pane: the longest option that fits the room available."""
    if role == "you":
        options = ["● you", "●"]
    elif role == "new":
        options = ["+ new", "+"]
    elif label and room >= 3:
        options = [label] if len(label) <= room else [label[: room - 1] + "…"]
    else:
        options = []
    return next((option for option in options if len(option) <= room), "")


def _tile(preset: Preset, view: View, selected: bool, grid: Grid, enabled: bool,
          palette: Palette) -> List[str]:
    """A filled miniature: your pane in the accent, new panes in yellow, others labelled."""
    width, height = grid.tile_width, grid.tile_height
    cells = _diagram(preset, width, height, heavy=selected)
    boxes = _boxes(preset, width, height)
    new_from = min(view.pane_count, preset.pane_count)
    state = "_selected" if selected else ""
    if enabled:
        border = palette.role("border_selected") if selected else palette.role("border")
        fills = {role: palette.role(role + "_fill" + state) for role in ("you", "new", "other")}
        inks = {"you": palette.role("you_ink"), "new": palette.role("new_ink"),
                "other": palette.role("other_ink")}
    else:
        border = palette.role("border_selected") if selected else palette.role("border_dim")
        fills = {role: palette.role("muted_fill") for role in ("you", "new", "other")}
        inks = {role: palette.role("muted_ink") for role in ("you", "new", "other")}

    styled = [[(character, border, None) for character in row] for row in cells]
    for index, (left, top, right, bottom) in boxes.items():
        role = "you" if index == 0 else ("new" if index >= new_from else "other")
        for row in range(top + 1, bottom):
            for column in range(left + 1, right):
                styled[row][column] = (" ", border, fills[role])
        inner = right - left - 1
        if bottom - top < 2:
            continue
        # Labels describe the tab's real panes, so they only map when the counts match.
        mapped = preset.pane_count == view.pane_count and index < len(view.pane_labels)
        label = view.pane_labels[index] if role == "other" and mapped else ""
        word = _pane_word(role, label, inner - 2 if inner > 4 else inner)
        if not word:
            continue
        row = (top + bottom) // 2
        start = left + 1 + (inner - len(word)) // 2
        for offset, character in enumerate(word):
            styled[row][start + offset] = (character, inks[role], fills[role])

    lines = []
    for row in styled:
        # Group runs of one style so each line carries a handful of escapes, not one per cell.
        parts, run, style = [], [], None
        for character, fg, bg in row:
            if (fg, bg) != style and run:
                parts.append(palette.fg(style[0]) + (palette.bg(style[1]) if style[1] else "")
                             + "".join(run) + palette.reset)
                run = []
            run.append(character)
            style = (fg, bg)
        if run:
            parts.append(palette.fg(style[0]) + (palette.bg(style[1]) if style[1] else "")
                         + "".join(run) + palette.reset)
        lines.append("".join(parts))
    return lines


def _label(preset: Preset, view: View, number: int, selected: bool, width: int,
           enabled: bool, palette: Palette) -> str:
    """Keycap number, favourite star, name and tag, centred under the tile."""
    is_current = preset.id == view.current_id
    tag = "current" if is_current else ("last" if preset.id == view.recent_id else "")
    if not tag and view.group.key in ("saved", "favorites"):
        tag = "%dp" % preset.pane_count
    star = "★ " if preset.id in view.favorites else ""
    auto = "⟳ " if preset.id in view.auto_ids else ""
    name = preset.name
    key = str(number) if number <= 9 else ""
    fixed = (len(key) + 3 if key else 0) + len(auto) + len(star) + (len(tag) + 1 if tag else 0)
    if fixed + len(name) > width:
        name = name[: max(1, width - fixed - 1)] + "…"
    plain_length = fixed + len(name)
    pad = max(0, (width - plain_length) // 2)
    keycap = ""
    if key:
        cap_bg = "accent" if selected else palette.role("chip")
        cap_fg = "panel_bg" if selected else ("text" if enabled else "overlay1")
        keycap = palette.bg(cap_bg) + palette.fg(cap_fg) + palette.bold + " " + key + " " + palette.reset + " "
    name_color = "accent" if selected else ("text" if enabled else "overlay1")
    text = palette.paint(auto, "teal", bold=True) if auto else ""
    text += palette.paint(star, palette.role("favorite")) if star else ""
    text += palette.fg(name_color) + (palette.bold if selected else "") + name + palette.reset
    if tag:
        text += " " + palette.paint(tag, "green" if is_current else "subtext0")
    return " " * pad + keycap + text + " " * max(0, width - pad - plain_length)


def _fit(line: str, width: int) -> str:
    plain_length = len(strip_ansi(line))
    if plain_length <= width:
        return line + " " * (width - plain_length)
    # Truncate by visible characters while keeping escape sequences intact.
    out, visible, index = [], 0, 0
    while index < len(line) and visible < width:
        match = ANSI_RE.match(line, index)
        if match:
            out.append(match.group(0))
            index = match.end()
            continue
        out.append(line[index])
        visible += 1
        index += 1
    return "".join(out) + "\x1b[0m"


def _spread(left: str, right: str, width: int) -> str:
    gap = width - len(strip_ansi(left)) - len(strip_ansi(right))
    return left + " " * gap + right if gap >= 1 else left


def _pill(text: str, fg: str, bg: str, palette: Palette, bold: bool = False) -> str:
    return palette.bg(bg) + palette.fg(fg) + (palette.bold if bold else "") + " " + text + " " + palette.reset


def _tab_title(group: Group, view: View) -> Tuple[str, bool]:
    if group.key.isdigit():
        return group.title, availability(int(group.key), view.pane_count)[2]
    return "%s %d" % (group.title, len(group.presets)), bool(group.presets)


def footer_keys(view: View) -> List[Tuple[str, str]]:
    if view.prompt_value is not None:
        return [("enter", "save"), ("esc", "cancel")]
    if view.prompt:
        return [("y", "confirm"), ("esc", "cancel")]
    keys = [("enter", "apply"), ("1-9", "pick"), ("tab", "group")]
    if view.presets:
        keys += [("f", "favorite"), ("a", "auto")]
    if 2 <= view.pane_count <= MAX_PANES:
        keys.append(("s", "save tab"))
    preset = view.selected_preset()
    if preset is not None and preset.id.startswith("saved:"):
        keys += [("r", "rename"), ("d", "delete")]
    keys.append(("esc", "close"))
    return keys


def detail_text(preset: Preset) -> str:
    return DESCRIPTIONS.get(preset.id) or describe(preset)


def render(view: View, width: int, height: int, palette: Palette, scroll_row: int = 0) -> List[str]:
    width, height = max(1, width), max(1, height)
    if width < 50 or height < 16:
        lines = ["", palette.paint("  Layouts", "accent", bold=True), "",
                 palette.paint("  Enlarge the popup to at least 50 x 16.", "subtext0")]
        return [_fit(line, width) for line in lines[:height]]

    chip = palette.role("chip")
    tabs = []
    for index, group in enumerate(view.groups):
        title, usable = _tab_title(group, view)
        if index == view.group_index:
            tabs.append(_pill(title, "panel_bg", "accent", palette, bold=True))
        else:
            tabs.append(_pill(title, "text" if usable else "overlay1", chip, palette))
    lines: List[str] = [""]
    lines.append(_spread(" " * MARGIN + palette.paint("Layouts", "accent", bold=True),
                         " ".join(tabs) + " " * MARGIN, width))
    noun = "pane" if view.pane_count == 1 else "panes"
    subtitle = " " * MARGIN + palette.paint("this tab: %d %s%s" % (
        view.pane_count, noun, ", zoomed" if view.zoomed else ""), "subtext0")
    if view.group.key.isdigit():
        status, status_color, _ = availability(int(view.group.key), view.pane_count)
        subtitle += palette.paint("   " + status, status_color)
    if view.pattern:
        subtitle += palette.paint("   ⟳ auto: " + view.pattern, "teal")
    lines.append(subtitle)
    lines.append("")

    body_height = height - CHROME_ABOVE - CHROME_BELOW
    first_row = 0
    grid = None
    if not view.presets:
        hint = view.group.empty_hint or "Nothing here yet."
        hint_lines = hint.split("\n")
        lines.extend([""] * max(0, (body_height - len(hint_lines)) // 2))
        for text in hint_lines:
            pad = max(MARGIN, (width - len(text)) // 2)
            lines.append(" " * pad + palette.paint(text, "subtext0"))
    else:
        grid = grid_for(width, height, view.presets)
        first_row = first_visible_row(view.selected, grid, scroll_row)
        shown_rows = range(first_row, min(grid.total_rows, first_row + grid.visible_rows))
        used = len(shown_rows) * grid.row_height
        banner = ""
        if not any(availability(preset.pane_count, view.pane_count)[2] for preset in view.presets):
            # A fully unavailable group says why up front, so dim tiles do not look broken.
            reason = availability(view.presets[0].pane_count, view.pane_count)[0]
            banner = "Preview only: %s to use these layouts." % reason.replace(" first", "")
            used += 2
        lines.extend([""] * max(0, (body_height - used) // 2))
        if banner:
            pad = max(MARGIN, (width - len(banner)) // 2)
            lines.extend([" " * pad + palette.paint(banner, "subtext0"), ""])
        indent = " " * max(MARGIN, (width - grid.width) // 2)
        for row in shown_rows:
            start = row * grid.columns
            row_presets = view.presets[start : start + grid.columns]
            enabled = [availability(preset.pane_count, view.pane_count)[2] for preset in row_presets]
            tiles = [
                _tile(preset, view, start + offset == view.selected, grid, enabled[offset], palette)
                for offset, preset in enumerate(row_presets)
            ]
            for line_index in range(grid.tile_height):
                lines.append(indent + (" " * GAP).join(tile[line_index] for tile in tiles))
            lines.append(indent + (" " * GAP).join(
                _label(preset, view, start + offset + 1, start + offset == view.selected,
                       grid.tile_width, enabled[offset], palette)
                for offset, preset in enumerate(row_presets)))
            lines.extend([""] * grid.spacing)

    body_limit = height - CHROME_BELOW
    lines = lines[:body_limit]
    while len(lines) < body_limit:
        lines.append("")
    hint = ""
    if grid is not None:
        hidden_above, hidden_below = first_row, grid.total_rows - first_row - grid.visible_rows
        marks = [text for text, show in (("↑ more", hidden_above > 0), ("↓ more", hidden_below > 0)) if show]
        if marks:
            hint = " " * MARGIN + palette.paint("  ".join(marks), "subtext0")
    lines.append(hint)

    if view.prompt:
        text = " " * MARGIN + palette.paint(view.prompt, "accent", bold=True)
        if view.prompt_value is not None:
            field_text = view.prompt_value + "▏"
            text += "  " + palette.bg(chip) + palette.fg("text") + " " + field_text + " " * max(
                1, 26 - len(field_text)) + palette.reset
        lines.append(text)
    elif view.message:
        color = {"error": "red", "busy": "accent", "success": "green"}.get(view.message_kind, "yellow")
        lines.append(" " * MARGIN + palette.paint(view.message, color, bold=view.message_kind != "info"))
    else:
        preset = view.selected_preset()
        if preset is not None:
            status, color, _ = availability(preset.pane_count, view.pane_count)
            action = "already applied" if preset.id == view.current_id else status
            left = " " * MARGIN + palette.paint(preset.name, "text", bold=True) + palette.paint(
                "  " + detail_text(preset), "subtext0")
            lines.append(_spread(left, _pill(action, "green" if action == "already applied" else color,
                                             chip, palette) + " " * MARGIN, width))
        else:
            lines.append("")
    lines.append("")
    lines.append(" " * MARGIN + "   ".join(
        _pill(key, "text", chip, palette) + " " + palette.paint(label, "subtext0")
        for key, label in footer_keys(view)))
    return [_fit(line, width) for line in lines[:height]]
