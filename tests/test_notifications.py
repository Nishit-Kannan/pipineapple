"""Smoke tests for the Notifications service."""

from __future__ import annotations

from app.services.notifications import NotificationsService


def test_add_and_list():
    n = NotificationsService(max_entries=10)
    e1 = n.info("first")
    e2 = n.warning("second", source="recon")
    items = n.list()
    # Most recent first (deque appendleft)
    assert items[0]["id"] == e2["id"]
    assert items[0]["severity"] == "warning"
    assert items[0]["source"] == "recon"
    assert items[1]["id"] == e1["id"]
    assert items[1]["severity"] == "info"
    assert all(item["read"] is False for item in items)


def test_unread_count_loud_only():
    n = NotificationsService(max_entries=10)
    n.info("quiet")
    n.warning("loud")
    n.error("loud")
    n.success("loud")
    n.unknown("quiet")
    # Loud-only counts warning + error + success
    assert n.unread_count(loud_only=True) == 3
    # All counts everything
    assert n.unread_count(loud_only=False) == 5


def test_mark_all_read():
    n = NotificationsService(max_entries=10)
    n.warning("x")
    n.error("y")
    assert n.unread_count() == 2
    n.mark_all_read()
    assert n.unread_count() == 0
    assert all(item["read"] for item in n.list())


def test_clear():
    n = NotificationsService(max_entries=10)
    n.info("x")
    n.warning("y")
    assert len(n.list()) == 2
    n.clear()
    assert n.list() == []
    assert n.unread_count() == 0


def test_buffer_capped():
    n = NotificationsService(max_entries=3)
    for i in range(5):
        n.info(f"msg {i}")
    items = n.list()
    assert len(items) == 3
    # Oldest dropped — newest first
    assert items[0]["message"] == "msg 4"
    assert items[-1]["message"] == "msg 2"
