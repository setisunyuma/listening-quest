from alembic import context
from sqlalchemy import create_engine

from lq.config import get_settings
from lq.db import models  # noqa: F401
from lq.db.base import Base

target_metadata = Base.metadata


def run() -> None:
    url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
