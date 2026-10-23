from app.db import sqlite_journal_mode


def test_local_disk_uses_wal(monkeypatch):
    monkeypatch.delenv("TK_SQLITE_JOURNAL", raising=False)
    assert sqlite_journal_mode("./data/turnkeeper.db") == "WAL"


def test_azure_home_share_uses_rollback_journal(monkeypatch):
    monkeypatch.delenv("TK_SQLITE_JOURNAL", raising=False)
    assert sqlite_journal_mode("/home/data/turnkeeper.db") == "DELETE"


def test_override_wins(monkeypatch):
    monkeypatch.setenv("TK_SQLITE_JOURNAL", "wal")
    assert sqlite_journal_mode("/home/data/turnkeeper.db") == "WAL"
