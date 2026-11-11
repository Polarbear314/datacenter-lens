"""
database.py
-----------
Sets up the SQLAlchemy engine and session factory.
All other modules import `get_db` (the FastAPI dependency) and `Base` (for models).
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Read the database URL from the environment.
# In Docker this is set by docker-compose.yml.
# When running locally without Docker, set it in your .env file.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://dclens:dclens_dev@localhost:5432/dclens")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # Reconnect if connection was dropped
    echo=False,           # Set to True to log all SQL queries (useful for debugging)
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """All SQLAlchemy models inherit from this class."""
    pass


def get_db():
    """
    FastAPI dependency. Yields a database session and closes it when the
    request is done (even if an error occurred).

    Usage in a route:
        @router.get("/sites")
        def list_sites(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
