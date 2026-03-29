"""
core/ledger/service.py — Сервис General Ledger

Вся логика двойной записи здесь. Роутеры только вызывают эти методы.

Главная гарантия: если метод вернул результат без ошибки —
в базе записана корректная проводка с Dr=Cr.
"""
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.ledger.models import Account, JournalEntry, JournalLine, LedgerBook


class LedgerValidationError(Exception):
    """Ошибка валидации проводки. Бизнес-правило нарушено."""
    pass


class LedgerService:
    """
    Сервис для работы с General Ledger.

    Использование:
        service = LedgerService(db)
        entry = await service.post_entry(
            date=date.today(),
            description="Оплата услуг",
            lines=[
                {"account_code": "1110", "debit": Decimal("1000")},
                {"account_code": "4100", "credit": Decimal("1000")},
            ],
            book_code="GAAP",
            created_by="admin",
        )
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def post_entry(
        self,
        date: date,
        lines: list[dict],
        book_code: str = "GAAP",
        description: str | None = None,
        reference: str | None = None,
        event_id: UUID | None = None,
        created_by: str = "system",
    ) -> JournalEntry:
        """
        Записать проводку в General Ledger.

        Args:
            date: дата проводки
            lines: список строк [{"account_code": "1110", "debit": Decimal("100")}]
            book_code: код книги учёта (по умолчанию GAAP)
            description: описание операции
            reference: ссылка на документ-основание
            event_id: ID бизнес-события которое создало проводку
            created_by: кто создал

        Returns:
            Созданная JournalEntry

        Raises:
            LedgerValidationError: если Dr ≠ Cr или счёт не найден
        """
        # 1. Получить книгу учёта
        book = await self._get_book(book_code)

        # 2. Получить счета и собрать строки
        journal_lines = await self._build_lines(lines)

        # 3. Проверить баланс Dr = Cr (главное правило бухгалтерии)
        self._validate_balance(journal_lines)

        # 4. Записать в базу
        entry = JournalEntry(
            date=date,
            description=description,
            reference=reference,
            event_id=event_id,
            book_id=book.id,
            created_by=created_by,
        )
        self.db.add(entry)
        await self.db.flush()  # получаем ID без коммита

        for line_data, account in journal_lines:
            line = JournalLine(
                entry_id=entry.id,
                account_id=account.id,
                debit=line_data.get("debit", Decimal("0")),
                credit=line_data.get("credit", Decimal("0")),
                currency=line_data.get("currency", "AZN"),
                description=line_data.get("description"),
            )
            self.db.add(line)

        return entry

    async def reverse_entry(
        self,
        entry_id: UUID,
        reason: str,
        created_by: str = "system",
    ) -> JournalEntry:
        """
        Сторнировать проводку (создать обратную).

        Оригинальная проводка не удаляется и не изменяется.
        Создаётся новая проводка с дебетами и кредитами наоборот.
        """
        # Найти оригинальную проводку
        original = await self.db.get(JournalEntry, entry_id)
        if not original:
            raise LedgerValidationError(f"Проводка {entry_id} не найдена")
        if original.is_reversed:
            raise LedgerValidationError(f"Проводка {entry_id} уже сторнирована")

        # Загрузить строки
        lines_result = await self.db.execute(
            select(JournalLine).where(JournalLine.entry_id == entry_id)
        )
        original_lines = lines_result.scalars().all()

        # Создать сторнирующие строки (дебет ↔ кредит)
        reversal_lines = [
            {
                "account_id": line.account_id,
                "debit": line.credit,    # меняем местами
                "credit": line.debit,    # меняем местами
                "currency": line.currency,
                "description": f"Сторно: {line.description or ''}",
            }
            for line in original_lines
        ]

        # Создать сторнирующую проводку
        reversal = JournalEntry(
            date=original.date,
            description=f"СТОРНО: {original.description or ''} | Причина: {reason}",
            reference=original.reference,
            book_id=original.book_id,
            created_by=created_by,
        )
        self.db.add(reversal)
        await self.db.flush()

        for line_data in reversal_lines:
            line = JournalLine(
                entry_id=reversal.id,
                account_id=line_data["account_id"],
                debit=line_data["debit"],
                credit=line_data["credit"],
                currency=line_data["currency"],
                description=line_data["description"],
            )
            self.db.add(line)

        # Пометить оригинал как сторнированный
        original.is_reversed = True
        original.reversed_by_id = reversal.id

        return reversal

    # ── Приватные методы ──────────────────────────────────────────

    async def _get_book(self, book_code: str) -> LedgerBook:
        result = await self.db.execute(
            select(LedgerBook).where(LedgerBook.code == book_code, LedgerBook.is_active == True)
        )
        book = result.scalar_one_or_none()
        if not book:
            raise LedgerValidationError(f"Книга учёта '{book_code}' не найдена или не активна")
        return book

    async def _build_lines(self, lines: list[dict]) -> list[tuple[dict, Account]]:
        """Найти счета для каждой строки проводки."""
        result = []
        for line_data in lines:
            account_code = line_data.get("account_code")
            account_id = line_data.get("account_id")

            if account_code:
                account = await self._get_account_by_code(account_code)
            elif account_id:
                account = await self.db.get(Account, account_id)
                if not account:
                    raise LedgerValidationError(f"Счёт ID {account_id} не найден")
            else:
                raise LedgerValidationError("Строка должна содержать account_code или account_id")

            if not account.is_leaf:
                raise LedgerValidationError(
                    f"Счёт {account.code} является группой. "
                    f"Проводки только на листовые счета."
                )

            result.append((line_data, account))
        return result

    async def _get_account_by_code(self, code: str) -> Account:
        result = await self.db.execute(
            select(Account).where(Account.code == code, Account.is_active == True)
        )
        account = result.scalar_one_or_none()
        if not account:
            raise LedgerValidationError(f"Счёт '{code}' не найден или не активен")
        return account

    @staticmethod
    def _validate_balance(lines: list[tuple[dict, Account]]) -> None:
        """
        Проверить что сумма дебетов = сумма кредитов.
        Это фундаментальное правило двойной записи.
        """
        total_debit = sum(
            line_data.get("debit", Decimal("0"))
            for line_data, _ in lines
        )
        total_credit = sum(
            line_data.get("credit", Decimal("0"))
            for line_data, _ in lines
        )

        if total_debit != total_credit:
            raise LedgerValidationError(
                f"Дебет ({total_debit}) ≠ Кредит ({total_credit}). "
                f"Разница: {abs(total_debit - total_credit)}"
            )

        if total_debit == Decimal("0"):
            raise LedgerValidationError("Проводка не может быть на нулевую сумму")
