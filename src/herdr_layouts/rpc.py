"""The HerdR socket API, for the two calls the CLI does not expose.

layout.export returns the split tree with pane IDs, and layout.set_split_ratio
changes one split in about 5 ms. Both leave every pane and process in place,
which is what makes ratio-only fitting and animation safe. Talking to the socket directly is
deliberate: the HerdR CLI has no generic API-call command, so these two methods are not reachable
any other way. The path comes from HERDR_SOCKET_PATH; the home-relative default is used only when
that variable is unset. Each request uses
its own connection because the server answers one request per connection.
"""
import json
import os
import socket
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .herdr import HerdrError


def socket_path(environ: Mapping[str, str]) -> Path:
    configured = environ.get("HERDR_SOCKET_PATH")
    if configured:
        return Path(configured)
    return Path.home() / ".config" / "herdr" / "herdr.sock"


class Rpc:
    def __init__(self, path: Path, timeout_seconds: float = 2.0) -> None:
        self.path = Path(path)
        self.timeout_seconds = timeout_seconds

    def call(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        request = json.dumps({"id": "herdr-layouts", "method": method, "params": dict(params)})
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.path))
                connection.sendall(request.encode("utf-8") + b"\n")
                buffer = b""
                while not buffer.endswith(b"\n"):
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
        except OSError as exc:
            raise HerdrError("HerdR socket unavailable: %s" % exc) from exc
        try:
            payload = json.loads(buffer)
        except (TypeError, ValueError) as exc:
            raise HerdrError("HerdR socket returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HerdrError("HerdR socket response is not an object")
        error = payload.get("error")
        if isinstance(error, dict):
            raise HerdrError("HerdR %s failed: %s" % (method, error.get("message", "unknown error")))
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HerdrError("HerdR %s returned no result" % method)
        return result

    def export(self, tab_id: str) -> Mapping[str, Any]:
        """The tab's layout: workspace_id, tab_id, zoomed, focused_pane_id and the split tree root."""
        layout = self.call("layout.export", {"tab_id": tab_id}).get("layout")
        if not isinstance(layout, dict) or not isinstance(layout.get("root"), dict):
            raise HerdrError("HerdR layout export is malformed")
        return layout

    def set_ratio(self, tab_id: str, path: Sequence[bool], ratio: float) -> None:
        self.call("layout.set_split_ratio", {"tab_id": tab_id, "path": list(path), "ratio": round(ratio, 4)})


def connect(environ: Mapping[str, str]) -> Optional[Rpc]:
    path = socket_path(environ)
    return Rpc(path) if os.path.exists(path) else None
