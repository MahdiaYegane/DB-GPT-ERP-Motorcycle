from __future__ import annotations

from erp.repositories.base import BaseRepository
from erp.models.erp_models import Customer, City, CustomerGroup
from sqlalchemy import select

class CustomerRepository(BaseRepository):
    def list_customers(self, offset: int = 0, limit: int = 5, city_id: int | None = None, group_id: int | None = None):
        with self._session() as s:
            stmt = select(Customer)
            if city_id is not None:
                stmt = stmt.where(Customer.city_id == city_id)
            if group_id is not None:
                stmt = stmt.where(Customer.customer_group_id == group_id)
            stmt = stmt.order_by(Customer.id.asc()).offset(offset).limit(limit)
            return list(s.scalars(stmt).all())

    def cities(self):
        with self._session() as s:
            return list(s.scalars(select(City).order_by(City.id)).all())

    def groups(self):
        with self._session() as s:
            return list(s.scalars(select(CustomerGroup).order_by(CustomerGroup.id)).all())
