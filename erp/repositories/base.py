"""
BaseRepository - reusable, parameterized query helpers.
No raw string concatenation; all queries use SQLAlchemy bound params.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Type, TypeVar

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from erp.database import get_session

ModelT = TypeVar("ModelT")


class BaseRepository:
    """
    Generic read-only repository base. Extend per aggregate.

    Design goals:
    - No hard-coded DB names (uses ORM model.__tablename__)
    - Parameterized queries only (no f-string SQL)
    - Small-sample inspection by default (LIMIT 5 style)
    - UTF-8 safe (DB is utf8mb4, pymysql handles unicode)
    """

    def __init__(self, session: Optional[Session] = None):
        self._external_session = session

    def _session(self):
        if self._external_session is not None:
            # caller manages lifecycle
            class _Ctx:
                def __enter__(inner_self):
                    return self._external_session

                def __exit__(inner_self, *args):
                    return False

            return _Ctx()
        return get_session()

    def count(self, model: Type[ModelT]) -> int:
        with self._session() as s:
            return s.scalar(select(func.count()).select_from(model)) or 0

    def list_paginated(
        self,
        model: Type[ModelT],
        offset: int = 0,
        limit: int = 5,
        order_by=None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ModelT]:
        with self._session() as s:
            stmt: Select = select(model)
            if filters:
                for k, v in filters.items():
                    if v is not None and hasattr(model, k):
                        stmt = stmt.where(getattr(model, k) == v)
            if order_by is not None:
                stmt = stmt.order_by(order_by)
            else:
                # default: PK asc
                pk = list(model.__table__.primary_key.columns)[0]
                stmt = stmt.order_by(pk.asc())
            stmt = stmt.offset(offset).limit(limit)
            return list(s.scalars(stmt).all())

    def get_by_id(self, model: Type[ModelT], id_: Any) -> Optional[ModelT]:
        with self._session() as s:
            return s.get(model, id_)

    def raw_select(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Low-level parameterized raw SELECT (read-only).
        `sql` must use :named bind params, e.g. "SELECT * FROM products WHERE id=:id"
        """
        if not sql.strip().lower().startswith("select"):
            raise ValueError("Only SELECT statements are allowed via raw_select()")
        with self._session() as s:
            result = s.execute(text(sql), params or {})
            cols = list(result.keys())
            return [dict(zip(cols, row)) for row in result.fetchall()]
