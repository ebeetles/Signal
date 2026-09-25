"""Pydantic models. The public API models are the frontend contract:
renaming or removing a field here breaks tests/test_contract.py on purpose."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Accepted(BaseModel):
    status: Literal["accepted"]
    job: Literal["collect", "send"]
