from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EndpointHosts:
    api: str = "https://api.dropboxapi.com"
    content: str = "https://content.dropboxapi.com"
    notify: str = "https://notify.dropboxapi.com"
