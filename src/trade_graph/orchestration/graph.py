"""Thin LangGraph wrapper. Checkpoints are not the financial ledger."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph


def run_once(task_id: str, checkpoint_path: Path, worker: Callable[[str], None]) -> dict:
    builder = StateGraph(dict)

    def node(state: dict) -> dict:
        worker(state["task_id"])
        return {"task_id": state["task_id"], "visits": int(state.get("visits", 0)) + 1}

    builder.add_node("run", node)
    builder.add_edge(START, "run")
    builder.add_edge("run", END)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        graph = builder.compile(checkpointer=SqliteSaver(connection))
        return graph.invoke(
            {"task_id": task_id, "visits": 0},
            {"configurable": {"thread_id": task_id}},
        )
    finally:
        connection.close()
