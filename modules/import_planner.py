"""Dependency-aware planning for schema creation and future data loading."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def plan_import(tables: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [table["id"] for table in tables]
    by_id = {table["id"]: table for table in tables}
    selected = [rel for rel in relationships if rel.get("approved")]
    dependencies: dict[str, set[str]] = {table_id: set() for table_id in ids}
    reverse: dict[str, set[str]] = defaultdict(set)
    for rel in selected:
        child = rel["child_table_id"]
        parent = rel["parent_table_id"]
        if child in dependencies and parent in dependencies and child != parent:
            dependencies[child].add(parent)
            reverse[parent].add(child)

    # Kahn: parents (no outgoing dependency) precede their dependent children.
    indegree = {table_id: len(dependencies[table_id]) for table_id in ids}
    queue = deque(sorted([table_id for table_id in ids if indegree[table_id] == 0], key=lambda i: by_id[i]["name"].casefold()))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for child in sorted(reverse.get(current, set()), key=lambda i: by_id[i]["name"].casefold()):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cyclic = sorted(set(ids) - set(order), key=lambda i: by_id[i]["name"].casefold())
    if cyclic:
        # Stable fallback order: constraints are added after all tables exist.
        order.extend(cyclic)
    return {
        "creation_order": [{"table_id": i, "table": by_id[i]["name"]} for i in order],
        "data_load_order": [{"table_id": i, "table": by_id[i]["name"]} for i in order],
        "constraint_order": "Create every table first, then add approved foreign-key constraints with ALTER TABLE. This also supports cyclic references.",
        "cycle_detected": bool(cyclic),
        "cyclic_tables": [by_id[i]["name"] for i in cyclic],
        "approved_foreign_keys": len(selected),
    }
