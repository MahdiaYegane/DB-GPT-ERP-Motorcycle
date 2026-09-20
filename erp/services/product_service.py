from __future__ import annotations

from erp.repositories.product_repository import ProductRepository

class ProductService:
    """Business logic thin wrapper over ProductRepository (keeps SQL out of UI)."""

    def __init__(self):
        self.repo = ProductRepository()

    def motorcycle_catalog(self, limit: int = 20):
        """Sample catalog with Persian branding preserved (utf8mb4)."""
        return self.repo.list_products(limit=limit)

    def brands_summary(self):
        return self.repo.brand_sales_summary()

    def categories(self):
        return self.repo.category_tree()
