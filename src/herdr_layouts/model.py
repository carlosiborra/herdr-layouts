from dataclasses import dataclass
from typing import Literal, Union


Direction = Literal["right", "down"]


@dataclass(frozen=True)
class Leaf:
    index: int


@dataclass(frozen=True)
class Split:
    direction: Direction
    ratio: float
    first: "Node"
    second: "Node"


Node = Union[Leaf, Split]


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    pane_count: int
    tree: Node
    # Pattern family ("main-left", "grid", ...) shared across pane counts, so an
    # auto layout chosen for one count can follow along when panes come and go.
    family: str = ""

