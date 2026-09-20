from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select, func, text

from erp.repositories.base import BaseRepository
from erp.models.erp_models import SalesOrder, Customer, Branch, Employee


class SalesRepository(BaseRepository):
    """Sales orders - authoritative sales source (read-only)."""

    def list_orders(self, offset: int = 0, limit: int = 5, status: Optional[str] = None, branch_id: Optional[int] = None, from_date: Optional[date] = None, to_date: Optional[date] = None):
        with self._session() as s:
            stmt = select(SalesOrder)
            if status:
                stmt = stmt.where(SalesOrder.status == status)
            if branch_id is not None:
                stmt = stmt.where(SalesOrder.branch_id == branch_id)
            if from_date:
                stmt = stmt.where(SalesOrder.order_date >= from_date)
            if to_date:
                stmt = stmt.where(SalesOrder.order_date <= to_date)
            stmt = stmt.order_by(SalesOrder.order_date.desc(), SalesOrder.id.desc()).offset(offset).limit(limit)
            return list(s.scalars(stmt).all())

    def monthly_revenue(self):
        """Monthly revenue - verified column: sales_orders.order_date is DATE (Gregorian)."""
        with self._session() as s:
            stmt = text("""
                SELECT DATE_FORMAT(order_date, '%Y-%m') AS ym,
                       COUNT(*) AS order_cnt,
                       SUM(total_amount) AS revenue,
                       SUM(discount_amount) AS discount_sum
                FROM sales_orders
                WHERE order_date IS NOT NULL
                GROUP BY ym
                ORDER BY ym
            """)
            rows = s.execute(stmt).mappings().all()
            return [dict(r) for r in rows]

    def top_customers(self, limit: int = 10):
        with self._session() as s:
            stmt = text("""
                SELECT c.id, CONCAT(COALESCE(c.first_name,''),' ',COALESCE(c.last_name,'')) AS customer,
                       c.customer_code, COUNT(so.id) AS orders, SUM(so.total_amount) AS total_spent
                FROM customers c
                JOIN sales_orders so ON so.customer_id = c.id
                GROUP BY c.id, c.customer_code, c.first_name, c.last_name
                ORDER BY total_spent DESC
                LIMIT :limit
            """)
            return [dict(r) for r in s.execute(stmt, {"limit": limit}).mappings().all()]

    def branch_performance(self):
        with self._session() as s:
            stmt = text("""
                SELECT b.id, b.name AS branch, COUNT(so.id) AS orders, SUM(so.total_amount) AS revenue
                FROM branches b
                LEFT JOIN sales_orders so ON so.branch_id = b.id
                GROUP BY b.id, b.name
                ORDER BY revenue DESC
            """)
            return [dict(r) for r in s.execute(stmt).mappings().all()]

    def status_breakdown(self):
        with self._session() as s:
            stmt = select(SalesOrder.status, func.count().label("cnt"), func.sum(SalesOrder.total_amount).label("sum_amt")).group_by(SalesOrder.status)
            return [{"status": r[0], "count": int(r[1]), "total_amount": float(r[2] or 0)} for r in s.execute(stmt).all()]
