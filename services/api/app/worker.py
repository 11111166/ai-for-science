from celery import Celery

from .config import get_settings
from .jobs import execute_job

settings = get_settings()
celery_app = Celery("drug_discovery", broker=settings.redis_url)
celery_app.conf.update(task_track_started=True, task_ignore_result=True)


@celery_app.task(name="drug_discovery.run_job")
def run_job(job_id: str):
    execute_job(job_id)

