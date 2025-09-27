import asyncio
import json
import logging
import os
import sys
import time
from decimal import Decimal
from typing import List, Optional

import aio_pika
import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from .db import Base, SessionLocal, engine, get_db, init_db
from .models import Order, OrderItem
from .schemas import (
    OrderCreate,
    OrderItemResponse,
    OrderResponse,
    OrderStatusUpdate,
    OrderUpdate,
)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)

# --- Service URLs ---
CUSTOMER_SERVICE_URL = os.getenv("CUSTOMER_SERVICE_URL", "http://localhost:8002")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://localhost:8001")
logger.info(f"Order Service: Customer Service URL: {CUSTOMER_SERVICE_URL}")
logger.info(f"Order Service: Product Service URL: {PRODUCT_SERVICE_URL}")

# --- RabbitMQ Config ---
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")

rabbitmq_connection: Optional[aio_pika.Connection] = None
rabbitmq_channel: Optional[aio_pika.Channel] = None
rabbitmq_exchange: Optional[aio_pika.Exchange] = None

# --- FastAPI App ---
app = FastAPI(
    title="Order Service API",
    description="Manages orders for mini-ecommerce app",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- RabbitMQ Helpers ---
async def connect_to_rabbitmq():
    global rabbitmq_connection, rabbitmq_channel, rabbitmq_exchange
    rabbitmq_url = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
    for i in range(10):
        try:
            rabbitmq_connection = await aio_pika.connect_robust(rabbitmq_url)
            rabbitmq_channel = await rabbitmq_connection.channel()
            rabbitmq_exchange = await rabbitmq_channel.declare_exchange(
                "ecomm_events", aio_pika.ExchangeType.DIRECT, durable=True
            )
            logger.info("Order Service: Connected to RabbitMQ.")
            return True
        except Exception as e:
            logger.warning(f"Order Service: RabbitMQ connection failed: {e}")
            await asyncio.sleep(5)
    return False


async def close_rabbitmq_connection():
    if rabbitmq_connection:
        await rabbitmq_connection.close()


async def publish_event(routing_key: str, message_data: dict):
    if not rabbitmq_exchange:
        logger.error("RabbitMQ not available")
        return
    message = aio_pika.Message(
        body=json.dumps(message_data).encode("utf-8"),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await rabbitmq_exchange.publish(message, routing_key=routing_key)

# --- Startup/Shutdown ---
@app.on_event("startup")
async def startup_event():
    try:
        init_db()  # ✅ ensures tables exist
        logger.info("Order Service: Tables initialized.")
    except Exception as e:
        logger.critical(f"DB init failed: {e}", exc_info=True)
        sys.exit(1)

    if await connect_to_rabbitmq():
        asyncio.create_task(consume_stock_events(SessionLocal))

@app.on_event("shutdown")
async def shutdown_event():
    await close_rabbitmq_connection()

# --- Endpoints ---
@app.get("/")
async def read_root():
    return {"message": "Welcome to the Order Service!"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "order-service"}

# (👉 keep the rest of your CRUD/order endpoints exactly as you had them)
