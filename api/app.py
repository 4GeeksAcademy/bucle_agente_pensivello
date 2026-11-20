"""FastAPI REST API for Carla's coffee-supply inventory."""

import csv
import math
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import List

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
INVENTORY_FILE = Path(os.getenv("INVENTORY_CSV", PROJECT_ROOT / "products.csv"))
CSV_FIELDS = ["id", "name", "quantity", "unit"]
# Re-entrant because endpoints hold the lock while the CSV helpers may also
# need it when initializing the file on first startup.
CSV_LOCK = threading.RLock()

DEFAULT_PRODUCTS = [
    {"id": "1", "name": "Leche de avena", "quantity": 24, "unit": "unidades"},
    {"id": "2", "name": "Café arábica", "quantity": 18, "unit": "bolsas"},
    {"id": "3", "name": "Granos de café robusta", "quantity": 12, "unit": "kg"},
    {"id": "4", "name": "Vasos de cartón", "quantity": 150, "unit": "unidades"},
    {"id": "5", "name": "Sirope de vainilla", "quantity": 8, "unit": "litros"},
    {"id": "6", "name": "Filtros de café", "quantity": 40, "unit": "unidades"},
]


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=50)

    @field_validator("name", "unit")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El campo no puede estar vacío")
        return value


class StockAdjustment(BaseModel):
    delta: float = Field(allow_inf_nan=False)


class Product(BaseModel):
    id: str
    name: str
    quantity: float
    unit: str


def _ensure_inventory_file() -> None:
    """Create inventory CSV with starter products on first run."""
    INVENTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if INVENTORY_FILE.exists():
        return
    with CSV_LOCK:
        if INVENTORY_FILE.exists():
            return
        with INVENTORY_FILE.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(DEFAULT_PRODUCTS)


def _read_products() -> List[dict]:
    _ensure_inventory_file()
    try:
        with INVENTORY_FILE.open("r", newline="", encoding="utf-8") as csv_file:
            rows = list(csv.DictReader(csv_file))
        products = []
        for row in rows:
            product = {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "quantity": float(row["quantity"]),
                "unit": str(row["unit"]),
            }
            if not math.isfinite(product["quantity"]):
                raise ValueError("cantidad no finita")
            products.append(product)
        return products
    except (OSError, KeyError, TypeError, ValueError, csv.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo leer el inventario CSV: {exc}",
        ) from exc


def _write_products(products: List[dict]) -> None:
    """Atomically replace the CSV to avoid leaving a partial inventory file."""
    _ensure_inventory_file()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", newline="", encoding="utf-8", dir=INVENTORY_FILE.parent, delete=False
        ) as csv_file:
            temp_path = Path(csv_file.name)
            writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(products)
        os.replace(temp_path, INVENTORY_FILE)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo guardar el inventario: {exc}",
        ) from exc


app = FastAPI(title="Carla - Gestión de inventario", version="1.0.0")


@app.get("/inventory", response_model=List[Product])
def get_inventory():
    """Return all products in the inventory."""
    with CSV_LOCK:
        return _read_products()


@app.post("/inventory", response_model=Product, status_code=status.HTTP_201_CREATED)
def add_product(product_data: ProductCreate):
    """Add a product and generate a unique identifier."""
    with CSV_LOCK:
        products = _read_products()
        product = {
            "id": uuid.uuid4().hex,
            "name": product_data.name,
            "quantity": product_data.quantity,
            "unit": product_data.unit,
        }
        products.append(product)
        _write_products(products)
        return product


@app.patch("/inventory/{product_id}", response_model=Product)
def update_stock(product_id: str, adjustment: StockAdjustment):
    """Adjust stock by delta; reject changes that would make stock negative."""
    with CSV_LOCK:
        products = _read_products()
        product = next((item for item in products if item["id"] == product_id), None)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No existe un producto con id '{product_id}'.",
            )
        new_quantity = product["quantity"] + adjustment.delta
        if new_quantity < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Stock insuficiente para '{product['name']}'. "
                    f"Disponible: {product['quantity']} {product['unit']}."
                ),
            )
        product["quantity"] = new_quantity
        _write_products(products)
        return product


@app.get("/inventory/alerts", response_model=List[Product])
def get_inventory_alerts(
    threshold: float = Query(default=10, ge=0, allow_inf_nan=False),
):
    """Return products whose quantity is below the requested threshold."""
    with CSV_LOCK:
        return [product for product in _read_products() if product["quantity"] < threshold]
