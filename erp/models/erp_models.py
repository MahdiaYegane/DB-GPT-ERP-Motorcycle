"""
ERP SQLAlchemy declarative models - read-only mirror of live erp_motorcycle schema.

Verified from live DESCRIBE/SHOW CREATE TABLE (MariaDB 10.4.32, 2026-09-19):
- No FK constraints in DB (information_schema KEY_COLUMN_USAGE returns 0 rows)
- All engines InnoDB, charset utf8mb4_unicode_ci
- No indexes beyond PRIMARY(id) on every table (and implicit unique PK)
- No FK - relationships are logical only, inferred from column names & data

Do NOT auto-create/alter tables. These models are for typed ORM access only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Date, ForeignKey, String, Text
from sqlalchemy import Integer as SAInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp.database import Base


# ---------------------------------------------------------------------------
# Lookup / Master tables
# ---------------------------------------------------------------------------

class City(Base):
    __tablename__ = "cities"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    branches: Mapped[list["Branch"]] = relationship(back_populates="city", viewonly=True)
    customers: Mapped[list["Customer"]] = relationship(back_populates="city", viewonly=True)
    suppliers: Mapped[list["Supplier"]] = relationship(back_populates="city", viewonly=True)


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(150))
    city_id: Mapped[Optional[int]] = mapped_column(SAInteger)

    city: Mapped[Optional[City]] = relationship(back_populates="branches", viewonly=True)


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(100))


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(150))
    parent_id: Mapped[Optional[int]] = mapped_column(SAInteger)


class CustomerGroup(Base):
    __tablename__ = "customer_groups"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(100))


class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(150))
    branch_id: Mapped[Optional[int]] = mapped_column(SAInteger)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(180))
    city_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    phone: Mapped[Optional[str]] = mapped_column(String(30))

    city: Mapped[Optional[City]] = relationship(back_populates="suppliers", viewonly=True)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    customer_code: Mapped[Optional[str]] = mapped_column(String(30))
    first_name: Mapped[Optional[str]] = mapped_column(String(80))
    last_name: Mapped[Optional[str]] = mapped_column(String(80))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    city_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    customer_group_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    created_at: Mapped[Optional[date]] = mapped_column(Date)

    city: Mapped[Optional[City]] = relationship(back_populates="customers", viewonly=True)


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    employee_code: Mapped[Optional[str]] = mapped_column(String(30))
    first_name: Mapped[Optional[str]] = mapped_column(String(80))
    last_name: Mapped[Optional[str]] = mapped_column(String(80))
    department_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    branch_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    hire_date: Mapped[Optional[date]] = mapped_column(Date)
    salary: Mapped[Optional[Decimal]] = mapped_column()


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    sku: Mapped[Optional[str]] = mapped_column(String(40))
    name: Mapped[Optional[str]] = mapped_column(String(220))
    category_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    brand_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    product_type: Mapped[Optional[str]] = mapped_column(String(50))
    unit_price: Mapped[Optional[Decimal]] = mapped_column()
    cost_price: Mapped[Optional[Decimal]] = mapped_column()
    warranty_months: Mapped[Optional[int]] = mapped_column(SAInteger)
    is_active: Mapped[Optional[int]] = mapped_column(SAInteger)  # tinyint(4) 0/1


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class Inventory(Base):
    __tablename__ = "inventory"
    id: Mapped[int] = mapped_column(SAInteger, primary_key=True)
    product_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    warehouse_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    quantity: Mapped[Optional[int]] = mapped_column(SAInteger)
    reserved_quantity: Mapped[Optional[int]] = mapped_column(SAInteger)


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------

class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_no: Mapped[Optional[str]] = mapped_column(String(40))
    customer_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    branch_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    employee_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    order_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[Optional[str]] = mapped_column(String(30))
    discount_amount: Mapped[Optional[Decimal]] = mapped_column()
    total_amount: Mapped[Optional[Decimal]] = mapped_column()


class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    product_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    quantity: Mapped[Optional[int]] = mapped_column(SAInteger)
    unit_price: Mapped[Optional[Decimal]] = mapped_column()
    discount_amount: Mapped[Optional[Decimal]] = mapped_column()
    line_total: Mapped[Optional[Decimal]] = mapped_column()


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_no: Mapped[Optional[str]] = mapped_column(String(40))
    order_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    invoice_date: Mapped[Optional[date]] = mapped_column(Date)
    tax_amount: Mapped[Optional[Decimal]] = mapped_column()
    total_amount: Mapped[Optional[Decimal]] = mapped_column()
    status: Mapped[Optional[str]] = mapped_column(String(30))


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    amount: Mapped[Optional[Decimal]] = mapped_column()
    method: Mapped[Optional[str]] = mapped_column(String(30))
    status: Mapped[Optional[str]] = mapped_column(String(30))


# ---------------------------------------------------------------------------
# Purchasing
# ---------------------------------------------------------------------------

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    po_no: Mapped[Optional[str]] = mapped_column(String(40))
    supplier_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    branch_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    order_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[Optional[str]] = mapped_column(String(30))
    total_amount: Mapped[Optional[Decimal]] = mapped_column()


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    purchase_order_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    product_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    quantity: Mapped[Optional[int]] = mapped_column(SAInteger)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column()
    line_total: Mapped[Optional[Decimal]] = mapped_column()


# ---------------------------------------------------------------------------
# Service / Warranty
# ---------------------------------------------------------------------------

class ServiceOrder(Base):
    __tablename__ = "service_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    service_no: Mapped[Optional[str]] = mapped_column(String(40))
    customer_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    employee_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    branch_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    service_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[Optional[str]] = mapped_column(String(30))
    total_amount: Mapped[Optional[Decimal]] = mapped_column()


class Warranty(Base):
    __tablename__ = "warranties"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    customer_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    serial_no: Mapped[Optional[str]] = mapped_column(String(80))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)


class WarrantyClaim(Base):
    __tablename__ = "warranty_claims"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    warranty_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    branch_id: Mapped[Optional[int]] = mapped_column(SAInteger)
    claim_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[Optional[str]] = mapped_column(String(30))
    description: Mapped[Optional[str]] = mapped_column(String(500))
