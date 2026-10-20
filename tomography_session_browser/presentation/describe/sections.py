"""The context panel's text layout, shared by every description.

:class:`MetadataPanel` reads a line of the form ``Title:`` (nothing after the
colon) as a section header, and the indented lines under it as that section's
rows. Text only.
"""
from __future__ import annotations


def emit_section(blocks: list[str], title: str, rows: list[str]) -> None:
    """Append a labelled section to ``blocks`` for the context panel.

    Empty section bodies are skipped so the panel does not render orphan
    headers. A blank line separates a section from what precedes it.
    """

    if not rows:
        return
    if blocks and blocks[-1] != "":
        blocks.append("")
    blocks.append(f"{title}:")
    blocks.extend(f"  {row}" if row else "" for row in rows)
