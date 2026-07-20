"""Instancia de Celery: la cola de trabajo del backend.

Redis actúa como broker (recibe los trabajos) y como back-end (guarda estados).
El worker se levanta aparte (ver README). En Windows se usa --pool=solo.
"""
from celery import Celery

from ..config import settings

celery_app = Celery(
    "atlas",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.jobs"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
)
