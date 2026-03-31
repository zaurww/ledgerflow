"""
modules/chart_of_accounts/schemas.py — Pydantic схемы для CoA

Входящие данные (Create/Update) и исходящие (Response).
Валидация на уровне API — до бизнес-логики.
"""
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


# ── Входящие схемы ──────────────────────────────────────────────


class AccountCreate(BaseModel):
    """Создание нового счёта."""
    code: str = Field(
        ...,
        min_length=1,
        max_length=20,
        examples=["1100"],
        description="Уникальный код счёта (числовой или буквенный)",
    )
    name_az: str = Field(
        ...,
        min_length=1,
        max_length=300,
        examples=["Cari aktivlər"],
        description="Название на азербайджанском (основной язык)",
    )
    name_en: str | None = Field(
        default=None,
        max_length=300,
        examples=["Current Assets"],
        description="Название на английском",
    )
    account_type: str = Field(
        ...,
        examples=["asset"],
        description="Тип счёта: asset, liability, equity, revenue, expense",
    )
    parent_code: str | None = Field(
        default=None,
        examples=["1000"],
        description="Код родительского счёта (если есть)",
    )
    description: str | None = None
    is_leaf: bool = Field(
        default=True,
        description="True = листовой счёт (принимает проводки). False = группа.",
    )


class AccountUpdate(BaseModel):
    """
    Обновление счёта.

    Нельзя менять: code, account_type (если есть проводки).
    Можно менять: name_az, name_en, description, is_active, parent.
    """
    name_az: str | None = Field(default=None, min_length=1, max_length=300)
    name_en: str | None = Field(default=None, max_length=300)
    description: str | None = None
    is_active: bool | None = None
    parent_code: str | None = None


# ── Исходящие схемы ─────────────────────────────────────────────


class AccountResponse(BaseModel):
    """Ответ API — один счёт."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name_az: str
    name_en: str | None = None
    account_type: str
    is_leaf: bool
    is_active: bool
    description: str | None = None
    parent_id: UUID | None = None
    parent_code: str | None = None
    book_id: UUID


class AccountTreeNode(BaseModel):
    """Счёт с вложенными дочерними счетами (для дерева CoA)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name_az: str
    name_en: str | None = None
    account_type: str
    is_leaf: bool
    is_active: bool
    children: list["AccountTreeNode"] = []


class AccountBalanceResponse(BaseModel):
    """Баланс по счёту — дебетовый и кредитовый оборот + сальдо."""
    code: str
    name_az: str
    account_type: str
    total_debit: Decimal
    total_credit: Decimal
    balance: Decimal  # для активных = debit - credit, для пассивных = credit - debit
