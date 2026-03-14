"""
TASO – Tool: dependency_scanner

Checks project dependencies for known vulnerabilities using:
  • pip-audit (Python)
  • npm audit (Node.js) – if available
  • Parses requirements.txt / pyproject.toml / package.json
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

from tools.base_tool import BaseTool, ToolSchema


class DependencyScannerTool(BaseTool):
    name        = "dependency_scanner"
    description = "Scan project dependencies for known CVEs using pip-audit / npm audit."
    schema      = ToolSchema({
        "repo_path": {"type": "str", "required": True,
                      "description": "Path to the project root."},
    })

    async def execute(self, repo_path: str, **_: Any) -> Dict[str, Any]:
        path = Path(repo_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Path not found: {repo_path}")

        results: Dict[str, Any] = {"path": str(path), "scanners": {}}

        # Python audit
        req_file      = path / "requirements.txt"
        pyproject     = path / "pyproject.toml"
        if req_file.exists() or pyproject.exists():
            results["scanners"]["pip_audit"] = await self._pip_audit(path)

        # Node audit
        pkg_json = path / "package.json"
        if pkg_json.exists():
            results["scanners"]["npm_audit"] = await self._npm_audit(path)

        # Summarise
        total_vulns = 0
        for scanner, data in results["scanners"].items():
            total_vulns += len(data.get("vulnerabilities", []))

        results["total_vulnerabilities"] = total_vulns
        results["status"] = "clean" if total_vulns == 0 else "vulnerable"
        return results

    # ------------------------------------------------------------------

    async def _pip_audit(self, path: Path) -> Dict[str, Any]:
        """Run pip-audit for Python dependency auditing."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pip-audit", "--format", "json", "--progress-spinner", "off",
                cwd=str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            try:
                data = json.loads(stdout.decode())
            except json.JSONDecodeError:
                return {"error": stdout.decode()[:500], "vulnerabilities": []}

            # pip-audit returns {"dependencies": [...]}
            vulns: List[Dict] = []
            for dep in data.get("dependencies", []):
                for v in dep.get("vulns", []):
                    vulns.append({
                        "package":    dep.get("name"),
                        "version":    dep.get("version"),
                        "vuln_id":    v.get("id"),
                        "fix_versions": v.get("fix_versions", []),
                        "description": v.get("description", ""),
                    })

            return {"vulnerabilities": vulns, "tool": "pip-audit"}

        except FileNotFoundError:
            # Try safety as fallback
            return await self._safety_check(path)
        except asyncio.TimeoutError:
            return {"error": "pip-audit timed out", "vulnerabilities": []}
        except Exception as exc:
            return {"error": str(exc), "vulnerabilities": []}

    async def _safety_check(self, path: Path) -> Dict[str, Any]:
        """Fallback: use safety check."""
        req_file = path / "requirements.txt"
        if not req_file.exists():
            return {"error": "requirements.txt not found", "vulnerabilities": []}
        try:
            proc = await asyncio.create_subprocess_exec(
                "safety", "check", "-r", str(req_file), "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            data = json.loads(stdout.decode())

            vulns = []
            for item in (data if isinstance(data, list) else []):
                vulns.append({
                    "package":    item[0] if len(item) > 0 else "",
                    "version":    item[2] if len(item) > 2 else "",
                    "vuln_id":    item[4] if len(item) > 4 else "",
                    "description": item[3] if len(item) > 3 else "",
                })
            return {"vulnerabilities": vulns, "tool": "safety"}
        except FileNotFoundError:
            return {"error": "neither pip-audit nor safety installed", "vulnerabilities": []}
        except Exception as exc:
            return {"error": str(exc), "vulnerabilities": []}

    async def _npm_audit(self, path: Path) -> Dict[str, Any]:
        """Run npm audit --json."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "audit", "--json",
                cwd=str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            try:
                data = json.loads(stdout.decode())
            except json.JSONDecodeError:
                return {"error": "npm audit JSON parse error", "vulnerabilities": []}

            vulns: List[Dict] = []
            for name, vuln in data.get("vulnerabilities", {}).items():
                vulns.append({
                    "package":   name,
                    "severity":  vuln.get("severity", ""),
                    "via":       vuln.get("via", []),
                    "fixAvailable": vuln.get("fixAvailable", False),
                })

            return {
                "vulnerabilities": vulns,
                "metadata":        data.get("metadata", {}),
                "tool":            "npm-audit",
            }
        except FileNotFoundError:
            return {"error": "npm not installed", "vulnerabilities": []}
        except Exception as exc:
            return {"error": str(exc), "vulnerabilities": []}
