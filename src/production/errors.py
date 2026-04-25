"""Errors raised by production workflow services."""

from __future__ import annotations


class ProductionError(Exception):
    """Base error for production workflow failures."""


class ProductionSessionNotFoundError(ProductionError):
    """Raised when a production session cannot be found or is not owned."""


class ProductionPersistenceError(ProductionError):
    """Raised when production state cannot be persisted or loaded."""

