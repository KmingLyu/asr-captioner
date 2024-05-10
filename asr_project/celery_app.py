# celery_app.py
from celery import Celery

app = Celery('tasks', broker='redis://asr-redis:6379/0', backend='redis://asr-redis:6379/0')
