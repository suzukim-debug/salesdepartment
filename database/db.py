from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from config import settings
from database.models import Base

def _sqlite_connect_args():
    if "sqlite" not in settings.database_url:
        return {}
    return {"check_same_thread": False, "timeout": 30}

def _on_connect(dbapi_con, con_record):
    if hasattr(dbapi_con, "execute"):
        try:
            dbapi_con.execute("PRAGMA journal_mode=WAL")
            dbapi_con.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass

from sqlalchemy import event

engine = create_engine(
    settings.database_url,
    connect_args=_sqlite_connect_args(),
)

if "sqlite" in settings.database_url:
    event.listen(engine, "connect", _on_connect)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_db_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
