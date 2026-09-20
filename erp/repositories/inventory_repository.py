from __future__ import annotations

from sqlalchemy import select, func
from erp.repositories.base import BaseRepository
from erp.models.erp_models import Inventory, Product, Warehouse, Branch

class InventoryRepository(BaseRepository):
    def low_stock(self, threshold: int = 20, limit: int = 20):
        with self._session() as s:
            stmt = select(Inventory).where(Inventory.quantity <= threshold).order_by(Inventory.quantity.asc()).limit(limit)
            return list(s.scalars(stmt).all())

    def warehouse_summary(self):
        with self._session() as s:
            stmt = (
                select(Warehouse.name, func.coalesce(func.sum(Inventory.quantity), 0).label("total_qty"))
                .join(Inventory, Inventory.warehouse_id == Warehouse.id, isouter=True)
                .group_by(Warehouse.id, Warehouse.name)
                .order_by(Warehouse.id)
            )
            return [{"warehouse": r[0], "total_quantity": int(r[1])} for r in s.execute(stmt).all()]

    def inventory_for_product(self, product_id: int):
        with self._session() as s:
            return list(s.scalars(select(Inventory).where(Inventory.product_id == product_id)).all())
