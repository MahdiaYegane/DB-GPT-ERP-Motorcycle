from erp.repositories.sales_repository import SalesRepository
from datetime import date

class SalesService:
    def __init__(self):
        self.repo = SalesRepository()

    def recent_orders(self, limit: int = 5, status: str | None = None):
        return self.repo.list_orders(limit=limit, status=status)

    def monthly_revenue(self):
        return self.repo.monthly_revenue()

    def top_customers(self, limit: int = 10):
        return self.repo.top_customers(limit)

    def branch_performance(self):
        return self.repo.branch_performance()
