from celery import Celery

app = Celery("gds_worker")
app.config_from_object("worker.celeryconfig")
app.autodiscover_tasks(["worker.tasks"])
