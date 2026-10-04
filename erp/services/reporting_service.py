from erp.repositories.sales_repository import SalesRepository
from erp.repositories.product_repository import ProductRepository

class ReportingService:
    def dashboard(self):
        sales = SalesRepository()
        prod = ProductRepository()
        return {
            "monthly_revenue": sales.monthly_revenue(),
            "top_customers": sales.top_customers(5),
            "branches": sales.branch_performance(),
            "brands": prod.brand_sales_summary(),
        }
