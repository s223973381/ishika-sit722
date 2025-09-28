import asyncio
import json
import logging
import os
import sys
from typing import Optional

import aio_pika
from fastapi import Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .db import get_db, init_db, SessionLocal
from .models import Product
from .schemas import ProductCreate, ProductResponse

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# FastAPI
app = FastAPI(title="Product Service API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# RabbitMQ (disabled in CI)
USE_SQLITE_FOR_TESTS = os.getenv("USE_SQLITE_FOR_TESTS", "false").lower() == "true"
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")

rabbitmq_connection: Optional[aio_pika.Connection] = None
rabbitmq_channel: Optional[aio_pika.Channel] = None
rabbitmq_exchange: Optional[aio_pika.Exchange] = None


async def connect_to_rabbitmq():
    if USE_SQLITE_FOR_TESTS:
        logger.info("Skipping RabbitMQ connection in CI.")
        return False
    url = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
    for i in range(5):
        try:
            global rabbitmq_connection, rabbitmq_channel, rabbitmq_exchange
            rabbitmq_connection = await aio_pika.connect_robust(url)
            rabbitmq_channel = await rabbitmq_connection.channel()
            rabbitmq_exchange = await rabbitmq_channel.declare_exchange(
                "ecomm_events", aio_pika.ExchangeType.DIRECT, durable=True
            )
            logger.info("Product Service: Connected to RabbitMQ.")
            return True
        except Exception as e:
            logger.warning(f"RabbitMQ connection failed ({i+1}/5): {e}")
            await asyncio.sleep(5)
    return False


async def close_rabbitmq_connection():
    if rabbitmq_connection:
        await rabbitmq_connection.close()


@app.on_event("startup")
async def startup_event():
    try:
        init_db()
        logger.info("Product Service: Tables ensured.")
    except OperationalError as e:
        logger.critical(f"DB unavailable: {e}")
        sys.exit(1)

    await connect_to_rabbitmq()


@app.on_event("shutdown")
async def shutdown_event():
    await close_rabbitmq_connection()


@app.get("/")
async def root():
    return {"message": "Welcome to the Product Service!"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "product-service"}
