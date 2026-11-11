"""
database_sqlite.py
------------------
SQLite version of database.py — for running WITHOUT Docker or PostgreSQL.

SQLite is a file-based database that requires zero installation.
Python ships with it built-in. This is perfect for local development.

To use this instead of PostGIS:
  1. Rename database.py → database_postgis.py
  2. Rename database_sqlite.py → database.py
  3. pip3 install -r requirements_lite.txt
  4. uvicorn main:app --reload

The geometry columns are stored as GeoJSON text (since SQLite has no
spatial type). This is fine for the MVP — PostGIS is only needed when
we start doing spatial queries like "find all sites within 5 miles."
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Store the DB file in the backend directory alongside the code
DB_PATH = os.path.join(os.path.dirname(__file__), "dclens_dev.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # Required for SQLite + FastAPI
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
