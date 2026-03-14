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
        try:
            init_logging()
            self._log_startup_info()

            # Initialize subsystems
            bus = await self._start_message_bus()
            db, vector, conv_store = await self._initialize_memory_subsystem()
            tool_registry = self._initialize_tool_registry()
            agents = await self._start_agents(bus, db, vector, conv_store)
            await self._initialize_optional_features(agents, bus, db, conv_store)
            bot = await self._start_telegram_bot(bus, agents, tool_registry, conv_store)

            # Wait for shutdown signal
            await self._wait_for_shutdown()

            # Perform graceful shutdown
            await self._shutdown(bot, agents, bus, db, conv_store)

        except Exception as exc:
            log.error(f"Critical error during orchestrator run: {exc}", exc_info=True)
            raise

    def _log_startup_info(self) -> None:
        """Log startup information."""
        log.info("=" * 60)
        log.info("TASO – Telegram Autonomous Security Operator")
        log.info(f"Environment: {settings.APP_ENV}")
        log.info(f"LLM backend: {settings.LLM_BACKEND} / {self._llm_model()}")
        log.info("=" * 60)

    async def _start_message_bus(self):
        """Start the message bus."""
        from agents.message_bus import bus
        await bus.start()
        log.info("Message bus started.")
        return bus

    async def _initialize_memory_subsystem(self):
        """Initialize the memory subsystem."""
        from memory.knowledge_db import KnowledgeDB
        from memory.vector_store import VectorStore
        from memory.conversation_store import ConversationStore

        db = KnowledgeDB()
        vector = VectorStore()
        conv_store = ConversationStore()

        await db.connect()
        await conv_store.connect()
        vector.load()
        log.info("Memory subsystem ready.")
        return db, vector, conv_store

    def _initialize_tool_registry(self):
        """Initialize the tool registry."""
        from tools.base_tool import registry as tool_registry
        tool_registry.discover()
        return tool_registry

    async def _start_agents(self, bus, db, vector, conv_store):
        """Start all agents."""
        from agents.coordinator_agent import CoordinatorAgent
        from agents.security_agent import SecurityAnalysisAgent
        from agents.research_agent import ResearchAgent
        from agents.dev_agent import DevAgent
        from agents.memory_agent import MemoryAgent
        from agents.system_agent import SystemAgent
        from agents.planner_agent import PlannerAgent
        from agents.coder_agent import CoderAgent
        from agents.analysis_agent import AnalysisAgent
        from agents.developer_agent import DeveloperAgent
        from agents.self_healing_agent import SelfHealingAgent

        agent_classes = [
            CoordinatorAgent, SecurityAnalysisAgent, ResearchAgent, DevAgent,
            MemoryAgent, SystemAgent, PlannerAgent, CoderAgent, AnalysisAgent,
            DeveloperAgent, SelfHealingAgent
        ]

        agents = []
        for agent_class in agent_classes:
            try:
                if agent_class == MemoryAgent:
                    agent = agent_class(bus, db, vector, conv_store)
                else:
                    agent = agent_class(bus)
                await agent.start()
                agents.append(agent)
                log.info(f"Agent started: {agent.name}")
            except Exception as exc:
                log.error(f"Failed to start agent {agent_class.__name__}: {exc}", exc_info=True)

        return agents

    async def _initialize_optional_features(self, agents, bus, db, conv_store):
        """Initialize optional features like self-improvement, swarm, and self-healing."""
        if settings.SELF_IMPROVE_ENABLED:
            asyncio.create_task(self._self_improve_loop(agents[0], db))
            log.info("Self-improvement loop scheduled.")

        if settings.SWARM_ENABLED:
            try:
                from swarm.agent_registry import register_default_agents
                agent_map = {a.name: a for a in agents}
                register_default_agents(agent_map)
                log.info(f"Swarm registry: {len(agents)} agents registered.")
            except Exception as exc:
                log.error(f"Error initializing swarm system: {exc}", exc_info=True)

        try:
            from self_healing.deploy_manager import deploy_manager
            from memory.version_history_db import version_history_db
            await version_history_db.connect()
            await deploy_manager.bootstrap()
            log.info("Self-healing: bootstrap complete.")
        except Exception as exc:
            log.warning(f"Self-healing bootstrap error (non-fatal): {exc}", exc_info=True)

    async def _start_telegram_bot(self, bus, agents, tool_registry, conv_store):
        """Start the Telegram bot."""
        from bot.telegram_bot import TelegramBot

        coordinator = next((a for a in agents if a.__class__.__name__ == "CoordinatorAgent"), None)
        if not coordinator:
            raise RuntimeError("CoordinatorAgent not found among agents.")

        bot = TelegramBot(
            bus=bus,
            coordinator=coordinator,
            conv_store=conv_store,
            tool_registry=tool_registry,
        )
        await bot.start()
        return bot

    async def _wait_for_shutdown(self):
        """Wait for a shutdown signal."""
        self._running = True
        log.info("TASO fully operational. Waiting for shutdown signal.")

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _shutdown(*_):
            log.info("Shutdown signal received.")
            loop.call_soon_threadsafe(stop_event.set)

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, _shutdown)

        await stop_event.wait()

    async def _shutdown(self, bot, agents, bus, db, conv_store):
        """Perform a graceful shutdown of all components."""
        log.info("Shutting down TASO…")
        try:
            await bot.stop()
        except Exception as exc:
            log.error(f"Error stopping Telegram bot: {exc}", exc_info=True)

        for agent in reversed(agents):
            try:
                await agent.stop()
            except Exception as exc:
                log.error(f"Error stopping agent {agent.name}: {exc}", exc_info=True)

        try:
            await bus.stop()
        except Exception as exc:
            log.error(f"Error stopping message bus: {exc}", exc_info=True)

        try:
            await db.close()
        except Exception as exc:
            log.error(f"Error closing knowledge database: {exc}", exc_info=True)

        try:
            await conv_store.close()
        except Exception as exc:
            log.error(f"Error closing conversation store: {exc}", exc_info=True)

        log.info("TASO shutdown complete.")

    async def _self_improve_loop(self, coordinator, db) -> None:
        """Periodically analyse the codebase and propose improvements."""
        from self_improvement.code_analyzer import CodeAnalyzer
        from self_improvement.patch_generator import PatchGenerator
        from self_improvement.auto_deployer import AutoDeployer

        interval = 3600  # run once per hour

        await asyncio.sleep(300)  # initial delay on startup

        while True:
            try:
                log.info("Self-improvement loop: starting analysis cycle.")
                analyser = CodeAnalyzer()
                generator = PatchGenerator(llm_callable=coordinator.llm_query)
                deployer = AutoDeployer(audit_callable=db.audit)

                results = analyser.analyse_repo(max_files=50)

                for result in results[:3]:  # max 3 files per cycle
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
                log.error(f"Self-improvement loop error: {exc}", exc_info=True)

            await asyncio.sleep(interval)

    @staticmethod
    def _llm_model() -> str:
        """Get the LLM model name based on the backend."""
        llm_models = {
            "ollama": settings.OLLAMA_MODEL,
            "openai": settings.OPENAI_MODEL,
            "anthropic": settings.ANTHROPIC_MODEL,
        }
        return llm_models.get(settings.LLM_BACKEND, "unknown")
