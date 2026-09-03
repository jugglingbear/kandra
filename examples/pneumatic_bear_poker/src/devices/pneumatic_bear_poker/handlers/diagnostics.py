"""Diagnostics: download the device's rolling log buffer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DownloadLogsRequest:
    """Args for `logs.download`."""

    max_lines: int = 1000
    """Cap on the number of log lines the device should return."""

    since_sequence: int | None = None
    """If set, only lines with sequence numbers >= this value are returned."""


@dataclass(frozen=True)
class DownloadLogsResponse:
    """Reply from `logs.download`."""

    lines: tuple[str, ...]
    next_sequence: int


class DownloadLogs:
    """Pull the device's rolling log buffer over HTTP."""

    request = DownloadLogsRequest
    response = DownloadLogsResponse
