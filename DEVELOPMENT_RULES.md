# Autonomous Development & Git Versioning Policy

> **TASO System Policy** — Governs all autonomous code modifications, tool
> additions, and architectural changes made by the self-improving bot.

---

## Core Principle

TASO operates as a **self-developing system** whose codebase is managed through
strict Git-based version control, automated testing, and self-healing safeguards.

Every code modification must follow a safe development lifecycle before being
merged into the main codebase. **System stability always takes priority over
innovation.**

---

## 1. Git Synchronisation Rule

Before making any modification, the bot **must**:

1. Fetch updates from the remote repository
2. Checkout the main branch
3. Pull the latest changes
4. Analyse the changed files and commit messages
5. Update internal memory about architecture changes

```bash
git fetch origin
git checkout main
git pull origin main
```

The bot must **never modify outdated code**. All development occurs on the
latest repository state.

---

## 2. Development Branching

All autonomous development occurs in temporary feature branches.

```bash
git checkout -b bot/dev/<feature_name>
```

Examples:
- `bot/dev/refactor-agent-orchestrator`
- `bot/dev/improve-sandbox-security`
- `bot/dev/add-new-tool-system`

> **The bot must never commit directly to `main`.**

---

## 3. Development Lifecycle Pipeline

Every modification follows this exact pipeline:

```
sync repo
  ↓
create feature branch  (bot/dev/<name>)
  ↓
implement change
  ↓
static analysis        (syntax + bandit)
  ↓
automated tests        (pytest -x)
  ↓
runtime validation     (health checks)
  ↓
risk scoring           (block if CRITICAL)
  ↓
commit with message    (conventional commit format)
  ↓
push branch
  ↓
merge / pull request → main
  ↓
tag stable version     (bot-vMAJOR.MINOR.PATCH)
```

Implemented in: `self_healing/dev_lifecycle.py` → `DevLifecycle.run_full_pipeline()`

---

## 4. Mandatory Testing Before Commit

The following test categories are **required** before any commit is allowed.

### 4a. Core Feature Tests

The bot verifies that all critical system functions still work:

| System | What is checked |
|--------|----------------|
| Imports | All key modules importable |
| Tools | ToolRegistry discovers ≥ 1 tool |
| Memory | KnowledgeDB connects, VectorStore loads |
| Sandbox | `run_tool({})` executes without error |
| Telegram | Bot token can reach the API |
| Agents | All registered agents start cleanly |

### 4b. Integration Tests

Verified interactions:
- Agent → tool execution
- Planner → DeveloperAgent
- DeveloperAgent → sandbox testing
- Sandbox → git commit workflow

### 4c. Regression Tests

- Preserved across all refactors
- Extended whenever a new feature is added
- Automatically executed before every commit

**If any test fails → commit is blocked → self-healing rollback triggers.**

---

## 5. Test Suite Structure

```
tests/
  test_core_bot.py           – imports, settings, logging
  test_telegram_commands.py  – command parsing, NLP routing
  test_agent_swarm.py        – agent startup, bus pub/sub
  test_tool_registry.py      – tool discovery, execution
  test_memory_system.py      – vector store, knowledge DB
  test_sandbox_execution.py  – subprocess sandbox
  test_git_self_healing.py   – git ops, dev lifecycle, health checks
  test_dynamic_tool_pipeline.py – tool generation pipeline
  test_new_modules.py        – monitoring, audit log
```

Every major subsystem **must** have corresponding tests.

---

## 6. Self-Healing Safety Checks

Before committing any change, the bot runs a **health check stage**
(`self_healing/health_checker.py`):

| Check | Description |
|-------|-------------|
| `check_imports` | Critical modules importable without errors |
| `check_tools` | ToolRegistry loads ≥ 1 static tool |
| `check_memory` | KnowledgeDB connects, VectorStore initialises |
| `check_sandbox` | Sandbox subprocess executes a no-op tool |
| `check_telegram` | Bot token responds to getMe API call |
| `check_agents` | All registered agent classes instantiate |

