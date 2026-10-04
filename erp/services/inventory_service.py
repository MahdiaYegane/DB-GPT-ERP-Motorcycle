from erp.repositories.inventory_repository import InventoryRepository

class InventoryService:
    def __init__(self):
        self.repo = InventoryRepository()

    def low_stock(self, threshold: int = 20):
        return self.repo.low_stock(threshold)

    def by_warehouse(self):
        return self.repo.warehouse_summary()
