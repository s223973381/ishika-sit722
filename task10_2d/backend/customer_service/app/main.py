import logging
import os
import sys
import time
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from .db import get_db, init_db
from .models import Customer
from .schemas import CustomerCreate, CustomerResponse, CustomerUpdate

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)

# FastAPI App
app = FastAPI(title="Customer Service API", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# Startup
@app.on_event("startup")
async def startup_event():
    retries, delay = 10, 5
    for i in range(retries):
        try:
            init_db()
            logger.info("Customer Service: Tables ensured.")
            break
        except OperationalError as e:
            logger.warning(f"DB connection failed (attempt {i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(delay)
            else:
                logger.critical("DB unavailable after retries. Exiting.")
                sys.exit(1)

# Health + Root
@app.get("/")
async def root():
    return {"message": "Welcome to the Customer Service!"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "customer-service"}

# CRUD Endpoints
@app.post("/customers/", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
async def create_customer(customer: CustomerCreate, db: Session = Depends(get_db)):
    db_customer = Customer(
        email=customer.email,
        password_hash=customer.password,
        first_name=customer.first_name,
        last_name=customer.last_name,
        phone_number=customer.phone_number,
        shipping_address=customer.shipping_address,
    )
    try:
        db.add(db_customer)
        db.commit()
        db.refresh(db_customer)
        return db_customer
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered.")
    except Exception as e:
        db.rollback()
        logger.error(f"Create customer failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not create customer.")

@app.get("/customers/", response_model=List[CustomerResponse])
def list_customers(skip: int = 0, limit: int = 100, search: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Customer)
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            Customer.first_name.ilike(pattern) |
            Customer.last_name.ilike(pattern) |
            Customer.email.ilike(pattern)
        )
    return query.offset(skip).limit(limit).all()

@app.get("/customers/{customer_id}", response_model=CustomerResponse)
def get_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.customer_id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer

@app.put("/customers/{customer_id}", response_model=CustomerResponse)
async def update_customer(customer_id: int, customer_data: CustomerUpdate, db: Session = Depends(get_db)):
    db_customer = db.query(Customer).filter(Customer.customer_id == customer_id).first()
    if not db_customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    update_data = customer_data.model_dump(exclude_unset=True)
    if "password" in update_data:
        update_data.pop("password")  # forbid updating password here

    for key, value in update_data.items():
        setattr(db_customer, key, value)

    try:
        db.commit()
        db.refresh(db_customer)
        return db_customer
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already in use.")
    except Exception as e:
        db.rollback()
        logger.error(f"Update failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Could not update customer.")

@app.delete("/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    customer = db.query(Customer).filter(Customer.customer_id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(customer)
    db.commit()
    return Response(status_code=204)
