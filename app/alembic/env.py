"""
Alembic Environment Configuration
Location: app/alembic/env.py

Tables managed by this migration:
  - orientation_sessions    (local analytics)
  - orientation_participants (local attendance tracking)
  - event_logs              (local audit trail)

NOT managed here:
  - leads  ← removed; SGP Lead lives in ERPNext only

Run from project root:
    alembic upgrade head
    alembic revision --autogenerate -m "description"
"""

import asyncio
import sys
import os
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# ── Ensure project root is on sys.path so `app.*` imports resolve ─────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ── Import all ORM models BEFORE accessing Base.metadata ─────────────────────
# Only import models that represent LOCAL PostgreSQL tables.
# Do NOT import Lead — it is managed by ERPNext, not this database.
from app.config.database import Base                                            # noqa
from app.orientation.models import OrientationSession, OrientationParticipant  # noqa
from app.events.logger import EventLog                                          # noqa
from app.casesheet.models import CasesheetSession, CasesheetDraft               # noqa

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# ── Pull DB URL from app settings (single source of truth) ───────────────────
from app.config.settings import settings
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


# ── Offline migrations ────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations ─────────────────────────────────────────────────────────
def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()