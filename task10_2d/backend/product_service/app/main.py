import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import List, Optional

import aio_pika
from fastapi import Depends, FastAPI, File, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy.orm import Session

from azure.storage.blob import BlobSasPermissions, BlobServiceClient, ContentSettings, generate_blob_sas

from .db import Base, engine, get_db, SessionLocal, init_db
from .models import Product
from .schemas import ProductCreate, ProductResponse, ProductUpdate, StockDeductRequest

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)

# --- Azure ---
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
AZURE_STORAGE_ACCOUNT_KEY = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
AZURE_STORAGE_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "product-images")

blob_service_client: Optional[BlobServiceClient] = None
if AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY:
    try:
        blob_service_client = BlobServiceClient(
            account_url=f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
            credential=AZURE_STORAGE_ACCOUNT_KEY,
        )
        container_client = blob_service_client.get_container_client(AZURE_STORAGE_CONTAINER_NAME)
        container_client.create_container()
    except Exception as e:
        logger.warning(f"Azure init failed: {e}")
        blob_service_client = None

# --- RabbitMQ ---
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")

rabbitmq_connection: Optional[aio_pika.Connection] = None
rabbitmq_channel: Optional[aio_pika.Channel] = None
rabbitmq_exchange: Optional[aio_pika.Exchange] = None

# --- FastAPI ---
app = FastAPI(title="Product Service API", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# --- RabbitMQ Helpers ---
async def connect_to_rabbitmq():
    global rabbitmq_connection, rabbitmq_channel, rabbitmq_exchange
    rabbitmq_url = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
    for _ in range(10):
        try:
            rabbitmq_connection = await aio_pika.connect_robust(rabbitmq_url)
            rabbitmq_channel = await rabbitmq_connection.channel()
            rabbitmq_exchange = await rabbitmq_channel.declare_exchange(
                "ecomm_events", aio_pika.ExchangeType.DIRECT, durable=True
            )
            return True
        except Exception as e:
            logger.warning(f"RabbitMQ connection failed: {e}")
            await asyncio.sleep(5)
    return False

async def close_rabbitmq_connection():
    if rabbitmq_connection:
        await rabbitmq_connection.close()

async def publish_event(routing_key: str, data: dict):
    if not rabbitmq_exchange:
        return
    msg = aio_pika.Message(
        body=json.dumps(data).encode("utf-8"),
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    await rabbitmq_exchange.publish(msg, routing_key=routing_key)

async def consume_order_placed_events(db_session_factory):
    queue_name = "product_service_order_placed_queue"
    try:
        queue = await rabbitmq_channel.declare_queue(queue_name, durable=True)
        await queue.bind(rabbitmq_exchange, routing_key="order.placed")

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    data = json.loads(message.body.decode())
                    order_id = data.get("order_id")
                    items = data.get("items", [])
                    session = db_session_factory()
                    try:
                        # deduct stock logic...
                        for item in items:
                            product = session.query(Product).filter(Product.product_id == item["product_id"]).first()
                            if not product or product.stock_quantity < item["quantity"]:
                                await publish_event("product.stock.deduction.failed", {"order_id": order_id})
                                session.rollback()
                                break
                            product.stock_quantity -= item["quantity"]
                            session.add(product)
                        else:
                            session.commit()
                            await publish_event("product.stock.deducted", {"order_id": order_id})
                    except Exception as e:
                        session.rollback()
                        logger.error(f"Stock deduction failed: {e}")
                    finally:
                        session.close()
    except Exception as e:
        logger.critical(f"Consumer error: {e}")

# --- Startup/Shutdown ---
@app.on_event("startup")
async def startup_event():
    try:
        init_db()  # ✅ ensures tables exist
    except Exception as e:
        logger.critical(f"DB init failed: {e}", exc_info=True)
        sys.exit(1)

    if await connect_to_rabbitmq():
        asyncio.create_task(consume_order_placed_events(SessionLocal))

@app.on_event("shutdown")
async def shutdown_event():
    await close_rabbitmq_connection()

# --- Endpoints ---
@app.get("/")
async def root():
    return {"message": "Welcome to the Product Service!"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "product-service"}

# (👉 keep your CRUD product endpoints same as before)
