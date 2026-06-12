from redis import Redis
from rq import Queue
from flask import current_app

def get_redis() -> Redis:
    return Redis.from_url(current_app.config["REDIS_URL"])

def get_queue(name: str = "default") -> Queue:
    return Queue(name, connection=get_redis(), default_timeout=900)
