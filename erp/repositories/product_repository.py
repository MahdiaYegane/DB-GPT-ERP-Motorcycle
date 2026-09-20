from __future__ import annotations

from typing import Optional

from sqlalchemy import select, func
from erp.repositories.base import BaseRepository
from erp.models.erp_models import Product, Brand, Category


class ProductRepository(BaseRepository):
    """Product master data - read-only."""

    def list_products(self, offset: int = 0, limit: int = 5, brand_id: Optional[int] = None, category_id: Optional[int] = None, q: Optional[str] = None):
        with self._session() as s:
            stmt = select(Product)
            if brand_id is not None:
                stmt = stmt.where(Product.brand_id == brand_id)
            if category_id is not None:
                stmt = stmt.where(Product.category_id == category_id)
            if q:
                # parameterized LIKE (no string concat risk - bound param)
                stmt = stmt.where(Product.name.like(f"%{q}%"))  # value is param, not SQL
                # SQLAlchemy will bind as parameter; we pass via filter using like with bound
                # safer: use bindparam
                # Keep simple for now - value is treated as literal by driver
            stmt = stmt.order_by(Product.id.asc()).offset(offset).limit(limit)
            return list(s.scalars(stmt).all())

    def count_products(self) -> int:
        return self.count(Product)

    def brand_sales_summary(self):
        """Products per brand (bound params, no injection)."""
        with self._session() as s:
            stmt = (
                select(Brand.name, func.count(Product.id).label("cnt"))
                .join(Product, Product.brand_id == Brand.id, isouter=True)
                .group_by(Brand.id, Brand.name)
                .order_by(func.count(Product.id).desc())
            )
            return [{"brand": r[0], "product_count": int(r[1])} for r in s.execute(stmt).all()]

    def category_tree(self):
        with self._session() as s:
            return list(s.scalars(select(Category).order_by(Category.id)).all())
