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
from telegram.constants import ParseMode, ChatAction
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
        BotCommand("models",     "Show registered LLM models"),
        BotCommand("learn_repo", "Learn from a GitHub repository URL"),
        BotCommand("add_feature","Generate and register a new feature"),
        BotCommand("create_agent","Autonomously generate and register a new agent"),
        BotCommand("create_tool", "Autonomously generate and register a new tool"),
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
        app.add_handler(CommandHandler("models",      self._cmd_models))
        app.add_handler(CommandHandler("learn_repo",  self._cmd_learn_repo))
        app.add_handler(CommandHandler("add_feature", self._cmd_add_feature))
        app.add_handler(CommandHandler("create_agent",   self._cmd_create_agent))
        app.add_handler(CommandHandler("create_tool",    self._cmd_create_tool))
        # Git dev-lifecycle commands (DEVELOPMENT_RULES.md)
        app.add_handler(CommandHandler("dev_sync",       self._cmd_dev_sync))
        app.add_handler(CommandHandler("dev_health",     self._cmd_dev_health))
        app.add_handler(CommandHandler("dev_lifecycle",  self._cmd_dev_lifecycle))
        app.add_handler(CommandHandler("dev_branches",   self._cmd_dev_branches))
        # Personalisation commands
        app.add_handler(CommandHandler("profile",     self._cmd_profile))
        app.add_handler(CommandHandler("plugins",     self._cmd_plugins))
        app.add_handler(CommandHandler("activate",    self._cmd_activate))
        app.add_handler(CommandHandler("deactivate",  self._cmd_deactivate))
        # Crawler commands
        app.add_handler(CommandHandler("crawl_start",  self._cmd_crawl_start))
        app.add_handler(CommandHandler("crawl_stop",   self._cmd_crawl_stop))
        app.add_handler(CommandHandler("crawl_status", self._cmd_crawl_status))
        app.add_handler(CommandHandler("crawl_add",    self._cmd_crawl_add))
        app.add_handler(CommandHandler("crawl_search", self._cmd_crawl_search))
        app.add_handler(CommandHandler("crawl_onions", self._cmd_crawl_onions))

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

        tool_list = self._tools.describe_all_tools()
        if not tool_list:
            await update.message.reply_text(
                "🧰 No tools registered yet. Use `/create_tool <description>` to generate one.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        static  = [t for t in tool_list if not t.get("dynamic")]
        dynamic = [t for t in tool_list if t.get("dynamic")]

        lines = [f"🧰 *Available Tools* ({len(tool_list)})\n"]
        if static:
            lines.append("*Built-in:*")
            for t in static:
                lines.append(f"  • `{t['name']}` – {t.get('description','')[:80]}")
        if dynamic:
            lines.append("\n*Dynamic (AI-generated):*")
            for t in dynamic:
                lines.append(f"  • `{t['name']}` – {t.get('description','')[:80]}")

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
        "models":        "_nlp_models",
        "learn_repo":    "_nlp_learn_repo",
        "add_feature":   "_nlp_add_feature",
        "create_agent":  "_nlp_create_agent",
        "create_tool":   "_nlp_create_tool",
        "chat":          "_nlp_chat",
    }

    # Maps every intent to a human-readable label shown while the action runs.
    # All 30 intents are covered so the user always knows what was understood.
    _INTENT_LABELS: Dict[str, str] = {
        "status":        "📊 Checking status",
        "agents":        "🤖 Listing agents",
        "tools":         "🔧 Listing tools",
        "system":        "🖥️ Reading system info",
        "logs":          "📋 Fetching logs",
        "memory":        "🧠 Querying memory",
        "scan_repo":     "🔍 Scanning repository",
        "security_scan": "🛡️ Running security scan",
        "code_audit":    "🔎 Auditing code",
        "threat_intel":  "🌐 Gathering threat intelligence",
        "update_self":   "🔄 Triggering self-update",
        "swarm_status":  "🐝 Checking swarm status",
        "swarm_agents":  "🐝 Listing swarm agents",
        "swarm_models":  "🤖 Listing LLM models",
        "swarm_task":    "🐝 Dispatching to agent swarm",
        "dev_status":    "📦 Checking dev status",
        "dev_task":      "⚙️ Submitting dev task",
        "dev_tool":      "🛠️ Generating new tool",
        "dev_patch":     "📝 Generating patch",
        "dev_rollback":  "⏪ Rolling back",
        "dev_deploy":    "🚀 Deploying",
        "dev_suggestion":"💡 Generating suggestions",
        "models":        "🤖 Listing models",
        "learn_repo":    "📚 Learning from repository",
        "add_feature":   "✨ Building new feature",
        "create_agent":  "🤖 Creating new agent",
        "create_tool":   "🛠️ Creating new tool",
        "chat":          "",
    }

    # Fast-path: obvious keyword patterns that skip the LLM entirely.
    # Checked before calling _classify_intent to reduce latency for simple inputs.
    _FAST_PATTERNS: List[tuple] = [
        # (regex, intent, arg_group_or_empty)
        (r"^(status|how are you|are you (up|alive|running)\??)$", "status", ""),
        (r"^(agents?|list agents?|show agents?)$", "agents", ""),
        (r"^(tools?|list tools?|show tools?|what tools?)$", "tools", ""),
        (r"^(logs?|show logs?|recent logs?)$", "logs", ""),
        (r"^(system|sysinfo|system (info|status|resources?))$", "system", ""),
        (r"^(memory|show memory|what do you know)$", "memory", ""),
        (r"^(models?|list models?|llm models?)$", "models", ""),
        (r"^(swarm status|swarm)$", "swarm_status", ""),
        (r"^(swarm agents?)$", "swarm_agents", ""),
        (r"^(dev status|development status)$", "dev_status", ""),
        (r"^(help|\?)$", "chat", ""),
    ]

    _INTENT_SYSTEM = (
        "You are an intent classifier for TASO, an autonomous AI security bot.\n"
        "Analyse the user message and recent conversation, then respond with ONLY valid JSON.\n\n"
        "INTENT LIST (pick exactly one):\n"
        "status | agents | tools | system | logs | memory\n"
        "scan_repo(arg=path/url) | security_scan(arg=target) | code_audit | threat_intel(arg=topic/CVE)\n"
        "update_self | swarm_status | swarm_agents | swarm_models | swarm_task(arg=task)\n"
        "dev_status | dev_task(arg=task) | dev_tool(arg=description) | dev_patch(arg=change)\n"
        "dev_rollback | dev_deploy | dev_suggestion | models\n"
        "learn_repo(arg=github_url) | add_feature(arg=description)\n"
        "create_agent(arg=description) | create_tool(arg=description)\n"
        "chat  ← use for greetings, questions, anything not matching above\n\n"
        "DISAMBIGUATION RULES:\n"
        "- 'scan' alone without a path/url → security_scan, not scan_repo\n"
        "- 'audit' + code snippet or file path → code_audit\n"
        "- 'create' + agent/tool keyword → create_agent or create_tool\n"
        "- Questions about TASO capabilities → chat\n"
        "- Confidence < 0.6 when genuinely ambiguous\n\n"
        "EXAMPLES:\n"
        'msg: "scan this repo github.com/x/y" → {"intent":"scan_repo","arg":"github.com/x/y","confidence":0.95}\n'
        'msg: "what cves are trending" → {"intent":"threat_intel","arg":"trending CVEs","confidence":0.90}\n'
        'msg: "make a tool that pings hosts" → {"intent":"create_tool","arg":"ping hosts","confidence":0.92}\n'
        'msg: "hello" → {"intent":"chat","arg":"","confidence":0.99}\n\n'
        "OUTPUT: ONLY this JSON, nothing else:\n"
        '{"intent":"<intent>","arg":"<argument or empty>","confidence":<0.0-1.0>}'
    )

    # Minimum confidence to act directly; below this we ask for clarification.
    _CONFIDENCE_THRESHOLD = 0.60
    # Minimum confidence to show the intent label without asking first.
    _LABEL_THRESHOLD = 0.75

    @staticmethod
    def _extract_json(raw: str) -> Optional[Dict]:
        """Robustly extract the first JSON object from an LLM response."""
        import re as _re
        # Try full parse first
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass
        # Find the outermost {...} block
        depth, start = 0, None
        for i, ch in enumerate(raw):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(raw[start:i + 1])
                    except json.JSONDecodeError:
                        start = None
        return None

    async def _fast_path(self, text: str) -> Optional[Dict]:
        """Check obvious keyword patterns without calling the LLM."""
        import re as _re
        lower = text.lower().strip()
        for pattern, intent, arg in self._FAST_PATTERNS:
            m = _re.match(pattern, lower)
            if m:
                return {"intent": intent, "arg": arg or text, "confidence": 0.99,
                        "fast_path": True}
        return None

    async def _classify_intent(self, text: str, history: List[Dict]) -> Dict:
        """Classify the user's natural-language intent via fast-path then LLM."""
        # 1. Fast-path for obvious inputs (zero LLM cost, instant response)
        fast = await self._fast_path(text)
        if fast:
            return fast

        # 2. LLM classification with conversation context
        try:
            from models.model_router import router
            from models.model_registry import TaskType

            # Last 4 turns (8 messages) — enough context without token bloat
            ctx_lines = [
                f"{h.get('role','')}: {h.get('content','')[:150]}"
                for h in history[-8:]
            ]
            ctx_str = "\n".join(ctx_lines)
            prompt = (
                f"Recent conversation:\n{ctx_str}\n\nUser: {text}"
                if ctx_str else f"User: {text}"
            )

            raw = await router.query(
                prompt=prompt,
                task_type=TaskType.ANALYSIS,
                system=self._INTENT_SYSTEM,
            )
            result = self._extract_json(raw)
            if result and "intent" in result:
                # Clamp confidence to valid range
                result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
                return result
        except Exception as exc:
            log.warning(f"Intent classification failed: {exc}")

        return {"intent": "chat", "arg": text, "confidence": 0.5}

    async def _send_typing(self, update: Update) -> None:
        """Send a typing indicator — fire-and-forget, never raises."""
        try:
            await update.message.chat.send_action(ChatAction.TYPING)
        except Exception:
            pass

    async def _ask_clarification(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE,
        intent: str, arg: str, confidence: float,
    ) -> None:
        """Ask the user to confirm a low-confidence intent via inline keyboard."""
        label = self._INTENT_LABELS.get(intent, intent.replace("_", " ").title())
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"✅ Yes — {label}", callback_data=f"confirm:{intent}:{arg[:80]}"),
                InlineKeyboardButton("❌ No, just chat", callback_data="confirm:chat:"),
            ]
        ])
        await update.message.reply_text(
            f"🤔 I think you want: *{label}*\n"
            f"_(confidence: {confidence:.0%})_\n\n"
            f"Should I proceed?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )

    async def _handle_message(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return

        chat_id    = update.effective_chat.id
        user       = update.effective_user
        text       = (update.message.text or "").strip()
        username   = user.username or "" if user else ""
        first_name = user.first_name or "" if user else ""

        if not text:
            return

        # --- Awaiting states take priority ---
        if ctx.user_data.get("awaiting_code_audit"):
            ctx.user_data.pop("awaiting_code_audit")
            await self._send_typing(update)
            await update.message.reply_text("🔎 Auditing submitted code…")
            result = await self._dispatch_task(
                "code_audit", {"code": text, "filename": "user_submitted.py"}, update
            )
            analysis = result.get("result", {}).get("analysis", "No analysis.")
            await self._reply_long(
                update, f"🛡️ *Code Audit Result*\n\n{analysis}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # --- Store message & load history ---
        await self._conv.add_message(chat_id, "user", text)
        history = await self._conv.get_context(chat_id)

        # Show typing while classifying (user gets immediate feedback)
        await self._send_typing(update)

        # --- Personalised fast-path: check user shortcuts + plugin patterns FIRST ---
        classification = None
        try:
            from personalization.personalization_engine import personalization_engine as _pe
            # Load profile to get shortcuts/plugins for pre-classification routing
            from memory.user_profile_store import user_profile_store as _ups
            _profile = await _ups.get_or_create(chat_id, username, first_name)
            _extra = (
                _pe._plugin_manager.build_shortcut_fast_paths(_profile.learned_shortcuts)
                + _pe._plugin_manager.build_fast_patterns(_profile.active_plugins)
            )
            import re as _re
            _lower = text.lower().strip()
            for _pattern, _intent, _arg in _extra:
                _m = _re.match(_pattern, _lower)
                if _m:
                    _a = _arg or (text if not _arg else "")
                    # Try named group 'arg' or group 2
                    try:
                        _a = _m.group("arg") or _a
                    except IndexError:
                        try:
                            _a = _m.group(2) or _a
                        except IndexError:
                            pass
                    classification = {"intent": _intent, "arg": _a,
                                      "confidence": 0.99, "fast_path": True}
                    break
        except Exception as _exc:
            log.debug(f"Personalised fast-path error (non-fatal): {_exc}")

        # --- Global intent classification (if no personalised match) ---
        if classification is None:
            classification = await self._classify_intent(text, history)

        intent     = classification.get("intent", "chat")
        arg        = classification.get("arg", text)
        confidence = float(classification.get("confidence", 0.5))
        fast       = classification.get("fast_path", False)

        log.info(
            f"NLP intent={intent!r} confidence={confidence:.2f} "
            f"fast={fast} arg={str(arg)[:60]!r}"
        )

        # --- Behaviour tracking + personalisation context ---
        pctx = None
        try:
            from personalization.personalization_engine import personalization_engine as _pe
            pctx = await _pe.process(
                user_id=chat_id,
                username=username,
                first_name=first_name,
                intent=intent,
                raw_text=text,
                confidence=confidence,
            )
        except Exception as _exc:
            log.debug(f"Personalisation engine error (non-fatal): {_exc}")

        # --- Low confidence → ask for clarification ---
        if intent != "chat" and confidence < self._CONFIDENCE_THRESHOLD:
            await self._ask_clarification(update, ctx, intent, arg, confidence)
            return

        # --- Show what the bot understood (for non-trivial actions) ---
        label = self._INTENT_LABELS.get(intent, "")
        if label and confidence >= self._LABEL_THRESHOLD and not fast:
            if arg and arg != text:
                await update.message.reply_text(
                    f"{label}…\n_Arg: {arg[:120]}_",
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await update.message.reply_text(f"{label}…")

        # Keep typing indicator alive for slow operations
        await self._send_typing(update)

        # --- Build personalised system-prompt hints for _nlp_chat ---
        if pctx:
            ctx.user_data["_persona_hints"] = pctx.response_hints
        else:
            ctx.user_data.pop("_persona_hints", None)

        # --- Dispatch to intent handler ---
        handler_name = self._INTENT_MAP.get(intent, "_nlp_chat")
        handler = getattr(self, handler_name, self._nlp_chat)
        response = await handler(update, ctx, arg, history)

        if response:
            await self._conv.add_message(chat_id, "assistant", response[:800])
            await self._reply_long(update, response)

        # --- Send personalisation notifications (plugin unlocks, style changes) ---
        if pctx and pctx.notifications:
            for note in pctx.notifications:
                await update.message.reply_text(note, parse_mode=ParseMode.MARKDOWN)

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

    async def _nlp_models(self, update, ctx, arg, history) -> Optional[str]:
        await self._cmd_models(update, ctx)
        return None

    async def _nlp_learn_repo(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_learn_repo(update, ctx)
        return None

    async def _nlp_add_feature(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_add_feature(update, ctx)
        return None

    async def _nlp_create_agent(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        await self._cmd_create_agent(update, ctx)
        return None

    async def _nlp_create_tool(self, update, ctx, arg, history) -> Optional[str]:
        ctx.args = arg.split() if arg else []
        # Route through the confirmation-gated command handler
        await self._cmd_create_tool(update, ctx)
        return None

    async def _nlp_chat(self, update, ctx, arg, history) -> Optional[str]:
        """General conversation — ground the answer with live agent context first."""
        text = arg or (update.message.text if update.message else "") or ""

        # ── 1. Classify what kind of context will help ──────────────────────
        tl = text.lower()
        wants_memory = any(w in tl for w in (
            "cve", "vuln", "exploit", "threat", "malware", "ransomware",
            "zero-day", "patch", "advisory", "breach", "attack", "payload",
            "injection", "xss", "rce", "sqli", "lfi", "rfi", "bypass",
            "pentest", "red team", "osint", "ioc", "ttps", "mitre",
            "what is", "tell me about", "explain", "how does", "how do",
            "what are", "latest", "recent", "news", "research",
        ))
        wants_system = any(w in tl for w in (
            "cpu", "ram", "memory", "disk", "uptime", "load", "process",
            "system", "server", "health", "status", "resource", "swap",
            "running", "agents", "bot", "service",
        ))

        context_parts: list[str] = []

        # ── 2. Fetch context in parallel from agents ─────────────────────────
        tasks = {}
        reply_base = f"chat.ctx.{int(__import__('time').time())}"

        if wants_memory:
            mem_reply = reply_base + ".mem"
            mem_msg = BusMessage(
                topic    = "memory.query",
                sender   = "telegram_bot",
                payload  = {"query": text[:300], "top_k": 4},
                reply_to = mem_reply,
            )
            tasks["memory"] = asyncio.create_task(
                self._bus.publish_and_wait(mem_msg, timeout=8.0)
            )

        if wants_system:
            sys_reply = reply_base + ".sys"
            sys_msg = BusMessage(
                topic    = "system.status",
                sender   = "telegram_bot",
                payload  = {},
                reply_to = sys_reply,
            )
            tasks["system"] = asyncio.create_task(
                self._bus.publish_and_wait(sys_msg, timeout=6.0)
            )

        if tasks:
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            result_map = dict(zip(tasks.keys(), results))

            mem_resp = result_map.get("memory")
            if mem_resp and not isinstance(mem_resp, Exception):
                payload = mem_resp.payload if hasattr(mem_resp, "payload") else {}
                vector  = payload.get("vector_results", [])
                cves    = payload.get("cve_results", [])
                snippets = []
                for r in vector[:3]:
                    snippets.append(f"[{r.get('category','knowledge')}] {r.get('text','')[:200]}")
                for c in cves[:2]:
                    snippets.append(
                        f"[CVE] {c.get('cve_id','')} ({c.get('severity','?')}) "
                        f"– {c.get('description','')[:180]}"
                    )
                if snippets:
                    context_parts.append(
                        "Relevant knowledge from memory:\n" + "\n".join(f"• {s}" for s in snippets)
                    )

            sys_resp = result_map.get("system")
            if sys_resp and not isinstance(sys_resp, Exception):
                payload = sys_resp.payload if hasattr(sys_resp, "payload") else {}
                m = payload.get("metrics", {})
                if m:
                    context_parts.append(
                        f"Live system metrics: CPU {m.get('cpu_pct','?')}% | "
                        f"RAM {m.get('mem_used_gb','?')} GB / {m.get('mem_total_gb','?')} GB | "
                        f"Disk {m.get('disk_used_gb','?')} GB used | "
                        f"Uptime {m.get('uptime_hours','?')} h"
                    )

        # ── 3. Build system prompt with grounding context ────────────────────
        system = (
            "You are TASO, an autonomous AI security research assistant running locally. "
            "You help with cybersecurity analysis, threat intelligence, code review, "
            "and security automation. Be concise, technical, and direct. "
            "Format responses with markdown where it aids readability.\n\n"
            "When the user seems to want to trigger an action (scan, search, generate), "
            "remind them they can describe it in plain English."
        )
        hints = ctx.user_data.get("_persona_hints", [])
        if hints:
            system += "\n\n" + "\n".join(hints)
        if context_parts:
            system += "\n\n--- Live context ---\n" + "\n\n".join(context_parts)

        response = await self._coordinator.llm_query(text, system=system, history=history)
        return response

    # ------------------------------------------------------------------
    # Inline keyboard callbacks
    # ------------------------------------------------------------------

    async def _callback_query(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""

        # Tool generation confirmation (from /create_tool or NLP create_tool)
        if data.startswith("gentool:"):
            description = data[len("gentool:"):]
            if description == "__cancel__":
                await query.edit_message_text("❌ Tool generation cancelled.")
                return
            await query.edit_message_text(
                f"⚙️ Generating tool: _{description[:120]}_\n\n"
                "Generating code → Testing in sandbox → Registering…",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                result = await self._dispatch("developer.request", {
                    "action": "generate_tool",
                    "task":   description,
                })
                text = result.get("result", result.get("error", "No response"))
                await query.message.reply_text(
                    text[:4000], parse_mode=ParseMode.MARKDOWN
                )
            except Exception as exc:
                log.exception("Tool generation callback error")
                await query.message.reply_text(f"❌ Error during tool generation: {exc}")
            return

        # Clarification confirmation from _ask_clarification()
        if data.startswith("confirm:"):
            parts = data.split(":", 2)
            intent = parts[1] if len(parts) > 1 else "chat"
            arg    = parts[2] if len(parts) > 2 else ""

            if intent == "chat":
                await query.edit_message_text("💬 Got it — treating as a chat message.")
                await self._send_typing(update)
                history = await self._conv.get_context(query.message.chat_id)
                response = await self._nlp_chat(update, ctx, arg or query.message.text or "", history)
                if response:
                    await query.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
                return

            label = self._INTENT_LABELS.get(intent, intent.replace("_", " ").title())
            await query.edit_message_text(f"{label}…")
            await self._send_typing(update)

            handler_name = self._INTENT_MAP.get(intent, "_nlp_chat")
            handler = getattr(self, handler_name, self._nlp_chat)
            history = await self._conv.get_context(query.message.chat_id)

            # Build a minimal fake update so handlers can reply normally
            response = await handler(update, ctx, arg, history)
            if response:
                await self._conv.add_message(query.message.chat_id, "assistant", response[:800])
                if len(response) <= 4000:
                    await query.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
                else:
                    for i in range(0, len(response), 4000):
                        await query.message.reply_text(
                            response[i:i + 4000], parse_mode=ParseMode.MARKDOWN
                        )
                        await asyncio.sleep(0.2)
            return

        # Legacy scan callback
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

    async def _cmd_models(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx):
            return
        from models.model_registry import registry as model_registry
        from models.model_router import router

        router_s = router.status()
        uncensored_name = router_s.get("uncensored_model", "")
        active_backend  = router_s.get("active_backend", settings.LLM_BACKEND)

        lines = [
            f"🤖 *Registered LLM Models*\n",
            f"Active backend: `{active_backend}`",
            f"Uncensored fallback: `{uncensored_name}`\n",
        ]
        for m in model_registry.all_models():
            avail   = "✅" if m.available else "❌"
            uncens  = "🔓" if m.uncensored else "-"
            tasks   = ", ".join(t.value for t in m.preferred_tasks)
            lines.append(
                f"{avail} {uncens} `{m.name}`\n"
                f"  Provider: {m.provider.value} | Tasks: {tasks}"
            )
        await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def _cmd_learn_repo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        url = " ".join(ctx.args).strip() if ctx.args else ""
        if not url or not url.startswith("http"):
            await update.message.reply_text(
                "Usage: `/learn_repo <github_url>`\n\n"
                "Example: `/learn_repo https://github.com/user/repo`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(f"📚 Fetching repo knowledge from {url}…")

        chat_id     = update.effective_chat.id
        reply_topic = f"bot.reply.{chat_id}.{int(time.time())}"

        msg = BusMessage(
            topic    = "research.learn_repo",
            sender   = "telegram_bot",
            payload  = {"url": url, "task_id": reply_topic},
            reply_to = reply_topic,
        )
        response = await self._bus.publish_and_wait(msg, timeout=120.0)
        if response is None:
            await update.message.reply_text("⚠️ Timeout waiting for repo knowledge.")
            return

        data = response.payload
        if data.get("error"):
            await update.message.reply_text(f"❌ Error: {data['error']}")
            return

        files   = data.get("files_learned", 0)
        repo    = data.get("repo", url)
        desc    = data.get("description", "")
        result  = (
            f"✅ *Repo knowledge ingested*\n\n"
            f"Repo: `{repo}`\n"
            f"Files learned: {files}\n"
            f"Description: {desc[:200] or 'N/A'}"
        )
        await self._reply_long(update, result, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_add_feature(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update, ctx, admin_required=True):
            return
        description = " ".join(ctx.args).strip() if ctx.args else ""
        if not description:
            await update.message.reply_text(
                "Usage: `/add_feature <description>`\n\n"
                "Example: `/add_feature A tool that checks if a port is open`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(
            f"🔨 Planning feature: _{description[:80]}_…",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            from swarm.swarm_orchestrator import swarm_orchestrator
            task = (
                f"Implement new feature or tool: {description}. "
                "Generate working Python code, test it, and if it's a tool register it "
                "in the tool registry."
            )
            result = await swarm_orchestrator.run(task)
            if len(result) > 3800:
                result = result[:3800] + "\n…[truncated]"
            await update.message.reply_text(
                f"✅ *Feature result:*\n\n{result}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            await update.message.reply_text(f"❌ Feature generation error: {exc}")

    async def _dispatch(self, topic: str, payload: Dict) -> Dict[str, Any]:
        """Publish a bus message directly to a topic and await the response."""
        reply_topic = f"bot.reply.{topic}.{int(time.time())}"
        msg = BusMessage(
            topic    = topic,
            sender   = "telegram_bot",
            payload  = payload,
            reply_to = reply_topic,
        )
        response = await self._bus.publish_and_wait(msg, timeout=90.0)
        if response is None:
            return {"error": "timeout"}
        if isinstance(response, dict):
            return response
        return response.payload if hasattr(response, "payload") else {}

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

    # ------------------------------------------------------------------
    # /create_agent – autonomously generate a new agent
    # ------------------------------------------------------------------

    async def _cmd_create_agent(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            description = " ".join(ctx.args) if ctx.args else ""
            if not description:
                await update.message.reply_text(
                    "🤖 *Create Agent*\n\n"
                    "Usage: `/create_agent <description>`\n\n"
                    "Example: `/create_agent An agent that monitors Tor hidden services for threat intel`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            await update.message.reply_text(
                f"🧬 Generating new agent for: _{description}_\n\n"
                "This may take 30–60 seconds…",
                parse_mode=ParseMode.MARKDOWN,
            )

            result = await self._dispatch("developer.create_agent", {
                "description": description,
                "agent_name": "",
            })

            text = result.get("result", result.get("error", "No response"))
            await self._reply_long(update, text)

        except Exception as exc:
            log.exception("create_agent command error")
            await update.message.reply_text(f"❌ Error: {exc}")

    # ------------------------------------------------------------------
    # /create_tool – dynamically generate a new tool via LLM
    # ------------------------------------------------------------------

    async def _cmd_create_tool(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            description = " ".join(ctx.args) if ctx.args else ""
            if not description:
                await update.message.reply_text(
                    "🔧 *Create Tool*\n\n"
                    "Usage: `/create_tool <description>`\n\n"
                    "Example: `/create_tool A tool that fetches the latest CVEs from NVD for a given keyword`",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            # Ask for confirmation before triggering LLM tool generation
            short = description[:120] + ("…" if len(description) > 120 else "")
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Yes, generate it",
                    callback_data=f"gentool:{description[:200]}",
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="gentool:__cancel__",
                ),
            ]])
            await update.message.reply_text(
                f"🔧 *Generate new tool?*\n\n"
                f"_{short}_\n\n"
                "This will use the LLM to write Python code, test it in a sandbox, "
                "and register it as a live tool available to all agents.\n\n"
                "Proceed?",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )

        except Exception as exc:
            log.exception("create_tool command error")
            await update.message.reply_text(f"❌ Error: {exc}")

    # ------------------------------------------------------------------
    # /dev_sync – sync repo to latest main (DEVELOPMENT_RULES.md §1)
    # ------------------------------------------------------------------

    async def _cmd_dev_sync(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            await update.message.reply_text("🔄 Syncing repository to latest main…")
            from self_healing.dev_lifecycle import dev_lifecycle
            result = await dev_lifecycle.sync_repo()

            nc = result.get("new_commits", [])
            sha_before = result.get("previous_sha", "?")[:8]
            sha_after  = result.get("current_sha",  "?")[:8]
            status     = "✅ Synced" if result.get("success") else "⚠️ Partial"

            commit_lines = "\n".join(
                f"  • `{c['sha'][:8]}` {c['message'][:60]}" for c in nc[:5]
            ) or "  _(no new commits)_"

            msg = (
                f"{status}\n"
                f"SHA: `{sha_before}` → `{sha_after}`\n\n"
                f"*New commits ({len(nc)}):*\n{commit_lines}"
            )
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("dev_sync error")
            await update.message.reply_text(f"❌ {exc}")

    # ------------------------------------------------------------------
    # /dev_health – run health checks and report
    # ------------------------------------------------------------------

    async def _cmd_dev_health(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            await update.message.reply_text("🩺 Running health checks…")
            from self_healing.health_checker import health_checker
            report = await health_checker.check_all()
            icon   = "✅" if report.passed else "❌"
            await self._reply_long(
                update,
                f"{icon} *Health Report* ({report.duration()}s)\n\n"
                f"```\n{report.summary()}\n```",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            log.exception("dev_health error")
            await update.message.reply_text(f"❌ {exc}")

    # ------------------------------------------------------------------
    # /dev_lifecycle <description> – run full dev pipeline for a change
    # ------------------------------------------------------------------

    async def _cmd_dev_lifecycle(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            description = " ".join(ctx.args) if ctx.args else ""
            if not description:
                await update.message.reply_text(
                    "🔁 *Dev Lifecycle*\n\n"
                    "Usage: `/dev_lifecycle <description>`\n\n"
                    "Example: `/dev_lifecycle refactor sandbox timeout handling`\n\n"
                    "This will trigger a full automated dev cycle:\n"
                    "sync → branch → implement → test → health → commit → merge",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            await update.message.reply_text(
                f"🔁 Starting dev lifecycle for:\n_{description}_\n\n"
                "Running: sync → branch → implement → test → health → risk → commit → merge…",
                parse_mode=ParseMode.MARKDOWN,
            )

            result = await self._dispatch("developer.dev_cycle", {
                "description": description,
                "action":      "run_lifecycle",
            })

            text = result.get("summary", result.get("result", result.get("error", "No response")))
            await self._reply_long(
                update,
                f"🔁 *Dev Lifecycle Result*\n\n```\n{text}\n```",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            log.exception("dev_lifecycle error")
            await update.message.reply_text(f"❌ {exc}")

    # ------------------------------------------------------------------
    # /dev_branches – list active feature branches
    # ------------------------------------------------------------------

    async def _cmd_dev_branches(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._guard(update, ctx):
            return
        try:
            from self_healing.git_manager import git_list_branches
            branches = await git_list_branches()
            dev_branches = [b for b in branches if b.startswith("bot/dev/")]
            if not dev_branches:
                await update.message.reply_text("🌿 No active `bot/dev/*` feature branches.")
                return
            lines = "\n".join(f"  • `{b}`" for b in dev_branches)
            await update.message.reply_text(
                f"🌿 *Active feature branches ({len(dev_branches)}):*\n{lines}",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as exc:
            log.exception("dev_branches error")
            await update.message.reply_text(f"❌ {exc}")

    # ------------------------------------------------------------------
    # Personalisation commands
    # ------------------------------------------------------------------

    async def _cmd_profile(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show this user's personalised profile."""
        if not await self._guard(update, ctx):
            return
        user    = update.effective_user
        chat_id = update.effective_chat.id
        try:
            from personalization.personalization_engine import personalization_engine as pe
            summary = await pe.get_profile_summary(chat_id)
            await self._reply_long(update, summary, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("profile error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_plugins(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List available plugins and show which are active for this user."""
        if not await self._guard(update, ctx):
            return
        chat_id = update.effective_chat.id
        try:
            from personalization.personalization_engine import personalization_engine as pe
            msg = await pe.list_plugins_message(chat_id)
            await self._reply_long(update, msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("plugins error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_activate(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Activate a plugin: /activate <plugin_id>"""
        if not await self._guard(update, ctx):
            return
        chat_id = update.effective_chat.id
        args    = ctx.args or []
        if not args:
            await update.message.reply_text(
                "Usage: `/activate <plugin_id>`\nUse /plugins to see available plugins.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        plugin_id = args[0].lower()
        try:
            from personalization.personalization_engine import personalization_engine as pe
            ok, msg = await pe.activate_plugin(chat_id, plugin_id)
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("activate error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_deactivate(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Deactivate a plugin: /deactivate <plugin_id>"""
        if not await self._guard(update, ctx):
            return
        chat_id = update.effective_chat.id
        args    = ctx.args or []
        if not args:
            await update.message.reply_text(
                "Usage: `/deactivate <plugin_id>`\nUse /plugins to see active plugins.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        plugin_id = args[0].lower()
        try:
            from personalization.personalization_engine import personalization_engine as pe
            ok, msg = await pe.deactivate_plugin(chat_id, plugin_id)
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("deactivate error")
            await update.message.reply_text(f"❌ {exc}")

    # ------------------------------------------------------------------
    # Crawler commands
    # ------------------------------------------------------------------

    async def _cmd_crawl_start(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Start all crawlers: /crawl_start [onion|clearnet|irc|news|all]"""
        if not await self._guard(update, ctx, admin_required=True):
            return
        await self._send_typing(update)
        arg = (ctx.args[0] if ctx.args else "all").lower()
        try:
            from crawler.crawler_manager import crawler_manager
            msgs = []
            if arg in ("all", "onion"):
                msgs.append(await crawler_manager.start_onion())
            if arg in ("all", "clearnet"):
                msgs.append(await crawler_manager.start_clearnet())
            if arg in ("all", "irc"):
                msgs.append(await crawler_manager.start_irc())
            if arg in ("all", "news", "newsgroup"):
                msgs.append(await crawler_manager.start_newsgroup())
            if not msgs:
                msgs = ["❓ Unknown target. Use: all | onion | clearnet | irc | news"]
            await update.message.reply_text("\n".join(msgs))
        except Exception as exc:
            log.exception("crawl_start error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_crawl_stop(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Stop all crawlers: /crawl_stop [onion|clearnet|irc|news|all]"""
        if not await self._guard(update, ctx, admin_required=True):
            return
        arg = (ctx.args[0] if ctx.args else "all").lower()
        try:
            from crawler.crawler_manager import crawler_manager
            msgs = []
            if arg in ("all", "onion"):
                msgs.append(await crawler_manager.stop_onion())
            if arg in ("all", "clearnet"):
                msgs.append(await crawler_manager.stop_clearnet())
            if arg in ("all", "irc"):
                msgs.append(await crawler_manager.stop_irc())
            if arg in ("all", "news", "newsgroup"):
                msgs.append(await crawler_manager.stop_newsgroup())
            await update.message.reply_text("\n".join(msgs or ["Done."]))
        except Exception as exc:
            log.exception("crawl_stop error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_crawl_status(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show crawler status and DB counts: /crawl_status"""
        if not await self._guard(update, ctx):
            return
        await self._send_typing(update)
        try:
            from crawler.crawler_manager import crawler_manager
            st  = await crawler_manager.status()
            msg = crawler_manager.format_status(st)
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("crawl_status error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_crawl_add(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Add a URL to the crawl queue: /crawl_add <url>"""
        if not await self._guard(update, ctx, admin_required=True):
            return
        if not ctx.args:
            await update.message.reply_text(
                "Usage: `/crawl_add <url>`\n\n"
                "Examples:\n"
                "  `/crawl_add https://krebsonsecurity.com`\n"
                "  `/crawl_add http://dread...onion/`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        url = ctx.args[0]
        try:
            from crawler.crawler_manager import crawler_manager
            msg = await crawler_manager.add_url(url)
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("crawl_add error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_crawl_search(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Search indexed content: /crawl_search <query>"""
        if not await self._guard(update, ctx):
            return
        if not ctx.args:
            await update.message.reply_text(
                "Usage: `/crawl_search <query>`\n"
                "Searches across crawled pages, IRC logs, and newsgroups.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        query = " ".join(ctx.args)
        await self._send_typing(update)
        try:
            from crawler.crawler_manager import crawler_manager
            results = await crawler_manager.search(query, limit=8)
            if not results:
                await update.message.reply_text(f"🔍 No results for `{query}`.",
                                                parse_mode=ParseMode.MARKDOWN)
                return
            lines = [f"🔍 *Results for* `{query}` *({len(results)})*\n"]
            for r in results:
                rtype = r.get("type", "page")
                snip  = r.get("snippet", "")[:200]
                if rtype == "page":
                    title = r.get("title") or r.get("url", "")[:60]
                    src   = r.get("source_type", "")
                    icon  = "🧅" if src == "onion" else "🌐"
                    lines.append(f"{icon} *{title}*\n`{r.get('url','')[:80]}`\n_{snip}_")
                elif rtype == "irc":
                    lines.append(
                        f"💬 [{r.get('network')}] {r.get('channel')} "
                        f"<{r.get('nick')}>\n_{snip}_"
                    )
                elif rtype == "newsgroup":
                    lines.append(
                        f"📰 [{r.get('newsgroup')}] {r.get('subject','')[:60]}\n_{snip}_"
                    )
                lines.append("")
            await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("crawl_search error")
            await update.message.reply_text(f"❌ {exc}")

    async def _cmd_crawl_onions(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show discovered .onion addresses: /crawl_onions [alive|dead|unknown] [offset]"""
        if not await self._guard(update, ctx):
            return
        await self._send_typing(update)
        args   = ctx.args or []
        status = args[0] if args and args[0] in ("alive", "dead", "timeout", "unknown") else None
        offset = int(args[-1]) if args and args[-1].isdigit() else 0

        try:
            from crawler.crawler_manager import crawler_manager
            onions = await crawler_manager.get_onions(status=status, limit=20)
            total  = (await crawler_manager._db.count_onions())
            if not onions:
                await update.message.reply_text("🧅 No onion addresses found yet.")
                return
            label = f" ({status})" if status else ""
            lines = [f"🧅 *Onion Addresses{label}* — total: {total}\n"]
            for o in onions:
                title  = o.get("title") or ""
                seen   = int(o.get("times_seen", 1))
                st     = o.get("status", "?")
                icon   = {"alive": "✅", "dead": "💀", "timeout": "⏱️"}.get(st, "❓")
                lines.append(
                    f"{icon} `{o['address']}`"
                    + (f"\n   _{title[:60]}_" if title else "")
                    + f"\n   seen: {seen}×"
                )
            lines.append(f"\n_Showing {len(onions)} of {total}. Use /crawl\\_onions alive/dead/unknown_")
            await self._reply_long(update, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:
            log.exception("crawl_onions error")
            await update.message.reply_text(f"❌ {exc}")
