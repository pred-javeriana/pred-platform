"""Task-table run orchestration: consumes pending tasks.

Each completed task commits transactionally so the worker survives crashes without losing work.
"""
