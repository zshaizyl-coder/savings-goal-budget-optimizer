from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by ORM models and Alembic."""
