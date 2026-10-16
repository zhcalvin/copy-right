from fastapi import APIRouter

from app.api import credentials, health, jobs, projects, providers, settings, setup, storyboard

api_router = APIRouter(prefix="/api")
for module in (health, projects, storyboard, providers, jobs, setup, settings, credentials):
    api_router.include_router(module.router)
