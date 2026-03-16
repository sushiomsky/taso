# TASO API Surfaces

This document summarizes TASO's operational interfaces: Telegram commands, bus topics, and crawler endpoints used internally by bot commands.

## Telegram command API

Public commands:

- `/status`
- `/agents`
- `/tools`
- `/memory <query>`
- `/system`
- `/profile`
- `/plugins`
- `/crawl_status`
- `/crawl_search <query>`
- `/crawl_onions [alive|dead|timeout|unknown]`

Admin-only commands:

- `/scan_repo <path_or_url>`
- `/security_scan <target>`
- `/code_audit`
- `/threat_intel <topic>`
- `/update_self`
- `/logs [category]`
- `/run_swarm_task <task>`
- `/dev_sync`
- `/dev_health`
- `/dev_lifecycle <description>`
- `/dev_branches`
- `/config`
- `/feature <name> <on|off>`
- `/agent_toggle <agent> <on|off>`
- `/model <show|backend|slot|enable|disable> ...`
- `/config_apply`
- `/crawl_start [all|onion|clearnet|irc|news]`
- `/crawl_stop [all|onion|clearnet|irc|news]`
- `/crawl_add <url>`

## Message bus API

The Telegram bot dispatches task requests to:

- `coordinator.task` with payload:
  - `command`
  - `args`
  - `reply_to_chat`

Coordinator routes to agent topics:

- `security.scan_repo`
- `security.full_scan`
- `security.code_audit`
- `research.threat_intel`
- `dev.update_self`
- `system.status`
- `memory.query`

Each request uses `reply_to` and resolves through `publish_and_wait(...)` for request/response behavior.

## Crawler manager interface

`crawler/crawler_manager.py` exposes runtime methods used by Telegram commands:

- `start_onion()`, `stop_onion()`
- `start_clearnet()`, `stop_clearnet()`
- `start_irc()`, `stop_irc()`
- `start_newsgroup()`, `stop_newsgroup()`
- `add_url(url, priority=8)`
- `status()`
- `search(query, limit=10)`
- `get_onions(status=None, limit=50)`

## Search API

Crawler search is backed by SQLite FTS5 in `crawler/crawler_db.py`:

- `pages_fts` over crawled web pages
- `irc_fts` over IRC messages
- `ng_fts` over newsgroup posts

Unified query entrypoint:

- `CrawlerDB.search(query, limit=20, source_types=None)`
