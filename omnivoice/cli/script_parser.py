"""Parse multi-speaker script files for OmniVoice demo.

Expected line format (tab- or multi-space separated):

    #1\tSome line of dialog here.
    #2\tAnother speaker.

Blank lines and lines starting with '#' (other than the speaker tag)
are ignored.
"""

import re
from typing import Dict, List

# Match "#1", "#12" (digits), optionally followed by separator + text.
_LINE_RE = re.compile(r"^#(\d+)\s*[\t ]+(.+?)\s*$")


def parse_script(text: str) -> List[Dict]:
    """Parse script text into ordered list of {speaker, text}.

    Returns:
        list of {"speaker": int, "text": str}
    """
    if not text:
        return []
    out: List[Dict] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        out.append({"speaker": int(m.group(1)), "text": m.group(2)})
    return out


def unique_speakers(items: List[Dict]) -> List[int]:
    """Return sorted unique speaker ids preserving first-seen order."""
    seen: List[int] = []
    for it in items:
        sp = it["speaker"]
        if sp not in seen:
            seen.append(sp)
    return seen


def speaker_count(items: List[Dict]) -> int:
    return len(set(it["speaker"] for it in items))
