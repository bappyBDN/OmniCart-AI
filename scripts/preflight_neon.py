"""Read-only Neon preflight: report current catalog/policy/customer/order state."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine

from backend.config import settings
from backend.database import normalize_url


def main():
    engine = create_engine(
        normalize_url(settings.database_url),
        connect_args={"connect_timeout": 8, "application_name": "omnichat-preflight"},
    )
    with engine.connect() as c:
        dbname = c.exec_driver_sql("select current_database()").scalar_one()
        print("database:", dbname)
        shop = c.exec_driver_sql("select count(*) from shops where shop_id='shop_01'").scalar_one()
        print("shop_01 exists:", shop)
        products = c.exec_driver_sql("select count(*) from products where shop_id='shop_01'").scalar_one()
        policies = c.exec_driver_sql("select count(*) from policies where shop_id='shop_01'").scalar_one()
        customers = c.exec_driver_sql("select count(*) from customers where shop_id='shop_01'").scalar_one()
        orders = c.exec_driver_sql("select count(*) from orders where shop_id='shop_01'").scalar_one()
        print("products:", products, "policies:", policies, "customers:", customers, "orders:", orders)
        price_nullable = c.exec_driver_sql(
            "select is_nullable from information_schema.columns where table_name='products' and column_name='price'"
        ).scalar_one()
        print("products.price is_nullable:", price_nullable)
        stock_default = c.exec_driver_sql(
            "select column_default from information_schema.columns where table_name='products' and column_name='stock_count'"
        ).scalar_one()
        print("products.stock_count default:", stock_default)
        stock_nullable = c.exec_driver_sql(
            "select is_nullable from information_schema.columns where table_name='products' and column_name='stock_count'"
        ).scalar_one()
        print("products.stock_count is_nullable:", stock_nullable)
        for t in ("catalog_entries", "policy_sources"):
            exists = c.exec_driver_sql(
                "select count(*) from information_schema.tables where table_name='" + t + "'"
            ).scalar_one()
            print(t, "exists:", exists)
        img = c.exec_driver_sql("select count(*) from products where shop_id='shop_01' and product_id like 'IMG_%%'").scalar_one()
        print("existing IMG_ products:", img)


if __name__ == "__main__":
    main()
