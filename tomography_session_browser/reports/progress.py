"""Report progress stages, importable without reportlab.

The window imports these at start-up; ``report_generator`` imports reportlab
and is imported only when a report job starts.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

SLICES, WRITING = "slices", "writing"
#: progress(stage, done, total): counted slices, then the PDF is being written.
ReportProgress = Callable[[Literal["slices", "writing"], int, int], None]
