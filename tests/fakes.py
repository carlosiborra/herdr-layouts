"""A geometric HerdR stand-in: splits and swaps move real rectangles."""
import copy


class GeometryClient:
    def __init__(self, panes, active="w1:p1", zoomed=False, fail_on_split=None):
        # panes: {pane_id: (x, y, width, height)} on a 120 x 60 area
        self.panes = dict(panes)
        self.active = active
        self.zoomed = zoomed
        self.fail_on_split = fail_on_split
        self.calls = []
        self._next = 100

    def layout(self, pane_id):
        self.calls.append(("layout", pane_id))
        return copy.deepcopy({"result": {"layout": {
            "workspace_id": "w1", "tab_id": "w1:t1", "zoomed": self.zoomed,
            "area": {"x": 0, "y": 0, "width": 120, "height": 60},
            "panes": [{"pane_id": pid, "rect": dict(zip(("x", "y", "width", "height"), rect))}
                      for pid, rect in self.panes.items()],
            "splits": [],
        }}})

    def split(self, pane_id, direction="right", ratio=0.5):
        self.calls.append(("split", pane_id, direction, round(ratio, 2)))
        if self.fail_on_split is not None and len([c for c in self.calls if c[0] == "split"]) == self.fail_on_split:
            from herdr_layouts.herdr import HerdrError
            raise HerdrError("injected split failure")
        x, y, width, height = self.panes[pane_id]
        self._next += 1
        new_id = "w1:p%d" % self._next
        if direction == "right":
            first = round(width * ratio)
            self.panes[pane_id] = (x, y, first, height)
            self.panes[new_id] = (x + first, y, width - first, height)
        else:
            first = round(height * ratio)
            self.panes[pane_id] = (x, y, width, first)
            self.panes[new_id] = (x, y + first, width, height - first)
        return new_id

    def swap(self, source, target):
        self.calls.append(("swap", source, target))
        self.panes[source], self.panes[target] = self.panes[target], self.panes[source]

    def pane_labels(self, tab_id):
        self.calls.append(("pane_labels", tab_id))
        return {pid: ("claude · repo" if i % 2 else "shell · tmp") for i, pid in enumerate(self.panes)}

    def close(self, pane_id):
        self.calls.append(("close", pane_id))
        del self.panes[pane_id]

    def unzoom(self, pane_id):
        self.calls.append(("unzoom", pane_id))
        self.zoomed = False


class ScriptedTerminal:
    """Feeds one key per poll and records drawn frames."""

    def __init__(self, keys, size=(110, 34)):
        # Strings longer than one character that are not key names are typed character by character.
        expanded = []
        for key in keys:
            if key.startswith("type:"):
                expanded.extend(key[5:])
            else:
                expanded.append(key)
        keys = expanded
        self._keys = list(keys)
        self._size = size
        self.frames = []
        outer = self

        class _Screen:
            def draw(self, lines, size):
                outer.frames.append("\n".join(lines))

        self.screen = _Screen()

    def size(self):
        return self._size

    def keys(self, timeout=0.1):
        if not self._keys:
            return ("escape",)
        return (self._keys.pop(0),)


class FakeRpc:
    """Socket stand-in over a GeometryClient: exports the split tree and applies ratio changes."""

    def __init__(self, client):
        self.client = client
        self.ratio_calls = []

    def _tree(self, panes):
        from herdr_layouts.planner import LayoutSnapshot, PaneRect, preset_from_snapshot
        rects = tuple(PaneRect(pid, *rect) for pid, rect in panes.items())
        ids = [rect.pane_id for rect in rects]
        snapshot = LayoutSnapshot("w1", "w1:t1", ids[0], (0, 0, 120, 60), rects,
                                  self.client.layout(ids[0]), False)
        from herdr_layouts.planner import pane_ids_by_logical_index
        order = pane_ids_by_logical_index(snapshot)
        tree = preset_from_snapshot(snapshot).tree

        def convert(node):
            if hasattr(node, "index"):
                return {"type": "pane", "pane_id": order[node.index]}
            return {"type": "split", "direction": node.direction, "ratio": round(node.ratio, 4),
                    "first": convert(node.first), "second": convert(node.second)}
        return convert(tree)

    def export(self, tab_id):
        return {"tab_id": tab_id, "zoomed": self.client.zoomed, "root": self._tree(self.client.panes)}

    def set_ratio(self, tab_id, path, ratio):
        from herdr_layouts.fit import rectangles
        self.ratio_calls.append((tuple(path), round(ratio, 4)))
        root = self._tree(self.client.panes)
        for pane_id, (x, y, width, height) in rectangles(root, {tuple(path): ratio}).items():
            self.client.panes[pane_id] = (round(x * 120), round(y * 60), round(width * 120), round(height * 60))
