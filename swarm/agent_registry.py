"""
TASO – Agent Registry

Tracks all active agents, their capabilities, and current workload.
Supports dynamic registration and capability-based agent lookup.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set
from config.logging_config import get_logger

log = get_logger("agent_registry")


@dataclass
class AgentInfo:
    name: str
    description: str
    capabilities: Set[str]           # e.g. {"coding", "analysis", "security"}
    handler: Callable                  # async coroutine to call the agent
    active_tasks: int = 0
    max_concurrent: int = 3
    total_tasks: int = 0
    total_errors: int = 0

    @property
    def available(self) -> bool:
        return self.active_tasks < self.max_concurrent

    @property
    def load(self) -> float:
        if self.max_concurrent == 0:
            return 1.0
        return self.active_tasks / self.max_concurrent


class AgentRegistry:
    """
    Central registry for all agents in the swarm.
    """
    def __init__(self) -> None:
        self._agents: Dict[str, AgentInfo] = {}
        self._lock = asyncio.Lock()

    def register(self, info: AgentInfo) -> None:
        self._agents[info.name] = info
        log.info(f"AgentRegistry: registered '{info.name}' caps={info.capabilities}")

    def get(self, name: str) -> Optional[AgentInfo]:
        return self._agents.get(name)

    def find_by_capability(self, capability: str) -> List[AgentInfo]:
        """Return available agents that have the given capability."""
        return [
            a for a in self._agents.values()
            if capability in a.capabilities and a.available
        ]

    def best_for(self, capability: str) -> Optional[AgentInfo]:
        """Return the least-loaded available agent for a capability."""
        candidates = self.find_by_capability(capability)
        if not candidates:
            return None
        return min(candidates, key=lambda a: a.load)

    def all_agents(self) -> List[AgentInfo]:
        return list(self._agents.values())

    async def increment_load(self, name: str) -> None:
        async with self._lock:
            if name in self._agents:
                self._agents[name].active_tasks += 1
                self._agents[name].total_tasks += 1

    async def decrement_load(self, name: str, error: bool = False) -> None:
        async with self._lock:
            if name in self._agents:
                self._agents[name].active_tasks = max(0, self._agents[name].active_tasks - 1)
                if error:
                    self._agents[name].total_errors += 1

    def status_dict(self) -> Dict:
        return {
            name: {
                "description": a.description,
                "capabilities": list(a.capabilities),
                "active_tasks": a.active_tasks,
                "max_concurrent": a.max_concurrent,
                "total_tasks": a.total_tasks,
                "total_errors": a.total_errors,
                "load_pct": round(a.load * 100),
            }
            for name, a in self._agents.items()
        }


# Singleton
agent_registry = AgentRegistry()


def register_default_agents(agents_map: dict) -> None:
    """Register a dict of {name: agent_instance} into the singleton registry."""
    for name, agent in agents_map.items():
        if hasattr(agent, 'handle'):
            caps = _default_capabilities(name)
            agent_registry.register(AgentInfo(
                name=name,
                description=getattr(agent, 'description', name),
                capabilities=caps,
                handler=agent.handle,
                max_concurrent=3,
            ))


def _default_capabilities(name: str) -> Set[str]:
    mapping = {
        "coordinator": {"general", "planning"},
        "security":    {"security", "analysis"},
        "research":    {"research", "general"},
        "dev":         {"coding", "general"},
        "memory":      {"general"},
        "system":      {"general"},
        "planner":     {"planning", "general"},
        "coder":       {"coding"},
        "analysis":    {"analysis", "research"},
    }
    return mapping.get(name, {"general"})
