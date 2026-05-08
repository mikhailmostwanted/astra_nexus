from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from astra_nexus.db.models import Base


def _prepare_sqlite_path(database_url: str) -> None:
    if not database_url.startswith("sqlite:///") or database_url == "sqlite:///:memory:":
        return

    raw_path = database_url.removeprefix("sqlite:///")
    Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    _prepare_sqlite_path(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, connect_args=connect_args)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def init_db(session_factory: sessionmaker[Session]) -> None:
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)
