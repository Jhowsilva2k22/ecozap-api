web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: celery -A app.queues.tasks.celery_app worker --loglevel=info -Q messages,learning
