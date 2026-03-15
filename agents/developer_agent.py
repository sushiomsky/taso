"""
TASO – Developer Agent

Generates code patches and new tools on request.
Uses the ModelRouter with deepseek-coder preference.
Coordinates with SecurityAgent for sandboxed testing before deployment.
"""
from __future__ import annotations
import hashlib
import re
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import get_logger
from config.settings import settings
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
        self._bus.subscribe("developer.*",      self._handle_dev_request)
        self._bus.subscribe("developer.create_agent", self._handle_create_agent)

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

    # ------------------------------------------------------------------
    # Autonomous agent creation
    # ------------------------------------------------------------------

    _AGENT_SYSTEM = textwrap.dedent("""\
        You are an expert Python developer specialising in autonomous AI agent systems.
        Generate a complete, working Python module for a new TASO agent.

        Requirements:
        - Subclass BaseAgent from agents.base_agent
        - Implement _register_subscriptions() to subscribe to relevant bus topics
        - Implement handle(description, context) for swarm-callable interface
        - Use async/await throughout
        - Use loguru logger: from config.logging_config import get_logger; log = get_logger("agent")
        - No placeholders – fully working code only
        - Include docstring explaining what the agent does

        Output ONLY the Python module code. No explanation. No markdown fences.
    """)

    async def _handle_create_agent(self, msg: BusMessage) -> None:
        """Handle developer.create_agent bus messages."""
        description = msg.payload.get("description", "")
        agent_name  = msg.payload.get("agent_name", "")
        result = await self.create_agent(description, agent_name)
        if msg.reply_to:
            await self.publish(
                topic=msg.reply_to,
                payload={"result": result, "agent": self.name, "action": "create_agent"},
                recipient=msg.sender,
            )

    async def create_agent(self, description: str, agent_name: str = "") -> str:
        """
        Autonomously generate, test, and register a new agent.

        1. LLM generates agent code from description.
        2. Syntax-check the code.
        3. Save to agents/<name>.py.
        4. Attempt to import and register in the swarm AgentRegistry.
        5. Log result in AuditLog.
        """
        from self_healing.test_runner import TestRunner
        from memory.audit_log import audit_log

        if not description:
            return "❌ Agent description is required."

        # Derive a snake_case module name if not given
        if not agent_name:
            words = re.sub(r"[^a-zA-Z0-9 ]", "", description).lower().split()[:3]
            agent_name = "_".join(words) + "_agent"

        module_name = agent_name.lower().replace(" ", "_").replace("-", "_")
        if not module_name.endswith("_agent"):
            module_name += "_agent"
        class_name = "".join(w.capitalize() for w in module_name.split("_"))

        log.info(f"DeveloperAgent: generating agent '{class_name}' ({module_name})")

        prompt = (
            f"Create a new TASO agent with the following purpose:\n\n"
            f"{description}\n\n"
            f"The class name must be exactly: {class_name}\n"
            f"The module-level `name` attribute must be: \"{module_name.replace('_agent','')}\"\n"
            f"The module-level `description` attribute must describe what the agent does.\n"
        )

        try:
            code = await model_router.query(
                prompt=prompt,
                system=self._AGENT_SYSTEM,
                task_type=TaskType.CODING,
            )

            # Strip markdown fences if LLM wrapped the code
            code = re.sub(r"^```python\s*", "", code.strip())
            code = re.sub(r"```\s*$", "", code.strip())

            # Syntax check
            runner = TestRunner()
            ok, errors = runner.syntax_check_code(code)
            if not ok:
                await audit_log.record(
                    agent=self.name, action="create_agent",
                    input_summary=description,
                    output_summary=f"Syntax errors: {errors[:3]}",
                    success=False,
                )
                return f"❌ Generated agent code has syntax errors:\n" + "\n".join(errors[:3])

            # Save to agents/
            agents_dir = Path(settings.BASE_DIR) / "agents"
            target     = agents_dir / f"{module_name}.py"

            if target.exists():
                return (
                    f"⚠️ Agent module '{module_name}.py' already exists. "
                    "Use /dev_patch to modify it."
                )

            target.write_text(code, encoding="utf-8")
            log.info(f"DeveloperAgent: saved {target}")

            # Attempt dynamic import + swarm registration
            registered = False
            try:
                import importlib
                mod = importlib.import_module(f"agents.{module_name}")
                agent_cls = getattr(mod, class_name, None)
                if agent_cls is None:
                    log.warning(
                        f"Class '{class_name}' not found in generated module; "
                        "skipping registry."
                    )
                else:
                    from agents.message_bus import bus
                    from swarm.agent_registry import agent_registry
                    instance = agent_cls(bus)
                    await instance.start()
                    agent_registry.register(
                        name=instance.name,
                        agent=instance,
                        capabilities={instance.description},
                    )
                    registered = True
                    log.info(f"DeveloperAgent: agent '{instance.name}' live in swarm.")
            except Exception as exc:
                log.warning(f"Could not register agent dynamically: {exc}")

            await audit_log.record(
                agent=self.name, action="create_agent",
                input_summary=description,
                output_summary=f"Created {module_name}.py, registered={registered}",
                success=True,
                metadata={"module": module_name, "class": class_name},
            )

            return (
                f"✅ Agent '{class_name}' created.\n"
                f"Module: agents/{module_name}.py\n"
                f"Swarm registration: {'✅ live' if registered else '⚠️ saved but not live (restart to load)'}"
            )

        except Exception as exc:
            log.exception("create_agent failed.")
            await audit_log.record(
                agent=self.name, action="create_agent",
                input_summary=description,
                output_summary=str(exc)[:200],
                success=False,
            )
            return f"❌ Agent creation failed: {exc}"
