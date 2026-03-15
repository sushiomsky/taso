"""
TASO – Telegram Control Bot

Production-grade async Telegram bot using python-telegram-bot v20+.

Features:
  • Admin-only authentication on sensitive commands
  • Full command set mapped to the agent orchestrator
  • Inline keyboards for interactive responses
  • Message length splitting (Telegram 4096 char limit)
  • Rate limiting per chat
  • Conversation context stored per chat
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agents.message_bus import BusMessage, MessageBus
from agents.coordinator_agent import CoordinatorAgent
from memory.conversation_store import ConversationStore
from config.settings import settings
from config.logging_config import get_logger

log = get_logger("agent")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple token-bucket rate limiter per chat_id."""

    def __init__(self, rate: int = 10, per: float = 60.0) -> None:
        self._rate    = rate
        self._per     = per
        self._buckets: Dict[int, List[float]] = {}

    def is_allowed(self, chat_id: int) -> bool:
        now = time.time()
        bucket = self._buckets.setdefault(chat_id, [])
        # Remove timestamps older than the window
        self._buckets[chat_id] = [t for t in bucket if now - t < self._per]
        if len(self._buckets[chat_id]) >= self._rate:
            return False
        self._buckets[chat_id].append(now)
        return True


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class TelegramBot:
    """TASO Telegram control interface."""

    _COMMANDS = [
        BotCommand("start",          "Welcome and authenticate"),
        BotCommand("status",         "System and agent status"),
        BotCommand("agents",         "List all agents"),
        BotCommand("memory",         "Query the knowledge base"),
        BotCommand("scan_repo",      "Scan a Git repository"),
        BotCommand("security_scan",  "Run full security scan"),
        BotCommand("code_audit",     "Audit a code snippet"),
        BotCommand("threat_intel",   "Collect threat intelligence"),
        BotCommand("update_self",    "Propose self-improvement patches"),
        BotCommand("tools",          "List available tools"),
        BotCommand("logs",           "View recent logs"),
        BotCommand("system",         "Host system metrics"),
        BotCommand("swarm_status",   "Show swarm execution status"),
        BotCommand("swarm_agents",   "List all registered agents and load"),
        BotCommand("swarm_models",   "Show model registry and routing"),
        BotCommand("run_swarm_task", "Execute a task via the agent swarm"),
        BotCommand("model_router",   "Show model routing configuration"),
        BotCommand("system_status",  "Full system status overview"),
        BotCommand("help",           "Show all commands"),
        BotCommand("dev_status",     "Development system overview"),
        BotCommand("dev_task",       "Submit a development task to the swarm"),
        BotCommand("dev_tool",       "Request dynamic tool creation"),
        BotCommand("dev_patch",      "Request code patch generation"),
        BotCommand("dev_review",     "Review latest code change"),
        BotCommand("dev_rollback",   "Rollback to previous stable version"),
        BotCommand("dev_deploy",     "Deploy latest from GitHub"),
        BotCommand("dev_memory",     "Query version history and logs"),
        BotCommand("dev_suggestion", "Get bot self-improvement suggestions"),
    ]

    def __init__(
        self,
        bus: MessageBus,
        coordinator: CoordinatorAgent,
        conv_store: ConversationStore,
        tool_registry: Any,
    ) -> None:
        self._bus         = bus
        self._coordinator = coordinator
        self._conv        = conv_store
        self._tools       = tool_registry
        self._limiter     = RateLimiter(rate=20, per=60)
        self._app: Optional[Application] = None

    # ------------------------------------------------------------------
    # Startup / shutdown
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not settings.TELEGRAM_BOT_TOKEN:
            log.error("TELEGRAM_BOT_TOKEN is not set – Telegram bot will not start.")
            return

        try:
            self._app = (
                ApplicationBuilder()
                .token(settings.TELEGRAM_BOT_TOKEN)
                .build()
            )

            # Register handlers
            self._register_handlers()

            # Set bot command menu
            await self._app.bot.set_my_commands(self._COMMANDS)

            log.info("Telegram bot initialising...")
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(drop_pending_updates=True)
            log.info("Telegram bot is online.")
        except Exception as e:
            log.error(f"Failed to start Telegram bot: {e}")

    async def stop(self) -> None:
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                log.info("Telegram bot stopped.")
            except Exception as e:
                log.error(f"Error during Telegram bot shutdown: {e}")

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        if not self._app:
            log.error("Telegram application instance is not initialized.")
            return

        app = self._app
        app.add_handler(CommandHandler("start",         self._cmd_start))
        app.add_handler(CommandHandler("help",          self._cmd_help))
        app.add_handler(CommandHandler("status",        self._cmd_status))
        app.add_handler(CommandHandler("agents",        self._cmd_agents))
        app.add_handler(CommandHandler("memory",        self._cmd_memory))
        app.add_handler(CommandHandler("scan_repo",     self._cmd_scan_repo))
        app.add_handler(CommandHandler("security_scan", self._cmd_security_scan))
        app.add_handler(CommandHandler("code_audit",    self._cmd_code_audit))
        app.add_handler(CommandHandler("threat_intel",  self._cmd_threat_intel))
        app.add_handler(CommandHandler("update_self",   self._cmd_update_self))
        app.add_handler(CommandHandler("tools",         self._cmd_tools))
        app.add_handler(CommandHandler("logs",          self._cmd_logs))
        app.add_handler(CommandHandler("system",        self._cmd_system))
        app.add_handler(CommandHandler("swarm_status",   self._cmd_swarm_status))
        app.add_handler(CommandHandler("swarm_agents",   self._cmd_swarm_agents))
        app.add_handler(CommandHandler("swarm_models",   self._cmd_swarm_models))
        app.add_handler(CommandHandler("run_swarm_task", self._cmd_run_swarm_task))
        app.add_handler(CommandHandler("model_router",   self._cmd_model_router))
        app.add_handler(CommandHandler("system_status",  self._cmd_system_status))

        app.add_handler(CommandHandler("dev_status",     self._cmd_dev_status))
        app.add_handler(CommandHandler("dev_task",       self._cmd_dev_task))
        app.add_handler(CommandHandler("dev_tool",       self._cmd_dev_tool))
        app.add_handler(CommandHandler("dev_patch",      self._cmd_dev_patch))
        app.add_handler(CommandHandler("dev_review",     self._cmd_dev_review))
        app.add_handler(CommandHandler("dev_rollback",   self._cmd_dev_rollback))
        app.add_handler(CommandHandler("dev_deploy",     self._cmd_dev_deploy))
        app.add_handler(CommandHandler("dev_memory",     self._cmd_dev_memory))
        app.add_handler(CommandHandler("dev_suggestion", self._cmd_dev_suggestion))

        # Inline keyboard callbacks
        app.add_handler(CallbackQueryHandler(self._callback_query))

        # Free-text messages → LLM conversation
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------

    def _is_admin(self, user_id: int, username: str = "") -> bool:
        """
        Return True if the user is an authorised admin.
        Checks both numeric user IDs and Telegram usernames (case-insensitive).
        If neither list is configured, all users are treated as admins (open mode).
        """
        has_id_list       = bool(settings.TELEGRAM_ADMIN_IDS)
        has_username_list = bool(settings.TELEGRAM_ADMIN_USERNAMES)

        if not has_id_list and not has_username_list:
            return True  # open mode – no admins configured

        if has_id_list and user_id in settings.TELEGRAM_ADMIN_IDS:
            return True

        if has_username_list and username.lower().lstrip("@") in settings.TELEGRAM_ADMIN_USERNAMES:
            return True

        return False

    async def _guard(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        admin_required: bool = False,
    ) -> bool:
        """Return True if the request should proceed."""
        chat_id  = update.effective_chat.id
        user     = update.effective_user
        username = (user.username or "").lower()

        if not self._limiter.is_allowed(chat_id):
            await update.message.reply_text("⏳ Rate limit exceeded. Please wait.")
            return False

        if admin_required and not self._is_admin(user.id, username):
            await update.message.reply_text(
                "🔒 This command is restricted to administrators."
            )
            log.warning(
                f"Unauthorised attempt: user_id={user.id} "
                f"username=@{username} command={update.message.text}"
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user     = update.effective_user
        username = (user.username or "").lower()

        if not await self._guard(update, ctx):
            return

        is_admin    = self._is_admin(user.id, username)
        admin_badge = "🛡️ **Admin**" if is_admin else "👤 User"

        # Always log the user ID so admins can add themselves
        log.info(
            f"User /start: id={user.id} username=@{username} "
            f"name='{user.full_name}' admin={is_admin}"
        )

        id_hint = ""
        if not is_admin and not settings.TELEGRAM_ADMIN_IDS:
            id_hint = (
                f"\n\n💡 Your numeric ID is `{user.id}` – "
                "add it to `TELEGRAM_ADMIN_IDS` in .env to grant admin access."
            )

        msg = (
            f"👋 Welcome to **TASO** – Telegram Autonomous Security Operator\n\n"
            f"Your role: {admin_badge}\n"
            f"Username:  @{username}\n"
            f"User ID:   `{user.id}`\n"
            f"LLM:       `{settings.LLM_BACKEND}/{self._llm_model()}`"
            f"{id_hint}\n\n"
            "Type /help to see all available commands."
        )
        try:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.error(f"Failed to send start message: {e}")
    def _llm_model(self) -> str:
        if settings.LLM_BACKEND == "copilot":
            return settings.COPILOT_MODEL
        elif settings.LLM_BACKEND == "openai":
            return settings.OPENAI_MODEL
        elif settings.LLM_BACKEND == "anthropic":
            return settings.ANTHROPIC_MODEL
        elif settings.LLM_BACKEND == "ollama":
            return settings.OLLAMA_MODEL
        return "unknown"

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return

        lines = ["📋 **TASO Command Reference**\n"]
        for cmd in self._COMMANDS:
            lines.append(f"/{cmd.command} – {cmd.description}")

        await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        await update.message.reply_text("🔍 Gathering status…")

        result = await self._dispatch_task("system_status", {}, update)
        metrics = result.get("result", {}).get("metrics", {})

        if not metrics:
            await update.message.reply_text("⚠️ Could not collect metrics.")
            return

        cpu    = metrics.get("cpu", {})
        mem    = metrics.get("memory", {})
        disk   = metrics.get("disk", {})
        docker = metrics.get("docker", {})

        msg = (
            f"🖥️ **System Status** – {metrics.get('hostname', 'unknown')}\n\n"
            f"🐍 Python: {metrics.get('python', '?')}\n"
            f"⏱ Boot: {metrics.get('boot_time', '?')[:19]}\n\n"
            f"💻 CPU: {cpu.get('percent', '?')}% "
            f"({cpu.get('cores', '?')} cores)\n"
            f"🧠 RAM: {mem.get('used_mb', '?')} / {mem.get('total_mb', '?')} MB "
            f"({mem.get('percent', '?')}%)\n"
            f"💾 Disk: {disk.get('used_gb', '?')} / {disk.get('total_gb', '?')} GB "
            f"({disk.get('percent', '?')}%)\n"
            f"🐳 Docker: {'✅' if docker.get('available') else '❌'} "
            f"({docker.get('containers', 0)} containers)\n\n"
            f"🤖 Agents: {len(self._coordinator.list_tasks(0))} tasks queued"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_agents(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        tasks = self._coordinator.list_tasks(10)
        if not tasks:
            await update.message.reply_text("🤖 No recent agent tasks.")
            return

        lines = ["🤖 **Recent Agent Tasks**\n"]
        for t in tasks:
            status_emoji = {"done": "✅", "running": "⏳", "failed": "❌",
                             "pending": "🔄"}.get(t["status"], "❓")
            lines.append(
                f"{status_emoji} `{t['id'][:8]}` – {t['command']} "
                f"[{t['status']}] {t['created_at'][:19]}"
            )

        await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        query = " ".join(ctx.args) if ctx.args else ""
        if not query:
            await update.message.reply_text(
                "Usage: /memory <search query>\nExample: /memory SQL injection"
            )
            return

        await update.message.reply_text(f"🔎 Searching memory for: *{query}*",
                                         parse_mode=ParseMode.MARKDOWN)

        result = await self._dispatch_task(
            "memory_query", {"query": query, "top_k": 5}, update
        )
        data   = result.get("result", {})
        vector = data.get("vector_results", [])
        cves   = data.get("cve_results", [])

        lines = [f"🧠 **Memory Search: {query}**\n"]

        if vector:
            lines.append("**Semantic Results:**")
            for r in vector[:3]:
                lines.append(f"• [{r.get('category', '')}] {r.get('text', '')[:150]}")

        if cves:
            lines.append("\n**CVE Results:**")
            for c in cves[:3]:
                lines.append(
                    f"• {c['cve_id']} [{c.get('severity', '?')}] "
                    f"– {c.get('description', '')[:100]}"
                )

        if not vector and not cves:
            lines.append("No results found.")

        await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_scan_repo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        repo_path = " ".join(ctx.args) if ctx.args else str(settings.GIT_REPO_PATH)
        await update.message.reply_text(f"🔍 Scanning repository: `{repo_path}`",
                                         parse_mode=ParseMode.MARKDOWN)

        result = await self._dispatch_task(
            "scan_repo", {"repo_path": repo_path}, update
        )
        data    = result.get("result", {})
        summary = data.get("summary", "No summary available.")
        findings = data.get("findings", {})

        issues = findings.get("static", {}).get("issues", [])
        secrets = findings.get("secrets", {}).get("findings", [])

        msg = (
            f"📊 **Repository Scan: {repo_path}**\n\n"
            f"**Summary:**\n{summary}\n\n"
            f"⚠️ Static issues: {len(issues)}\n"
            f"🔑 Secret patterns: {len(secrets)}"
        )
        await self._reply_long(update, msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_security_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        repo_path = " ".join(ctx.args) if ctx.args else str(settings.GIT_REPO_PATH)
        await update.message.reply_text("🛡️ Running full security scan…")

        result  = await self._dispatch_task(
            "security_scan", {"repo_path": repo_path}, update
        )
        data    = result.get("result", {})
        summary = data.get("summary", "Scan complete.")

        await self._reply_long(
            update,
            f"🛡️ **Security Scan Result**\n\n{summary}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_code_audit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        await update.message.reply_text(
            "📎 Please send the code you want audited as the next message."
        )
        ctx.user_data["awaiting_code_audit"] = True

    async def _cmd_threat_intel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        keywords = ctx.args if ctx.args else []
        sources  = ["nvd", "cisa"]
        await update.message.reply_text(
            f"🌐 Collecting threat intelligence (sources: {', '.join(sources)})…"
        )

        result  = await self._dispatch_task(
            "threat_intel",
            {"keywords": keywords, "sources": sources},
            update,
        )
        data    = result.get("result", {})
        summary = data.get("summary", "Collection complete.")
        nvd_count  = len(data.get("gathered", {}).get("nvd", {}).get("items", []))
        cisa_count = len(data.get("gathered", {}).get("cisa", {}).get("items", []))

        msg = (
            f"🌐 **Threat Intelligence Report**\n\n"
            f"NVD CVEs fetched: {nvd_count}\n"
            f"CISA KEV items:   {cisa_count}\n\n"
            f"**Analysis:**\n{summary}"
        )
        await self._reply_long(update, msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_update_self(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        if not settings.SELF_IMPROVE_ENABLED:
            await update.message.reply_text(
                "⚠️ Self-improvement is disabled.\n"
                "Set `SELF_IMPROVE_ENABLED=true` in .env to enable."
            )
            return

        await update.message.reply_text("🔧 Analysing codebase for improvements…")

        result   = await self._dispatch_task("update_self", {}, update)
        data     = result.get("result", {})
        proposals = data.get("proposals", [])

        if not proposals:
            await update.message.reply_text("✅ No improvements identified.")
            return

        lines = [f"🔧 **{len(proposals)} Improvement Proposals**\n"]
        for p in proposals[:5]:
            lines.append(f"📄 `{p.get('file', '?')}`")
            for imp in p.get("improvements", [])[:2]:
                lines.append(
                    f"  • [{imp.get('priority', '?').upper()}] {imp.get('issue', '')}"
                )

        await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_tools(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return

        tool_list = self._tools.list_tools()
        lines     = [f"🧰 **Available Tools** ({len(tool_list)})\n"]
        for t in tool_list:
            lines.append(f"• **{t['name']}** – {t['description']}")

        await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_logs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return

        category = ctx.args[0] if ctx.args else "combined"
        lines    = 30

        result = await self._dispatch_task(
            "system_status",
            {"logs": True, "category": category, "lines": lines},
            update,
        )

        # Fall back to direct file read if agent not available
        from agents.system_agent import SystemAgent
        log_lines = SystemAgent._read_log(category, lines)

        text = "\n".join(log_lines[-20:])
        await self._reply_long(
            update,
            f"📋 **Logs ({category})**\n```\n{text}\n```",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_system(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._cmd_status(update, ctx)

    async def _cmd_swarm_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        from swarm.swarm_orchestrator import swarm_orchestrator
        s = swarm_orchestrator.status()
        msg = (
            f"🐝 *Swarm Status*\n\n"
            f"Active: {s['active_swarms']} | Completed: {s['completed_swarms']}\n"
            f"Max parallel: {s['max_parallel']} | Timeout: {s['task_timeout']}s\n"
        )
        if s["recent"]:
            msg += "\n*Recent swarms:*\n"
            for sw in s["recent"][-3:]:
                status_icon = {"done": "✅", "failed": "❌", "executing": "⚙️", "planning": "🔍"}.get(sw.get("status", ""), "⏳")
                elapsed = f" ({sw['elapsed']:.1f}s)" if "elapsed" in sw else ""
                req = sw.get('request', '?')[:40]
                msg += f"{status_icon} {req}…{elapsed}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_swarm_agents(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        from swarm.agent_registry import agent_registry
        agents = agent_registry.status_dict()
        if not agents:
            await update.message.reply_text("No agents registered in swarm registry.")
            return
        lines = ["👾 *Swarm Agent Registry*\n"]
        for name, info in agents.items():
            bar = "█" * (info["load_pct"] // 20) + "░" * (5 - info["load_pct"] // 20)
            lines.append(
                f"*{name}* [{bar}] {info['load_pct']}%\n"
                f"  caps: {', '.join(info['capabilities'])}\n"
                f"  tasks: {info['total_tasks']} done, {info['total_errors']} errors\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_swarm_models(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        from models.model_registry import registry
        models = registry.all_models()
        lines = ["🤖 *Model Registry*\n"]
        for m in models:
            avail = "✅" if m.available else "❌"
            uncensored = " 🔓" if m.uncensored else ""
            lines.append(
                f"{avail} *{m.name}*{uncensored}\n"
                f"  provider: {m.provider.value} | latency: {m.latency_tier} | cost: {m.cost_tier}\n"
                f"  tasks: {', '.join(t.value for t in m.preferred_tasks)}\n"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_run_swarm_task(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        request = " ".join(ctx.args) if ctx.args else ""
        if not request:
            await update.message.reply_text("Usage: /run\\_swarm\\_task <your task description>")
            return
        await update.message.reply_text(f"🐝 Swarm executing: _{request[:60]}_…", parse_mode="Markdown")
        try:
            from swarm.swarm_orchestrator import swarm_orchestrator
            result = await swarm_orchestrator.run(request)
            if len(result) > 3800:
                result = result[:3800] + "\n…[truncated]"
            await update.message.reply_text(f"✅ *Swarm result:*\n\n{result}", parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ Swarm error: {exc}")

    async def _cmd_model_router(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        from models.model_router import router
        s = router.status()
        msg = (
            f"🔀 *Model Router*\n\n"
            f"Active backend: `{s['active_backend']}`\n"
            f"Primary model: `{s['primary_model']}`\n"
            f"Uncensored fallback: `{s['uncensored_model']}`\n"
            f"Refusal fallback enabled: {'✅' if s['uncensored_fallback_enabled'] else '❌'}\n"
            f"Registered models: {s['registered_models']}\n\n"
            f"*Routing logic:*\n"
            f"• coding → deepseek\\-coder \\(Ollama\\) or primary\n"
            f"• security/analysis → primary model\n"
            f"• refused → uncensored local LLM \\({s['uncensored_model']}\\)\n"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def _cmd_system_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        import psutil
        from swarm.swarm_orchestrator import swarm_orchestrator
        from swarm.agent_registry import agent_registry
        from models.model_router import router

        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        agents = agent_registry.all_agents()
        swarm_s = swarm_orchestrator.status()
        router_s = router.status()

        msg = (
            f"🖥 *TASO System Status*\n\n"
            f"*Resources*\n"
            f"CPU: {cpu:.1f}% | RAM: {mem.percent:.1f}% "
            f"({mem.used//1024//1024}MB/{mem.total//1024//1024}MB)\n"
            f"Disk: {disk.percent:.1f}% used\n\n"
            f"*LLM*\n"
            f"Backend: `{router_s['active_backend']}` → `{router_s['primary_model']}`\n"
            f"Uncensored: `{router_s['uncensored_model']}`\n\n"
            f"*Swarm*\n"
            f"Active swarms: {swarm_s['active_swarms']} | Agents: {len(agents)}\n\n"
            f"*Agents* ({len(agents)} registered)\n"
        )
        for a in agents[:8]:
            msg += f"• {a.name}: {a.active_tasks}/{a.max_concurrent} tasks\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    # ------------------------------------------------------------------
    # Free-text handler (LLM conversation)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # NLP Intent Router
    # ------------------------------------------------------------------

    # Maps intent name → (handler_method, needs_args_key)
    # handler_method receives (update, ctx, extracted_arg: str)
    _INTENT_MAP: Dict[str, str] = {
        "status":        "_nlp_status",
        "agents":        "_nlp_agents",
        "tools":         "_nlp_tools",
        "system":        "_nlp_system",
        "logs":          "_nlp_logs",
        "memory":        "_nlp_memory",
        "scan_repo":     "_nlp_scan_repo",
        "security_scan": "_nlp_security_scan",
        "code_audit":    "_nlp_code_audit",
        "threat_intel":  "_nlp_threat_intel",
        "update_self":   "_nlp_update_self",
        "swarm_status":  "_nlp_swarm_status",
        "swarm_agents":  "_nlp_swarm_agents",
        "swarm_models":  "_nlp_swarm_models",
        "swarm_task":    "_nlp_swarm_task",
        "dev_status":    "_nlp_dev_status",
        "dev_task":      "_nlp_dev_task",
        "dev_tool":      "_nlp_dev_tool",
        "dev_patch":     "_nlp_dev_patch",
        "dev_rollback":  "_nlp_dev_rollback",
        "dev_deploy":    "_nlp_dev_deploy",
        "dev_suggestion":"_nlp_dev_suggestion",
        "chat":          "_nlp_chat",
    }

    _INTENT_SYSTEM = (
        "You are an intent classifier for TASO, a security AI bot. "
        "Given a user message, respond with JSON only — no explanation.\n\n"
        "Available intents:\n"
        "- status: check bot/system status\n"
        "- agents: list running agents\n"
        "- tools: list available tools\n"
        "- system: system resource info\n"
        "- logs: view recent logs\n"
        "- memory: query stored knowledge/memory\n"
        "- scan_repo: scan/analyze a code repository (arg: repo path or URL)\n"
        "- security_scan: run security vulnerability scan (arg: target path/URL)\n"
        "- code_audit: audit a code snippet for vulnerabilities\n"
        "- threat_intel: gather threat intelligence on a topic (arg: topic/CVE/domain)\n"
        "- update_self: trigger self-improvement/update cycle\n"
        "- swarm_status: swarm orchestrator status\n"
        "- swarm_agents: list swarm agents\n"
        "- swarm_models: list available LLM models\n"
        "- swarm_task: run a complex task via agent swarm (arg: task description)\n"
        "- dev_status: development/version/health overview\n"
        "- dev_task: submit a development or feature task (arg: task description)\n"
        "- dev_tool: create a new tool via LLM (arg: tool description)\n"
        "- dev_patch: patch/modify a module (arg: what to change)\n"
        "- dev_rollback: rollback to previous version\n"
        "- dev_deploy: deploy latest code from GitHub\n"
        "- dev_suggestion: ask bot to suggest improvements\n"
        "- chat: general conversation, questions, anything else\n\n"
        "Respond with ONLY valid JSON:\n"
        '{"intent": "<intent>", "arg": "<extracted argument or empty string>", '
        '"confidence": <0.0-1.0>}'
    )

    async def _classify_intent(self, text: str, history: List[Dict]) -> Dict:
        """Use LLM to classify the user's natural-language intent."""
        try:
            from models.model_router import router
            from models.model_registry import TaskType
            # Build compact history context (last 3 turns)
            ctx_lines = []
            for h in history[-6:]:
                role = h.get("role", "")
                content = h.get("content", "")[:120]
                ctx_lines.append(f"{role}: {content}")
            ctx_str = "\n".join(ctx_lines)

            prompt = (
                f"Conversation context:\n{ctx_str}\n\n"
                f"User message: {text}"
            ) if ctx_str else f"User message: {text}"

            raw = await router.query(
                prompt=prompt,
                task_type=TaskType.ANALYSIS,
                system=self._INTENT_SYSTEM,
            )
            # Extract JSON from response
            import re as _re
            m = _re.search(r'\{[^{}]+\}', raw, _re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as exc:
            log.warning(f"Intent classification failed: {exc}")
        # Fallback: treat as chat
        return {"intent": "chat", "arg": text, "confidence": 0.5}

    async def _handle_message(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return

        chat_id = update.effective_chat.id
        text    = (update.message.text or "").strip()

        if not text:
            return

        # --- Awaiting state takes priority ---
        if ctx.user_data.get("awaiting_code_audit"):
            ctx.user_data.pop("awaiting_code_audit")
            await update.message.reply_text("🔍 Auditing submitted code…")
            result = await self._dispatch_task(
                "code_audit", {"code": text, "filename": "user_submitted.py"}, update
            )
            analysis = result.get("result", {}).get("analysis", "No analysis.")
            await self._reply_long(
                update, f"🛡️ **Code Audit Result**\n\n{analysis}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # --- Store message & load history ---
        await self._conv.add_message(chat_id, "user", text)
        history = await self._conv.get_context(chat_id)

        # --- Classify intent ---
        classification = await self._classify_intent(text, history)
        intent     = classification.get("intent", "chat")
        arg        = classification.get("arg", text)
        confidence = classification.get("confidence", 1.0)

        log.info(f"NLP intent: {intent!r} confidence={confidence:.2f} arg={str(arg)[:60]!r}")

        # Show intent hint for transparency (only when not plain chat)
        if intent != "chat" and confidence >= 0.75:
            intent_labels = {
                "scan_repo": "🔍 Scanning repository",
                "security_scan": "🛡️ Running security scan",
                "code_audit": "🔎 Preparing code audit",
                "threat_intel": "🌐 Gathering threat intelligence",
                "update_self": "🔄 Triggering self-update",
                "swarm_task": "🐝 Dispatching to agent swarm",
                "dev_task": "⚙️ Submitting dev task",
                "dev_tool": "🛠️ Creating new tool",
                "dev_patch": "📝 Generating patch",
                "dev_rollback": "⏪ Rolling back",
                "dev_deploy": "🚀 Deploying",
            }
            label = intent_labels.get(intent)
            if label:
                await update.message.reply_text(f"{label}…")

        # --- Dispatch to intent handler ---
        handler_name = self._INTENT_MAP.get(intent, "_nlp_chat")
        handler = getattr(self, handler_name, self._nlp_chat)
        response = await handler(update, ctx, arg, history)

        if response:
            await self._conv.add_message(chat_id, "assistant", response[:500])
            await self._reply_long(update, response)

    # ------------------------------------------------------------------
    # NLP intent handlers — thin wrappers that reuse existing logic
    # ------------------------------------------------------------------

    async def _nlp_status(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_status(update, ctx)
        return None  # handler already replied

    async def _nlp_agents(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_agents(update, ctx)
        return None

    async def _nlp_tools(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_tools(update, ctx)
        return None

    async def _nlp_system(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_system(update, ctx)
        return None

    async def _nlp_logs(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_logs(update, ctx)
        return None

    async def _nlp_memory(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_memory(update, ctx)
        return None

    async def _nlp_scan_repo(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_scan_repo(update, ctx)
        return None

    async def _nlp_security_scan(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_security_scan(update, ctx)
        return None

    async def _nlp_code_audit(self, update, ctx, arg, history) -> Optional[str]:
        # If arg looks like code (has newlines or >80 chars), audit directly
        if "\n" in arg or len(arg) > 80:
            result = await self._dispatch_task(
                "code_audit", {"code": arg, "filename": "nlp_submitted.py"}, update
            )
            analysis = result.get("result", {}).get("analysis", "No analysis.")
            await self._reply_long(
                update, f"🛡️ **Code Audit Result**\n\n{analysis}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                "📎 Please send the code you want audited as the next message."
            )
            ctx.user_data["awaiting_code_audit"] = True
        return None

    async def _nlp_threat_intel(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_threat_intel(update, ctx)
        return None

    async def _nlp_update_self(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_update_self(update, ctx)
        return None

    async def _nlp_swarm_status(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_swarm_status(update, ctx)
        return None

    async def _nlp_swarm_agents(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_swarm_agents(update, ctx)
        return None

    async def _nlp_swarm_models(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_swarm_models(update, ctx)
        return None

    async def _nlp_swarm_task(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_run_swarm_task(update, ctx)
        return None

    async def _nlp_dev_status(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_dev_status(update, ctx)
        return None

    async def _nlp_dev_task(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_dev_task(update, ctx)
        return None

    async def _nlp_dev_tool(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_dev_tool(update, ctx)
        return None

    async def _nlp_dev_patch(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_dev_patch(update, ctx)
        return None

    async def _nlp_dev_rollback(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_dev_rollback(update, ctx)
        return None

    async def _nlp_dev_deploy(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_dev_deploy(update, ctx)
        return None

    async def _nlp_dev_suggestion(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_dev_suggestion(update, ctx)
        return None

    async def _nlp_chat(self, update, ctx, arg, history) -> Optional[str]:
        """General conversation — answer directly via LLM."""
        system = (
            "You are TASO, an autonomous AI security research assistant. "
            "You help with cybersecurity analysis, threat intelligence, code review, "
            "and security automation. Be concise, technical, and direct. "
            "You can also explain your own capabilities when asked."
        )
        response = await self._coordinator.llm_query(
            arg or update.message.text or "",
            system=system,
            history=history,
        )
        return response

    # ------------------------------------------------------------------
    # Inline keyboard callbacks
    # ------------------------------------------------------------------

    async def _callback_query(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        data  = query.data or ""

        if data.startswith("scan:"):
            repo = data[5:]
            await query.message.reply_text(f"🔍 Scanning `{repo}`…",
                                            parse_mode=ParseMode.MARKDOWN)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _cmd_dev_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        from self_healing.version_manager import version_manager
        from self_healing.git_manager import git_current_sha, git_log
        from tools.base_tool import registry as tool_registry

        s = version_manager.status_dict()
        sha = await git_current_sha()
        log_entries = await git_log(3)

        dyn_tools = []
        try:
            dyn_tools = tool_registry.list_dynamic()
        except Exception:
            pass

        msg = (
            f"🔧 *TASO Dev Status*\n\n"
            f"*Git*: `{sha[:12] if sha else 'no commits'}`\n"
            f"*Versions*: {s['total_versions']} recorded, {s['stable_versions']} stable\n"
            f"*Last stable*: `{s['last_stable'] or 'none'}`\n"
            f"*Dynamic tools*: {len(dyn_tools)}\n\n"
            f"*Recent commits:*\n"
        )
        for entry in log_entries:
            msg += f"• `{entry['sha']}` {entry['message'][:40]}\n"
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_dev_task(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        task = " ".join(ctx.args) if ctx.args else ""
        if not task:
            await update.message.reply_text(
                "Usage: `/dev_task <description>`\n\n"
                "Example: `/dev_task Add a port scanner tool that checks common ports`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(f"🤖 Submitting to agent swarm: _{task[:60]}_…", parse_mode=ParseMode.MARKDOWN)
        try:
            from swarm.swarm_orchestrator import swarm_orchestrator
            result = await swarm_orchestrator.run(task)
            if len(result) > 3800:
                result = result[:3800] + "\n…[truncated]"
            await update.message.reply_text(f"✅ *Result:*\n\n{result}", parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            await update.message.reply_text(f"❌ Error: {exc}")

    async def _cmd_dev_tool(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        task = " ".join(ctx.args) if ctx.args else ""
        if not task:
            await update.message.reply_text(
                "Usage: `/dev_tool <description>`\n\n"
                "Example: `/dev_tool A tool that checks if a URL is reachable`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(f"⚙️ Generating tool: _{task[:60]}_…", parse_mode=ParseMode.MARKDOWN)
        try:
            import hashlib
            from tools.dynamic_tool_generator import tool_generator
            from tools.sandbox_tester import sandbox_test_tool
            from tools.base_tool import registry as tool_registry
            from memory.version_history_db import version_history_db

            tool = await tool_generator.generate(task)
            await update.message.reply_text("🔬 Testing in sandbox…")
            passed, output = await sandbox_test_tool(tool.code)
            tool.test_passed = passed

            if passed:
                tool_registry.register_dynamic(
                    name=tool.name, code=tool.code,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    output_schema=tool.output_schema,
                    tags=tool.tags,
                )
                await version_history_db.log_tool(
                    tool_name=tool.name, version=tool.version,
                    action="created", agent="telegram_user",
                    test_passed=True, test_output=output,
                    code_hash=hashlib.sha256(tool.code.encode()).hexdigest()[:16],
                )
                result = (
                    f"✅ *Tool Created & Registered*\n\n"
                    f"Name: `{tool.name}`\n"
                    f"Description: {tool.description}\n"
                    f"Version: {tool.version}\n"
                    f"Test: passed ✅\n\n"
                    f"The tool is now available to all agents."
                )
            else:
                result = (
                    f"❌ *Tool Generated but Failed Tests*\n\n"
                    f"Name: `{tool.name}`\n"
                    f"Error: {output[:400]}\n\n"
                    f"Tool was NOT registered. Fix the description and try again."
                )
            await update.message.reply_text(result, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            await update.message.reply_text(f"❌ Tool generation error: {exc}")

    async def _cmd_dev_patch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        task = " ".join(ctx.args) if ctx.args else ""
        if not task:
            await update.message.reply_text("Usage: `/dev_patch <patch description>`", parse_mode=ParseMode.MARKDOWN)
            return
        await update.message.reply_text(f"🔧 Generating patch: _{task[:60]}_…", parse_mode=ParseMode.MARKDOWN)
        try:
            from models.model_router import router
            from models.model_registry import TaskType
            patch = await router.query(
                prompt=task,
                system="Generate a minimal git-style unified diff for the requested code change. Output ONLY the diff.",
                task_type=TaskType.CODING,
            )
            if len(patch) > 3800:
                patch = patch[:3800] + "\n…[truncated]"
            await update.message.reply_text(
                f"📋 *Proposed patch:*\n```\n{patch}\n```\n\nUse /dev\\_task to apply via swarm.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Error: {exc}")

    async def _cmd_dev_review(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            from self_healing.git_manager import git_log
            from models.model_router import router
            from models.model_registry import TaskType

            log_entries = await git_log(1)
            if not log_entries:
                await update.message.reply_text("No commits yet.")
                return
            last = log_entries[0]
            review = await router.query(
                prompt=f"Explain this commit in plain language: '{last['message']}' (SHA: {last['sha']})",
                task_type=TaskType.ANALYSIS,
            )
            await update.message.reply_text(
                f"📝 *Last commit review*\n`{last['sha']}` — {last['date']}\n\n{review[:1500]}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Error: {exc}")

    async def _cmd_dev_rollback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        target_sha = ctx.args[0] if ctx.args else None
        reason = " ".join(ctx.args[1:]) if len(ctx.args or []) > 1 else "manual rollback via Telegram"
        await update.message.reply_text("⏪ Initiating rollback…")
        try:
            from self_healing.rollback_manager import rollback_manager
            sha = await rollback_manager.rollback(reason=reason, target_sha=target_sha)
            if sha:
                await update.message.reply_text(f"✅ Rolled back to `{sha[:12]}`", parse_mode=ParseMode.MARKDOWN)
            else:
                await update.message.reply_text("❌ Rollback failed — no stable version found.")
        except Exception as exc:
            await update.message.reply_text(f"❌ Rollback error: {exc}")

    async def _cmd_dev_deploy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        await update.message.reply_text("🚀 Deploying latest…")
        try:
            from self_healing.deploy_manager import deploy_manager
            ok = await deploy_manager.bootstrap()
            sha = deploy_manager.current_sha
            msg = (
                f"{'✅ Deployed' if ok else '⚠️ Partial deploy'}\n"
                f"SHA: `{sha[:12] if sha else 'unknown'}`"
            )
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            await update.message.reply_text(f"❌ Deploy error: {exc}")

    async def _cmd_dev_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            from memory.version_history_db import version_history_db
            versions = await version_history_db.recent_versions(5)
            tools = await version_history_db.recent_tools(5)
            rollbacks = await version_history_db.recent_rollbacks(3)

            msg = "🧠 *Dev Memory*\n\n*Recent Versions:*\n"
            for v in versions:
                icon = "✅" if v["stable"] else ("🧪" if v["tested"] else "📝")
                msg += f"{icon} `{v['version_id'][:20]}` {v['desc'][:40]}\n"

            msg += "\n*Recent Tools:*\n"
            for t in tools:
                icon = "✅" if t["passed"] else "❌"
                msg += f"{icon} `{t['tool']}` v{t['version']} — {t['action']}\n"

            if rollbacks:
                msg += "\n*Rollbacks:*\n"
                for r in rollbacks:
                    icon = "✅" if r["success"] else "❌"
                    msg += f"{icon} → `{r['to'][:8] if r['to'] else '?'}` — {r['reason'][:40]}\n"

            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            await update.message.reply_text(f"❌ Memory error: {exc}")

    async def _cmd_dev_suggestion(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        await update.message.reply_text("💡 Analyzing codebase for improvements…")
        try:
            from models.model_router import router
            from models.model_registry import TaskType
            from self_healing.git_manager import git_log

            recent = await git_log(5)
            recent_str = "\n".join(f"- {e['message']}" for e in recent)

            suggestion = await router.query(
                prompt=(
                    f"You are analyzing TASO, an autonomous security research bot. "
                    f"Recent changes:\n{recent_str}\n\n"
                    f"Suggest 3 specific, actionable improvements or new capabilities "
                    f"that would make the bot more useful for security research."
                ),
                task_type=TaskType.PLANNING,
            )
            await update.message.reply_text(
                f"💡 *Improvement Suggestions:*\n\n{suggestion[:2000]}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Error: {exc}")

    async def _dispatch_task(
        self, command: str, args: Dict, update: Update
    ) -> Dict[str, Any]:
        """Send a task to the coordinator and await the result."""
        chat_id     = update.effective_chat.id
        reply_topic = f"bot.reply.{chat_id}.{int(time.time())}"

        msg = BusMessage(
            topic      = "coordinator.task",
            sender     = "telegram_bot",
            payload    = {
                "command":       command,
                "args":          args,
                "reply_to_chat": chat_id,
            },
            reply_to   = reply_topic,
        )

        response = await self._bus.publish_and_wait(msg, timeout=90.0)
        if response is None:
            return {"error": "timeout"}
        return response.payload

    @staticmethod
    async def _reply_long(
        update: Update, text: str,
        parse_mode: Optional[str] = None,
        chunk_size: int = 4000,
    ) -> None:
        """Split long messages to respect Telegram's 4096 char limit."""
        if len(text) <= chunk_size:
            await update.message.reply_text(text, parse_mode=parse_mode)
            return

        for i in range(0, len(text), chunk_size):
            await update.message.reply_text(
                text[i: i + chunk_size], parse_mode=parse_mode
            )
            await asyncio.sleep(0.2)
