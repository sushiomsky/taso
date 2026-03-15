"""
TASO – Log Monitor Tool

Scans log files for error patterns and returns a summary of recent errors.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

from tools.base_tool import BaseTool, ToolSchema
from config.logging_config import tool_log as log


class LogMonitorTool(BaseTool):
    name = "log_monitor"
    description = "Scans log files for error patterns and returns a summary of recent errors"
    schema = ToolSchema({
        "log_file":     {"type": "str", "required": False, "default": "logs/agent.log"},
        "lines":        {"type": "int", "required": False, "default": 200},
        "min_severity": {"type": "str", "required": False, "default": "ERROR"},
    })

    # loguru default format: 2024-01-01 12:00:00.000 | LEVEL    | module:func:line – message
    _LOG_RE = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s*\|\s*"
        r"(?P<level>\w+)\s*\|\s*(?P<module>[^:]+):(?P<func>[^:]+):(?P<lineno>\d+)\s*[-–]\s*"
        r"(?P<message>.+)$"
    )

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        log_file: str = kwargs.get("log_file", "logs/agent.log")
        lines: int = int(kwargs.get("lines", 200))
        min_severity: str = str(kwargs.get("min_severity", "ERROR")).upper()

        from config.settings import settings
        path = Path(log_file)
        if not path.is_absolute():
            path = settings.LOG_DIR.parent / log_file

        if not path.exists():
            return {
                "total_errors": 0,
                "total_warnings": 0,
                "top_errors": [],
                "recent_errors": [],
                "summary": f"Log file not found: {path}",
            }

        # Read last N lines efficiently
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
            tail = all_lines[-lines:]
        except Exception as exc:
            return {
                "total_errors": 0,
                "total_warnings": 0,
                "top_errors": [],
                "recent_errors": [],
                "summary": f"Error reading log file: {exc}",
            }

        severities_to_collect = {"ERROR", "CRITICAL"}
        if min_severity == "WARNING":
            severities_to_collect.add("WARNING")

        errors_by_module: Counter = Counter()
        recent_errors: List[Dict] = []
        total_errors = 0
        total_warnings = 0

        for raw_line in tail:
            raw_line = raw_line.rstrip()
            m = self._LOG_RE.match(raw_line)
            if not m:
                continue
            level = m.group("level").upper()
            if level in ("ERROR", "CRITICAL"):
                total_errors += 1
                module = m.group("module").strip()
                errors_by_module[module] += 1
                recent_errors.append({
                    "ts":      m.group("ts"),
                    "level":   level,
                    "module":  module,
                    "func":    m.group("func").strip(),
                    "message": m.group("message").strip()[:200],
                })
            elif level == "WARNING":
                total_warnings += 1
                if min_severity == "WARNING":
                    module = m.group("module").strip()
                    errors_by_module[module] += 1
                    recent_errors.append({
                        "ts":      m.group("ts"),
                        "level":   level,
                        "module":  module,
                        "func":    m.group("func").strip(),
                        "message": m.group("message").strip()[:200],
                    })

        top_errors = [
            {"module": mod, "count": cnt}
            for mod, cnt in errors_by_module.most_common(5)
        ]

        last_5 = recent_errors[-5:]

        if total_errors == 0 and total_warnings == 0:
            summary = f"No errors or warnings found in last {lines} log lines."
        else:
            top_str = ", ".join(f"{e['module']}({e['count']})" for e in top_errors)
            summary = (
                f"{total_errors} error(s), {total_warnings} warning(s) "
                f"in last {lines} lines. Top modules: {top_str or 'none'}."
            )

        return {
            "total_errors":   total_errors,
            "total_warnings": total_warnings,
            "top_errors":     top_errors,
            "recent_errors":  last_5,
            "summary":        summary,
        }
