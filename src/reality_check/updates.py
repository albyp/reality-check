"""Update notice: is a newer RealityCheck release on GitHub?

At start the GUI asks GitHub for the latest release, in the background with a
short timeout. If it is newer than this version, and the user has not chosen
to skip it, the GUI shows a notice with a link to the release page. Nothing is
downloaded or installed: the user downloads the zip as usual. (An exe that
replaces itself is likely to be blocked by SmartScreen or antivirus while
RealityCheck is not code-signed.)

No network, a proxy that blocks GitHub or any other failure means no notice.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from reality_check import __version__

REPO = "albyp/reality-check"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 5  # s
log = logging.getLogger("reality_check")


@dataclass
class Release:
    version: str  # "0.4.0"
    url: str  # release page
    notes: str = ""  # release notes (Markdown)
    published: str = ""  # ISO date


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.3.1' -> (0, 3, 1, 1, 0); '0.4.0.dev2' -> (0, 4, 0, 0, 2), which sorts before 0.4.0.

    Anything that is not a version sorts first, so it is never "newer".
    """
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:\.?dev(\d+))?", v.strip())
    if not m:
        return (-1,)
    major, minor, patch, dev = m.groups()
    final = 0 if dev is not None else 1
    return int(major), int(minor), int(patch or 0), final, int(dev or 0)


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def fetch_latest(timeout: float = TIMEOUT) -> Release | None:
    req = urllib.request.Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"RealityCheck/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - offline, proxy, rate limit: no notice
        log.info("Update check skipped: %s", e)
        return None
    tag = str(d.get("tag_name", ""))
    if parse_version(tag) == (-1,) or d.get("draft") or d.get("prerelease"):
        return None
    return Release(tag.lstrip("v"), d.get("html_url") or RELEASES_PAGE, d.get("body") or "",
                   (d.get("published_at") or "")[:10])


def available_update(enabled: bool, skipped: str, current: str = __version__,
                     fetch: Callable[[], Release | None] = fetch_latest) -> Release | None:
    """The release to tell the user about, or None."""
    if not enabled:
        return None
    rel = fetch()
    if rel is None or not is_newer(rel.version, current) or rel.version == skipped.lstrip("v"):
        return None
    return rel


def summary(notes: str, lines: int = 8) -> str:
    """The first headings and bullet points of the release notes, as plain text.

    A bullet wrapped over several lines (as in the changelog) is joined into one.
    """
    out: list[str] = []
    for line in notes.splitlines():
        s = line.strip()
        if s.startswith("### "):
            out.append(s[4:].upper())
        elif s.startswith("- "):
            out.append("• " + s[2:])
        elif s and line.startswith("  ") and out and out[-1].startswith("•"):
            out[-1] += " " + s
    out = [re.sub(r"[*_`]", "", s) for s in out[:lines]]
    return "\n".join(out)
