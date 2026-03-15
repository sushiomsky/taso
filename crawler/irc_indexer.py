"""
TASO Crawler — IRC Indexer

Connects to IRC networks, joins security-focused channels,
and indexes all public messages (text only, no DMs).

Uses raw asyncio TCP sockets — no heavy IRC library required.
Reconnects automatically on disconnect.
"""
from __future__ import annotations

import asyncio
import ssl
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from config.logging_config import get_logger
from crawler.crawler_db import CrawlerDB, SRC_IRC
from crawler.seed_urls import IRC_TARGETS
from crawler.text_extractor import extract_onions_from_text

log = get_logger("irc_indexer")

NICK_BASE     = "TASO_research"
REALNAME      = "TASO Security Research Bot"
RECONNECT_SEC = 60
IDLE_TIMEOUT  = 300   # send PING every 5 min if no traffic


@dataclass
class IRCNetwork:
    network: str
    host: str
    port: int
    tls: bool
    channels: List[str]


class IRCClient:
    """
    Minimal IRC client for a single network.
    Joins channels and logs all PRIVMSG to the crawler DB.
    """

    def __init__(self, network: IRCNetwork, db: CrawlerDB) -> None:
        self._net    = network
        self._db     = db
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = True
        self._nick    = f"{NICK_BASE}_{network.network[:4]}"

    async def run(self) -> None:
        while self._running:
            try:
                await self._connect_and_loop()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning(f"[irc:{self._net.network}] error: {exc} — reconnecting in {RECONNECT_SEC}s")
                await asyncio.sleep(RECONNECT_SEC)

    async def stop(self) -> None:
        self._running = False
        if self._writer:
            try:
                self._writer.write(b"QUIT :TASO shutting down\r\n")
                await self._writer.drain()
            except Exception:
                pass

    async def _connect_and_loop(self) -> None:
        log.info(f"[irc:{self._net.network}] connecting to {self._net.host}:{self._net.port}")

        if self._net.tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
            self._reader, self._writer = await asyncio.open_connection(
                self._net.host, self._net.port, ssl=ctx
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self._net.host, self._net.port
            )

        # Register
        await self._send(f"NICK {self._nick}")
        await self._send(f"USER taso 0 * :{REALNAME}")

        last_activity = time.time()
        registered    = False

        while self._running:
            # Idle ping
            if time.time() - last_activity > IDLE_TIMEOUT:
                await self._send(f"PING :{self._net.host}")
                last_activity = time.time()

            try:
                line_bytes = await asyncio.wait_for(self._reader.readline(), timeout=30)
            except asyncio.TimeoutError:
                continue

            if not line_bytes:
                log.warning(f"[irc:{self._net.network}] connection closed by server")
                return

            last_activity = time.time()
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")

            if not line:
                continue

            log.debug(f"[irc:{self._net.network}] << {line[:120]}")

            # Handle PING
            if line.startswith("PING"):
                await self._send("PONG " + line[5:])
                continue

            parts  = line.split(" ", 3)
            prefix = parts[0] if parts[0].startswith(":") else ""
            cmd    = parts[1] if prefix else parts[0]
            rest   = parts[2:] if prefix else parts[1:]

            # Server welcome — join channels
            if cmd == "001" and not registered:
                registered = True
                log.info(f"[irc:{self._net.network}] registered as {self._nick}")
                for ch in self._net.channels:
                    await self._send(f"JOIN {ch}")
                    await asyncio.sleep(1)

            # Nickname in use
            elif cmd == "433":
                self._nick += "_"
                await self._send(f"NICK {self._nick}")

            # Public message
            elif cmd == "PRIVMSG":
                channel = rest[0] if rest else ""
                msg_raw = rest[1].lstrip(":") if len(rest) > 1 else ""
                nick    = _parse_nick(prefix)

                # Only index channel messages (not DMs)
                if channel.startswith("#") and msg_raw:
                    await self._store(channel, nick, msg_raw)

            # Kicked — rejoin
            elif cmd == "KICK":
                kicked_ch = rest[0] if rest else ""
                if kicked_ch:
                    await asyncio.sleep(5)
                    await self._send(f"JOIN {kicked_ch}")

    async def _store(self, channel: str, nick: str, message: str) -> None:
        # Strip IRC colour/format codes
        clean = re.sub(r"\x03(?:\d{1,2}(?:,\d{1,2})?)?|\x02|\x0f|\x16|\x1d|\x1f", "", message)
        await self._db.save_irc_message(
            network=self._net.network,
            channel=channel,
            nick=nick,
            message=clean,
        )

        # Check for any .onion addresses and register them
        for onion in extract_onions_from_text(clean):
            is_new = await self._db.register_onion(onion, tags=["irc-discovered"])
            if is_new:
                log.info(f"[irc:{self._net.network}] 🧅 .onion found in {channel}: {onion}")
                await self._db.enqueue(
                    f"http://{onion}/", "onion", priority=6, depth=0,
                    referrer=f"irc://{self._net.network}/{channel}",
                )

    async def _send(self, line: str) -> None:
        if self._writer:
            self._writer.write((line + "\r\n").encode("utf-8", errors="replace"))
            await self._writer.drain()


def _parse_nick(prefix: str) -> str:
    """Extract nick from ':nick!user@host' prefix."""
    if prefix.startswith(":"):
        prefix = prefix[1:]
    return prefix.split("!")[0] if "!" in prefix else prefix


class IRCIndexer:
    """
    Manages connections to all configured IRC networks.
    """

    def __init__(self, db: CrawlerDB) -> None:
        self._db      = db
        self._clients: List[IRCClient] = []
        self._tasks:   List[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        log.info(f"IRCIndexer starting ({len(IRC_TARGETS)} networks)…")
        self._running = True

        for cfg in IRC_TARGETS:
            net    = IRCNetwork(**cfg)
            client = IRCClient(net, self._db)
            self._clients.append(client)
            t = asyncio.create_task(client.run())
            self._tasks.append(t)

        log.info("IRCIndexer: all clients launched")

    async def stop(self) -> None:
        self._running = False
        for client in self._clients:
            await client.stop()
        for t in self._tasks:
            t.cancel()
        log.info("IRCIndexer stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    async def status(self) -> Dict:
        rows = await self._db.get_irc_messages(limit=5)
        latest = rows[0] if rows else {}
        total  = len(await self._db.get_irc_messages(limit=100_000))
        return {
            "networks": len(IRC_TARGETS),
            "clients":  len(self._clients),
            "messages_indexed": total,
            "latest": latest,
        }
