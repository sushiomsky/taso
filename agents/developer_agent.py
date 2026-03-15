"""
TASO – Developer Agent

Generates code patches and new tools on request.
Uses the ModelRouter with deepseek-coder preference.
Coordinates with SecurityAgent for sandboxed testing before deployment.
"""
from __future__ import annotations
import hashlib
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from models.model_router import router as model_router
from models.model_registry import TaskType

log = get_logger("agent")

_DEV_SYSTEM = """You are an expert Python developer working on an autonomous security research bot.
When asked to write code or a patch:
- Write clean, well-documented, production-ready Python
- Always include error handling
- Use async/await where the codebase does
- Keep changes minimal and focused
- Include a brief summary of what changed and why at the top as a comment
"""

_PATCH_SYSTEM = """You are a code refactoring expert. Given existing code and a requested change,
produce a minimal unified diff (git diff format) that makes exactly the requested change.
Only output the diff. No explanation.
"""


class DeveloperAgent(BaseAgent):
    name = "developer"
    description = "Generates code, patches, and new tools via multi-model LLM orchestration."

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("developer.*", self._handle_dev_request)

    async def _handle_dev_request(self, msg: BusMessage) -> None:
        try:
            action = msg.payload.get("action", "generate")
            task = msg.payload.get("task", "")
            context = msg.payload.get("context", "")

            if not task:
                raise ValueError("Task description is missing in the payload.")

            if action == "generate_tool":
                result = await self._generate_tool(task)
            elif action == "generate_patch":
                result = await self._generate_patch(task, context)
            else:
                result = await self.handle(task, context)

            if msg.reply_to:
                await self.publish(
                    topic=msg.reply_to,
                    payload={"result": result, "agent": self.name, "action": action},
                    recipient=msg.sender,
                )
        except ValueError as ve:
            log.warning(f"Validation error: {ve}")
            if msg.reply_to:
                await self.publish(
                    topic=msg.reply_to,
                    payload={"error": str(ve), "agent": self.name, "action": "error"},
                    recipient=msg.sender,
                )
        except Exception as exc:
            log.exception("Unexpected error occurred while handling developer request.")
            if msg.reply_to:
                await self.publish(
                    topic=msg.reply_to,
                    payload={"error": "An unexpected error occurred. Please try again later.", "agent": self.name, "action": "error"},
                    recipient=msg.sender,
                )

    async def _generate_tool(self, task: str) -> str:
        """Generate a new dynamic tool, test it, and register if safe."""
        from tools.dynamic_tool_generator import tool_generator
        from tools.sandbox_tester import sandbox_test_tool
        from tools.base_tool import registry as tool_registry
        from self_healing.version_manager import version_manager
        from memory.version_history_db import version_history_db

        try:
            tool = await tool_generator.generate(task)
            passed, output = await sandbox_test_tool(tool.code)
            tool.test_passed = passed
            tool.test_output = output

            if passed:
                registration_success = tool_registry.register_dynamic(
                    name=tool.name,
                    code=tool.code,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    output_schema=tool.output_schema,
                    tags=tool.tags,
                    version=tool.version,
                )
                if registration_success:
                    version_manager.record(
                        author_agent=self.name,
                        change_type="tool_add",
                        description=f"Generated tool '{tool.name}': {tool.description}",
                        test_passed=True,
                        metadata={"tool_id": tool.id, "tool_name": tool.name},
                    )
                    await version_history_db.log_tool(
                        tool_name=tool.name,
                        version=tool.version,
                        action="created",
                        agent=self.name,
                        test_passed=True,
                        test_output=output,
                        code_hash=hashlib.sha256(tool.code.encode()).hexdigest()[:16],
                    )
                    return (
                        f"✅ Tool '{tool.name}' generated, tested, and registered.\n"
                        f"Description: {tool.description}\n"
                        f"Version: {tool.version} | ID: {tool.id}\n"
                        f"Test output: {output[:200]}"
                    )
                else:
                    log.error(f"Tool registration failed for '{tool.name}'.")
                    return f"❌ Tool '{tool.name}' generated but registration failed."
            else:
                await version_history_db.log_tool(
                    tool_name=tool.name,
                    version=tool.version,
                    action="tested",
                    agent=self.name,
                    test_passed=False,
                    test_output=output,
                )
                return (
                    f"❌ Tool '{tool.name}' generated but FAILED sandbox test.\n"
                    f"Error: {output[:300]}"
                )
        except Exception as exc:
            log.exception("Tool generation failed.")
            return f"❌ Tool generation failed: {exc}"

    async def _generate_patch(self, task: str, context: str) -> str:
        """Generate a code patch and return the unified diff."""
        try:
            if not task:
                raise ValueError("Task description for patch generation is missing.")
            prompt = task
            if context:
                prompt = f"Existing code:\n```python\n{context}\n```\n\nRequested change: {task}"
            return await model_router.query(
                prompt=prompt,
                system=_PATCH_SYSTEM,
                task_type=TaskType.CODING,
            )
        except ValueError as ve:
            log.warning(f"Validation error: {ve}")
            return f"❌ Patch generation failed: {ve}"
        except Exception as exc:
            log.exception("Patch generation failed.")
            return f"❌ Patch generation failed: {exc}"

    async def handle(self, description: str, context: str = "") -> str:
        """Handle a generic development task."""
        try:
            if not description:
                raise ValueError("Task description is missing.")
            prompt = description
            if context:
                prompt = f"Context:\n{context}\n\nTask: {description}"
            return await model_router.query(
                prompt=prompt,
                system=_DEV_SYSTEM,
                task_type=TaskType.CODING,
            )
        except ValueError as ve:
            log.warning(f"Validation error: {ve}")
            return f"❌ Task handling failed: {ve}"
        except Exception as exc:
            log.exception("Task handling failed.")
            return f"❌ Task handling failed: {exc}"
