"""anchors.py — split extracted text into [BLOCK_n] paragraph/clause blocks.

Each block keeps its character offsets into the source text so a future
viewer can highlight the exact span, and so `block_ids` in a finding
(CONTRACTS.md §2) can be traced back to source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARAGRAPH_RE = re.compile(r"[^\n]+(?:\n[^\n]+)*")


@dataclass
class Block:
    id: str
    start: int
    end: int
    text: str


def make_anchors(text: str) -> list[Block]:
    """Split `text` on blank-line boundaries into sequential BLOCK_1..BLOCK_n blocks.

    Offsets are computed so that `text[block.start:block.end] == block.text`
    exactly. Whitespace-only chunks are skipped and do not consume an id.
    """
    blocks: list[Block] = []
    n = 0
    for match in _PARAGRAPH_RE.finditer(text):
        raw = match.group()
        content = raw.strip()
        if not content:
            continue
        n += 1
        lstrip_len = len(raw) - len(raw.lstrip())
        rstrip_len = len(raw) - len(raw.rstrip())
        start = match.start() + lstrip_len
        end = match.end() - rstrip_len
        blocks.append(Block(id=f"BLOCK_{n}", start=start, end=end, text=content))
    return blocks