If any **critical** check fails:
1. Changes are **not committed**
2. Rollback to last stable version triggers
3. Admin is notified via Telegram

---

## 7. Commit Standards

All commits follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): short description

Body explaining what changed and why.

Test results: N passed, M failed
Files changed: file1.py, file2.py
```

**Types:**

| Type | Usage |
|------|-------|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code restructuring |
| `test` | Adding/updating tests |
| `docs` | Documentation |
| `perf` | Performance improvement |
| `chore` | Maintenance |

---

## 8. Automatic Version Tagging

Stable commits are tagged with semantic versions:

```bash
git tag bot-v1.3.4 -m "description"
git push origin bot-v1.3.4
```

**Version format: `MAJOR.MINOR.PATCH`**

| Bump | When |
|------|------|
| `PATCH` | Bug fix, small tool addition |
| `MINOR` | New feature, new agent |
| `MAJOR` | Architecture change, breaking change |

Implemented in: `self_healing/version_tagger.py` → `VersionTagger`

---

## 9. Continuous Self-Improvement Loop

The bot continuously:

1. Analyses its own code for inefficiencies
2. Proposes improvements
3. Tests refactors in a feature branch
4. Commits only when all gates pass

**Constraint: no change may bypass the test pipeline.**

The loop runs at configurable intervals (`SELF_IMPROVE_INTERVAL_HOURS`).

---

## 10. Self-Healing Rollback

If runtime errors occur after deployment, the system automatically:

```bash
git revert <last commit>
# OR
git checkout <last stable tag>
```

Then restarts the bot and logs the rollback event.

Rollback is **debounced** (minimum 5 minutes between rollbacks) to prevent
cascading loops.

Implemented in: `self_healing/rollback_manager.py` → `RollbackManager`

---

## 11. Repository Bootstrapping

On restart or environment reset, the bot restores itself:

```bash
git clone <repo>
git checkout main
git pull origin main
pip install -r requirements.txt
python main.py
```

If the latest commit fails startup health checks:
1. Checkout previous stable tag
2. Log the downgrade event
3. Alert admin

Implemented in: `self_healing/deploy_manager.py` → `DeployManager.bootstrap()`

---

## 12. Test Maintenance Rule

**Every new feature must include tests.**

The bot automatically:
1. Generates test stubs when creating new tools/agents
2. Adds tests to the suite
3. Verifies tests pass before committing

**No feature exists without tests.**

---

## 13. Logging and Audit

All development operations are logged with:

```
timestamp | commit_sha | change_description | test_result | deployment_status
```

Log files:
- `logs/self_improvement.log` — autonomous changes
- `logs/security.log` — sandbox/security checks
- `logs/agent.log` — agent activity
- `logs/error.log` — failures and rollbacks

Structured audit trail: `memory/audit_log.py` → `AuditLog`

---

## 14. Risk Scoring

Before deployment, every change is risk-scored (`self_healing/risk_scorer.py`):

| Score | Band | Action |
|-------|------|--------|
| 0.0 – 2.9 | LOW | Auto-deploy |
| 3.0 – 5.9 | MEDIUM | Deploy with audit log |
| 6.0 – 7.9 | HIGH | Require human approval |
| 8.0 – 10.0 | CRITICAL | **Block auto-deploy, alert admin** |

Protected modules (never auto-modified):
- `config/`
- `sandbox/`
- `self_healing/`
- `bot/telegram_bot.py`

---

## 15. Safety Principles

1. **Never break core functionality** — stability > innovation
2. **Always run the full test suite** before committing
3. **Always sync repository** before starting a change
4. **Always maintain rollback capability** — every deployment is reversible
5. **Branch isolation** — no direct commits to `main`
6. **Audit everything** — every action is logged with context
7. **Test every feature** — no untested code in production
