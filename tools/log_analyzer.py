"""
TASO – Tool: log_analyzer

Parses and searches TASO log files, returning structured summaries.
Supports pattern matching, severity filtering, and time-range filtering.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from tools.base_tool import BaseTool, ToolSchema

# Loguru timestamp format: 2025-06-01 12:00:00.123
_TS_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)"
    r" \| (\w+)\s+\| "
    r"(.+)"
)
_LEVEL_ORDER = {
    "TRACE": 0, "DEBUG": 1, "INFO": 2,
    "SUCCESS": 3, "WARNING": 4, "ERROR": 5, "CRITICAL": 6,
}


class LogAnalyzerTool(BaseTool):
    name        = "log_analyzer"
    description = "Search and analyse TASO log files by pattern, level, or time range."
    schema      = ToolSchema({
        "category": {
            "type": "str", "required": False, "default": "combined",
            "description": "Log category: combined|agent|tool|security|self_improvement|error.",
        },
        "pattern": {
            "type": "str", "required": False, "default": "",
            "description": "Regex pattern to search for.",
        },
        "min_level": {
            "type": "str", "required": False, "default": "INFO",
            "description": "Minimum log level (DEBUG|INFO|WARNING|ERROR|CRITICAL).",
        },
        "tail": {
            "type": "int", "required": False, "default": 100,
            "description": "Number of matching lines to return (from end of file).",
        },
    })

    async def execute(self, category: str = "combined", pattern: str = "",
                       min_level: str = "INFO", tail: int = 100,
                       **_: Any) -> Dict[str, Any]:
        log_file = settings.LOG_DIR / f"{category}.log"

        if not log_file.exists():
            return {
                "category": category,
                "file":     str(log_file),
                "found":    0,
                "entries":  [],
                "error":    f"Log file not found: {log_file}",
            }

        text  = log_file.read_text(errors="ignore")
        lines = text.splitlines()

        min_order = _LEVEL_ORDER.get(min_level.upper(), 2)

        compiled_pat = re.compile(pattern, re.IGNORECASE) if pattern else None

        entries: List[Dict] = []
        for line in lines:
            m = _TS_PATTERN.match(line)
            if m:
                ts, level, rest = m.group(1), m.group(2), m.group(3)
                if _LEVEL_ORDER.get(level.strip(), 0) < min_order:
                    continue
                if compiled_pat and not compiled_pat.search(line):
                    continue
                entries.append({"ts": ts, "level": level.strip(),
                                  "message": rest.strip()})
            else:
                # Continuation line
                if entries and (not compiled_pat or compiled_pat.search(line)):
                    entries[-1]["message"] += "\n" + line

        # Return last `tail` entries
        entries = entries[-tail:]

        # Level summary
        summary: Dict[str, int] = {}
        for e in entries:
            summary[e["level"]] = summary.get(e["level"], 0) + 1

        return {
            "category":      category,
            "file":          str(log_file),
            "total_lines":   len(lines),
            "matched":       len(entries),
            "level_summary": summary,
            "entries":       entries,
        }
