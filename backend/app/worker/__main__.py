"""Standalone worker process: python -m app.worker"""
import logging
import threading

from app.db import connect, init_db

from .runner import worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
conn = connect()
init_db(conn)
conn.close()
worker_loop(threading.Event())
