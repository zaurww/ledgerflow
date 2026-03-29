"""
core/ledger/models.py — Модели General Ledger

Это сердце системы. Двойная запись. Здесь хранятся все проводки.

Правила которые НИКОГДА не нарушаются:
- Сумма дебетов = сумма кредитов в каждой проводке
- Проводки не удаляются, только сторнируются
- Деньги только Decimal, никогда float
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Numeric, String, Text, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class AccountType(str, Enum):
    """Тип счёта определяет нормальное сальдо (дебет или кредит)."""
    ASSET = "asset"           # Активы — нормальное сальдо ДЕБЕТ
    LIABILITY = "liability"   # Пассивы — нормальное сальдо КРЕДИТ
    EQUITY = "equity"         # Капитал — нормальное сальдо КРЕДИТ
    REVENUE = "revenue"       # Доходы — нормальное сальдо КРЕДИТ
    EXPENSE = "expense"       # Расходы — нормальное сальдо ДЕБЕТ


class LedgerBook(Base):
    """
    Книга учёта. Одна операция может создавать проводки в нескольких книгах.

    Фаза 1: только одна книга — GAAP (общий учёт).
    Фаза 4+: добавляем TAX (налоговый), MGMT (управленческий).
    """
    __tablename__ = "gl_books"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # 'GAAP', 'TAX'
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="AZN")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    accounts: Mapped[list["Account"]] = relationship(back_populates="book")
    journal_entries: Mapped[list["JournalEntry"]] = relationship(back_populates="book")


class Account(Base):
    """
    Счёт в плане счетов.

    Иерархия: 1 (Assets) → 1100 (Current Assets) → 1110 (Cash) → 1111 (Bank USD)
    Только листовые счета (is_leaf=True) принимают проводки.
    """
    __tablename__ = "gl_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(String(20), nullable=False)
    is_leaf: Mapped[bool] = mapped_column(Boolean, default=True)  # только листья принимают проводки
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text)

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_accounts.id"), nullable=True
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_books.id"), nullable=False
    )

    parent: Mapped["Account | None"] = relationship("Account", remote_side="Account.id")
    children: Mapped[list["Account"]] = relationship("Account", back_populates="parent",
                                                       foreign_keys=[parent_id])
    book: Mapped[LedgerBook] = relationship(back_populates="accounts")
    lines: Mapped[list["JournalLine"]] = relationship(back_populates="account")

    def __repr__(self) -> str:
        return f"Account({self.code} — {self.name})"


class JournalEntry(Base):
    """
    Заголовок проводки (журнальная запись).

    Один документ = одна проводка с несколькими строками.
    Проводка НИКОГДА не удаляется. Если ошибка — создаётся сторнирующая проводка.
    """
    __tablename__ = "gl_journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(100))  # номер документа-основания

    # Источник: какое событие создало эту проводку
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_books.id"), nullable=False
    )

    # Сторнирование — проводки не удаляются
    is_reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    reversed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_journal_entries.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)  # имя/email пользователя

    book: Mapped[LedgerBook] = relationship(back_populates="journal_entries")
    lines: Mapped[list["JournalLine"]] = relationship(back_populates="entry",
                                                        cascade="all, delete-orphan")
    reversed_by: Mapped["JournalEntry | None"] = relationship("JournalEntry", remote_side="JournalEntry.id")

    def __repr__(self) -> str:
        return f"JournalEntry({self.date} — {self.description})"


class JournalLine(Base):
    """
    Строка проводки (дебет или кредит).

    Каждая строка — либо дебет, либо кредит (не оба).
    Сумма всех дебетов = сумма всех кредитов в рамках одной JournalEntry.
    """
    __tablename__ = "gl_journal_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_journal_entries.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_accounts.id"), nullable=False
    )

    # Деньги — ТОЛЬКО Decimal. Float запрещён из-за ошибок округления.
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="AZN")
    description: Mapped[str | None] = mapped_column(Text)

    # База данных тоже проверяет: только одна сторона за раз
    __table_args__ = (
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="ck_one_side_only"
        ),
    )

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship(back_populates="lines")

    def __repr__(self) -> str:
        side = f"Dr {self.debit}" if self.debit > 0 else f"Cr {self.credit}"
        return f"JournalLine({self.account_id} — {side} {self.currency})"
