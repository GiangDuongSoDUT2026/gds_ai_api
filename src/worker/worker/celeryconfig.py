import os

from kombu import Exchange, Queue

broker_url = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672/")
result_backend = "rpc://"

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]

task_acks_late = True
worker_prefetch_multiplier = 1
worker_max_tasks_per_child = 50

task_track_started = True
task_reject_on_worker_lost = True

default_exchange = Exchange("default", type="direct")
gpu_high_exchange = Exchange("gpu.high", type="direct")
gpu_medium_exchange = Exchange("gpu.medium", type="direct")
db_exchange = Exchange("db", type="direct")

dlq_exchange = Exchange("dlq", type="direct")

task_queues = (
    Queue(
        "gpu.high",
        gpu_high_exchange,
        routing_key="gpu.high",
        queue_arguments={"x-dead-letter-exchange": "dlq", "x-dead-letter-routing-key": "dlq.gpu.high"},
    ),
    Queue(
        "gpu.medium",
        gpu_medium_exchange,
        routing_key="gpu.medium",
        queue_arguments={"x-dead-letter-exchange": "dlq", "x-dead-letter-routing-key": "dlq.gpu.medium"},
    ),
    Queue(
        "db",
        db_exchange,
        routing_key="db",
        queue_arguments={"x-dead-letter-exchange": "dlq", "x-dead-letter-routing-key": "dlq.db"},
    ),
    Queue("dlq.gpu.high", dlq_exchange, routing_key="dlq.gpu.high"),
    Queue("dlq.gpu.medium", dlq_exchange, routing_key="dlq.gpu.medium"),
    Queue("dlq.db", dlq_exchange, routing_key="dlq.db"),
)

task_default_queue = "gpu.medium"
task_default_exchange = "gpu.medium"
task_default_routing_key = "gpu.medium"

task_routes = {
    "worker.tasks.scene_detection.detect_scenes": {"queue": "gpu.high", "routing_key": "gpu.high"},
    "worker.tasks.pipeline.run_pipeline": {"queue": "gpu.high", "routing_key": "gpu.high"},
    "worker.tasks.asr.run_asr": {"queue": "gpu.medium", "routing_key": "gpu.medium"},
    "worker.tasks.ocr.run_ocr": {"queue": "gpu.medium", "routing_key": "gpu.medium"},
    "worker.tasks.clip_embed.run_clip_embed": {"queue": "gpu.medium", "routing_key": "gpu.medium"},
    "worker.tasks.indexing.run_indexing": {"queue": "db", "routing_key": "db"},
}

timezone = "UTC"
enable_utc = True
