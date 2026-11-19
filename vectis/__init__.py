"""Vectis: Ultra-lightweight autonomous multi-agent framework."""

__version__ = "0.3.0"

from init_db import init_database
from heartbeat import run_daemon, run_single_heartbeat
from agent_engine import process_ticket, claim_ticket
from tools import get_ticket_workspace, dispatch_tool, execute_tool_calls, check_agent_tool_permission
from memory import record_memory, search_memories, list_memories
import config

__all__ = [
    "__version__",
    "init_database",
    "run_daemon",
    "run_single_heartbeat",
    "process_ticket",
    "claim_ticket",
    "get_ticket_workspace",
    "dispatch_tool",
    "execute_tool_calls",
    "check_agent_tool_permission",
    "record_memory",
    "search_memories",
    "list_memories",
    "config",
]

if __name__ == "__main__":
    from main import main
    main()
