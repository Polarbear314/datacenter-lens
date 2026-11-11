"""
main.py
-------
FastAPI application entry point.

Run locally:  uvicorn main:app --reload
API docs:     http://localhost:8000/docs  (auto-generated Swagger UI)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base
import models  # Ensure all models are registered before create_all

# Create all database tables if they don't exist yet.
# In production you'd use Alembic migrations instead, but for MVP
# this is fine and makes the first `docker compose up` work immediately.
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="DataCenter Lens API",
    description="Estimate and explain local resource impacts of data centers.",
    version="0.1.0",
)

# Allow the frontend (running on localhost:3000) to call the API.
# In production, replace "*" with your actual domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
from routers.sites import router as sites_router
from routers.scrape import router as scrape_router
app.include_router(sites_router)
app.include_router(scrape_router)


@app.get("/")
def root():
    return {
        "name": "DataCenter Lens API",
        "version": "0.1.0",
        "docs": "/docs",
        "status": "ok",
    }


@app.get("/health")
def health():
    """Simple health check endpoint used by docker-compose healthcheck."""
    return {"status": "healthy"}
