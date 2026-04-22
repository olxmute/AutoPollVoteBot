import asyncio
import gc
import os
import tempfile

import pytest

from src.database import apply_migrations

# Python 3.14 removes the implicit "running event loop" that older versions
# created lazily.  Pyrogram's sync shim (imported at module level via
# `from pyrogram import Client`) calls asyncio.get_event_loop() during module
# loading.  Setting an explicit loop before any pyrogram import avoids the
# RuntimeError during test collection.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        apply_migrations(db_path)
        yield db_path
    finally:
        # Force garbage collection to close any lingering SQLite connections
        # (yoyo keeps the backend connection alive on Windows).
        gc.collect()
        try:
            os.unlink(db_path)
        except OSError:
            # On Windows the file may still be held by yoyo's backend;
            # ignore cleanup errors — the temp file will be removed on reboot.
            pass
