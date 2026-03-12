from dataclasses import dataclass
from typing import Optional


@dataclass
class VlessItem:
    index: int
    url: str
    parsed: dict
    status: str = "PENDING"
    latency_ms: Optional[int] = None
    exit_ip: str = ""
    reason: str = ""
    partial: bool = False
    xray_error: str = ""
