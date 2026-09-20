from sqlalchemy import select
from erp.repositories.base import BaseRepository
from erp.models.erp_models import Branch, City, Warehouse, Employee

class BranchRepository(BaseRepository):
    def list_branches(self, offset: int = 0, limit: int = 20):
        with self._session() as s:
            return list(s.scalars(select(Branch).order_by(Branch.id).offset(offset).limit(limit)).all())

    def branch_detail(self, branch_id: int):
        with self._session() as s:
            b = s.get(Branch, branch_id)
            if not b:
                return None
            city = s.get(City, b.city_id) if b.city_id else None
            warehouses = list(s.scalars(select(Warehouse).where(Warehouse.branch_id == branch_id)).all())
            employees = list(s.scalars(select(Employee).where(Employee.branch_id == branch_id)).all())
            return {"branch": b, "city": city, "warehouses": warehouses, "employees": employees}
