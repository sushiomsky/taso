"""
TASO – Agent Orchestrator

Central startup and lifecycle manager.

Responsibilities:
  • Instantiate and start all agents
  • Start the message bus
  • Start the Telegram bot
  • Connect to the knowledge database and conversation store
  • Load the vector store
  • Register the tool registry
  • Gracefully shut down all components on SIGINT/SIGTERM
"""

from __future__ import annotations

import asyncio
import signal
from typing import List

from config.logging_config import init_logging, get_logger
from config.settings import settings

log = get_logger("agent")


class Orchestrator:
    """Top-level coordinator for all TASO subsystems."""

    def __init__(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Main entry point – initialise everything and run until shutdown."""
        init_logging()
        log.info("=" * 60)
        log.info("TASO – Telegram Autonomous Security Operator")
        log.info(f"Environment: {settings.APP_ENV}")
        log.info(f"LLM backend: {settings.LLM_BACKEND} / {self._llm_model()}")
        log.info("=" * 60)

        # ------------------------------------------------------------------
        # 1. Message bus
        # ------------------------------------------------------------------
        from agents.message_bus import bus
        await bus.start()
        log.info("Message bus started.")

        # ------------------------------------------------------------------
        # 2. Memory subsystem
        # ------------------------------------------------------------------
        from memory.knowledge_db import KnowledgeDB
        from memory.vector_store import VectorStore
        from memory.conversation_store import ConversationStore

        db         = KnowledgeDB()
        vector     = VectorStore()
        conv_store = ConversationStore()

        await db.connect()
        await conv_store.connect()
        vector.load()
        log.info("Memory subsystem ready.")

        # ------------------------------------------------------------------
        # 3. Tool registry
        # ------------------------------------------------------------------
        from tools.base_tool import registry as tool_registry
        tool_registry.discover()

        # ------------------------------------------------------------------
        # 4. Agents
        # ------------------------------------------------------------------
        from agents.coordinator_agent import CoordinatorAgent
        from agents.security_agent    import SecurityAnalysisAgent
        from agents.research_agent    import ResearchAgent
        from agents.dev_agent         import DevAgent
        from agents.memory_agent      import MemoryAgent
        from agents.system_agent      import SystemAgent
        from agents.planner_agent     import PlannerAgent
        from agents.coder_agent       import CoderAgent
        from agents.analysis_agent    import AnalysisAgent

        coordinator = CoordinatorAgent(bus)
        security    = SecurityAnalysisAgent(bus)
        research    = ResearchAgent(bus)
        dev         = DevAgent(bus)
        memory_agent = MemoryAgent(bus, db, vector, conv_store)
        system      = SystemAgent(bus)
        planner     = PlannerAgent(bus)
        coder       = CoderAgent(bus)
        analysis    = AnalysisAgent(bus)

        agents = [coordinator, security, research, dev, memory_agent, system, planner, coder, analysis]
        for agent in agents:
            await agent.start()
            log.info(f"Agent started: {agent.name}")

        # ----- New specialized agents -----
        from agents.developer_agent    import DeveloperAgent
        from agents.self_healing_agent import SelfHealingAgent
        new_agents = [DeveloperAgent(bus), SelfHealingAgent(bus)]
        for agent in new_agents:
            await agent.start()
            agents.append(agent)
            log.info(f"Agent started: {agent.name}")

        # ------------------------------------------------------------------
        # 5. Self-improvement engine (if enabled)
        # ------------------------------------------------------------------
        if settings.SELF_IMPROVE_ENABLED:
            asyncio.create_task(
                self._self_improve_loop(coordinator, db)
            )
            log.info("Self-improvement loop scheduled.")

        # ------------------------------------------------------------------
        # 5b. Swarm system initialisation
        # ------------------------------------------------------------------
        if settings.SWARM_ENABLED:
            from swarm.agent_registry import agent_registry, register_default_agents
            from swarm.swarm_orchestrator import swarm_orchestrator

            # Register all running agents into the swarm registry
            agent_map = {a.name: a for a in agents}
            register_default_agents(agent_map)
            log.info(f"Swarm registry: {len(agents)} agents registered.")

        # ------------------------------------------------------------------
        # 5c. Self-healing bootstrap
        # ------------------------------------------------------------------
        try:
            from self_healing.deploy_manager import deploy_manager
            from memory.version_history_db import version_history_db
            await version_history_db.connect()
            await deploy_manager.bootstrap()
            log.info("Self-healing: bootstrap complete.")
        except Exception as exc:
            log.warning(f"Self-healing bootstrap error (non-fatal): {exc}")

        # ------------------------------------------------------------------
        # 6. Telegram bot
        # ------------------------------------------------------------------
        from bot.telegram_bot import TelegramBot

        bot = TelegramBot(
            bus         = bus,
            coordinator = coordinator,
            conv_store  = conv_store,
            tool_registry = tool_registry,
        )
        await bot.start()

        # ------------------------------------------------------------------
        # 7. Wait for shutdown signal
        # ------------------------------------------------------------------
        self._running = True
        log.info("TASO fully operational. Waiting for shutdown signal.")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _shutdown(*_):
            log.info("Shutdown signal received.")
            loop.call_soon_threadsafe(stop_event.set)

        # Use asyncio-safe signal handling (avoids conflicts with PTB's event loop)
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Fallback for environments that don't support add_signal_handler
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, _shutdown)

        await stop_event.wait()

        # ------------------------------------------------------------------
        # 8. Graceful shutdown
        # ------------------------------------------------------------------
        log.info("Shutting down TASO…")
        await bot.stop()

        for agent in reversed(agents):
            await agent.stop()

        await bus.stop()
        await db.close()
        await conv_store.close()

        log.info("TASO shutdown complete.")

    # ------------------------------------------------------------------
    # Self-improvement background loop
    # ------------------------------------------------------------------

    async def _self_improve_loop(self, coordinator, db) -> None:
        """Periodically analyse the codebase and propose improvements."""
        from self_improvement.code_analyzer  import CodeAnalyzer
        from self_improvement.patch_generator import PatchGenerator
        from self_improvement.auto_deployer   import AutoDeployer

        interval = 3600  # run once per hour

        await asyncio.sleep(300)  # initial delay on startup

        while True:
            try:
                log.info("Self-improvement loop: starting analysis cycle.")
                analyser   = CodeAnalyzer()
                generator  = PatchGenerator(llm_callable=coordinator.llm_query)
                deployer   = AutoDeployer(audit_callable=db.audit)

                results    = analyser.analyse_repo(max_files=50)

                for result in results[:3]:   # max 3 files per cycle
                    fpath = result["file"]
                    issues = result["findings"]

                    from pathlib import Path
                    proposal = await generator.generate_for_file(
                        Path(fpath), issues=issues
                    )
                    if proposal:
                        deploy_result = await deployer.evaluate_and_deploy(proposal)
                        log.info(f"Self-improve: {deploy_result.summary()}")

            except Exception as exc:
                log.error(f"Self-improvement loop error: {exc}")

            await asyncio.sleep(interval)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _llm_model() -> str:
        if settings.LLM_BACKEND == "ollama":
            return settings.OLLAMA_MODEL
        elif settings.LLM_BACKEND == "openai":
            return settings.OPENAI_MODEL
        elif settings.LLM_BACKEND == "anthropic":
            return settings.ANTHROPIC_MODEL
        return "unknown"
