"""
modules/chart_of_accounts/service.py — Бизнес-логика плана счетов

Все операции над счетами проходят через этот сервис.
Роутер только вызывает методы, не содержит логику.

Правила:
- Счёт нельзя удалить, только деактивировать
- Тип счёта нельзя менять если есть проводки
- Проводки только на листовые счета (is_leaf=True)
- Код счёта уникален в рамках книги
"""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.ledger.models import (
    Account, AccountType, JournalLine, LedgerBook,
)
from modules.chart_of_accounts.schemas import (
    AccountCreate, AccountUpdate, AccountResponse, AccountTreeNode,
    AccountBalanceResponse,
)


class CoAServiceError(Exception):
    """Ошибка бизнес-логики плана счетов."""
    pass


class ChartOfAccountsService:
    """
    Сервис управления планом счетов.

    Использование:
        service = ChartOfAccountsService(db)
        account = await service.create_account(data, book_code="GAAP")
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Создание ────────────────────────────────────────────────

    async def create_account(
        self,
        data: AccountCreate,
        book_code: str = "GAAP",
    ) -> Account:
        """
        Создать новый счёт в плане счетов.

        Проверки:
        - Код уникален
        - Тип счёта валиден
        - Родительский счёт существует (если указан)
        - Родитель должен быть группой (is_leaf=False)
        """
        # Проверить что тип счёта валиден
        try:
            account_type = AccountType(data.account_type)
        except ValueError:
            valid = ", ".join(t.value for t in AccountType)
            raise CoAServiceError(
                f"Неизвестный тип счёта: '{data.account_type}'. Допустимые: {valid}"
            )

        # Получить книгу учёта
        book = await self._get_book(book_code)

        # Проверить уникальность кода
        existing = await self._get_account_by_code(data.code)
        if existing:
            raise CoAServiceError(f"Счёт с кодом '{data.code}' уже существует")

        # Найти родителя если указан
        parent_id = None
        if data.parent_code:
            parent = await self._get_account_by_code(data.parent_code)
            if not parent:
                raise CoAServiceError(
                    f"Родительский счёт '{data.parent_code}' не найден"
                )
            if parent.is_leaf:
                # Родитель должен быть группой — переключаем его
                # (только если у него нет проводок)
                has_lines = await self._has_journal_lines(parent.id)
                if has_lines:
                    raise CoAServiceError(
                        f"Счёт '{data.parent_code}' имеет проводки и не может "
                        f"стать группой. Создайте промежуточный групповой счёт."
                    )
                parent.is_leaf = False
            parent_id = parent.id

        account = Account(
            code=data.code,
            name_az=data.name_az,
            name_en=data.name_en,
            account_type=account_type.value,
            is_leaf=data.is_leaf,
            description=data.description,
            parent_id=parent_id,
            book_id=book.id,
        )
        self.db.add(account)
        await self.db.flush()
        return account

    # ── Чтение ──────────────────────────────────────────────────

    async def get_account(self, code: str) -> Account:
        """Получить счёт по коду."""
        account = await self._get_account_by_code(code)
        if not account:
            raise CoAServiceError(f"Счёт '{code}' не найден")
        return account

    async def get_account_by_id(self, account_id: UUID) -> Account:
        """Получить счёт по ID."""
        account = await self.db.get(Account, account_id)
        if not account:
            raise CoAServiceError(f"Счёт с ID '{account_id}' не найден")
        return account

    async def list_accounts(
        self,
        book_code: str = "GAAP",
        account_type: str | None = None,
        is_active: bool | None = None,
        is_leaf: bool | None = None,
    ) -> list[Account]:
        """
        Список счетов с фильтрацией.

        Фильтры можно комбинировать:
        - account_type: только определённый тип (asset, expense, ...)
        - is_active: только активные или деактивированные
        - is_leaf: только листовые или только группы
        """
        book = await self._get_book(book_code)

        query = (
            select(Account)
            .where(Account.book_id == book.id)
            .order_by(Account.code)
        )

        if account_type:
            query = query.where(Account.account_type == account_type)
        if is_active is not None:
            query = query.where(Account.is_active == is_active)
        if is_leaf is not None:
            query = query.where(Account.is_leaf == is_leaf)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_account_tree(self, book_code: str = "GAAP") -> list[AccountTreeNode]:
        """
        Получить план счетов как дерево.

        Возвращает корневые счета (без parent) с вложенными children.
        Удобно для отображения в интерфейсе.
        """
        accounts = await self.list_accounts(book_code=book_code)

        # Собираем дерево из плоского списка
        by_id: dict[UUID, AccountTreeNode] = {}
        roots: list[AccountTreeNode] = []

        for acc in accounts:
            node = AccountTreeNode(
                id=acc.id,
                code=acc.code,
                name_az=acc.name_az,
                name_en=acc.name_en,
                account_type=acc.account_type,
                is_leaf=acc.is_leaf,
                is_active=acc.is_active,
                children=[],
            )
            by_id[acc.id] = node

        for acc in accounts:
            node = by_id[acc.id]
            if acc.parent_id and acc.parent_id in by_id:
                by_id[acc.parent_id].children.append(node)
            else:
                roots.append(node)

        return roots

    # ── Обновление ──────────────────────────────────────────────

    async def update_account(self, code: str, data: AccountUpdate) -> Account:
        """
        Обновить счёт.

        Можно менять: name_az, name_en, description, is_active, parent.
        Нельзя менять: code, account_type (через этот метод).
        """
        account = await self._get_account_by_code(code)
        if not account:
            raise CoAServiceError(f"Счёт '{code}' не найден")

        if data.name_az is not None:
            account.name_az = data.name_az
        if data.name_en is not None:
            account.name_en = data.name_en
        if data.description is not None:
            account.description = data.description

        # Деактивация счёта
        if data.is_active is not None:
            if not data.is_active:
                # Нельзя деактивировать счёт с активными дочерними
                active_children = await self._get_active_children(account.id)
                if active_children:
                    codes = ", ".join(c.code for c in active_children)
                    raise CoAServiceError(
                        f"Нельзя деактивировать: есть активные дочерние счета ({codes})"
                    )
            account.is_active = data.is_active

        # Смена родителя
        if data.parent_code is not None:
            if data.parent_code == "":
                # Убрать родителя (сделать корневым)
                account.parent_id = None
            else:
                parent = await self._get_account_by_code(data.parent_code)
                if not parent:
                    raise CoAServiceError(
                        f"Родительский счёт '{data.parent_code}' не найден"
                    )
                # Нельзя сделать счёт родителем самого себя
                if parent.id == account.id:
                    raise CoAServiceError("Счёт не может быть родителем самого себя")
                account.parent_id = parent.id

        await self.db.flush()
        return account

    # ── Баланс ──────────────────────────────────────────────────

    async def get_account_balance(self, code: str) -> AccountBalanceResponse:
        """
        Рассчитать текущий баланс по счёту.

        Для активных/расходных счетов (нормальное сальдо — дебет):
            balance = total_debit - total_credit
        Для пассивных/доходных/капитальных (нормальное сальдо — кредит):
            balance = total_credit - total_debit
        """
        account = await self._get_account_by_code(code)
        if not account:
            raise CoAServiceError(f"Счёт '{code}' не найден")

        # Сумма дебетов и кредитов по всем проводкам на этот счёт
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(JournalLine.debit), Decimal("0")).label("total_debit"),
                func.coalesce(func.sum(JournalLine.credit), Decimal("0")).label("total_credit"),
            ).where(JournalLine.account_id == account.id)
        )
        row = result.one()
        total_debit = row.total_debit
        total_credit = row.total_credit

        # Нормальное сальдо зависит от типа счёта
        debit_normal = account.account_type in (
            AccountType.ASSET.value, AccountType.EXPENSE.value
        )
        if debit_normal:
            balance = total_debit - total_credit
        else:
            balance = total_credit - total_debit

        return AccountBalanceResponse(
            code=account.code,
            name_az=account.name_az,
            account_type=account.account_type,
            total_debit=total_debit,
            total_credit=total_credit,
            balance=balance,
        )

    # ── Загрузка из CSV ─────────────────────────────────────────

    async def load_from_list(
        self,
        accounts_data: list[dict],
        book_code: str = "GAAP",
    ) -> list[Account]:
        """
        Массовая загрузка счетов из списка словарей.

        Используется при первоначальной настройке или импорте.
        Порядок важен: родительские счета должны идти раньше дочерних.

        Формат каждого элемента:
        {
            "code": "1100",
            "name_az": "Cari aktivlər",
            "name_en": "Current Assets",
            "account_type": "asset",
            "parent_code": "1000",  # опционально
            "is_leaf": False,
        }
        """
        created = []
        for item in accounts_data:
            data = AccountCreate(**item)
            account = await self.create_account(data, book_code=book_code)
            created.append(account)
        return created

    # ── Приватные методы ────────────────────────────────────────

    async def _get_book(self, book_code: str) -> LedgerBook:
        result = await self.db.execute(
            select(LedgerBook).where(
                LedgerBook.code == book_code,
                LedgerBook.is_active == True,
            )
        )
        book = result.scalar_one_or_none()
        if not book:
            raise CoAServiceError(
                f"Книга учёта '{book_code}' не найдена или не активна"
            )
        return book

    async def _get_account_by_code(self, code: str) -> Account | None:
        result = await self.db.execute(
            select(Account).where(Account.code == code)
        )
        return result.scalar_one_or_none()

    async def _has_journal_lines(self, account_id: UUID) -> bool:
        """Проверить есть ли проводки на счёте."""
        result = await self.db.execute(
            select(func.count()).where(JournalLine.account_id == account_id)
        )
        return result.scalar() > 0

    async def _get_active_children(self, account_id: UUID) -> list[Account]:
        """Получить активные дочерние счета."""
        result = await self.db.execute(
            select(Account).where(
                Account.parent_id == account_id,
                Account.is_active == True,
            )
        )
        return list(result.scalars().all())
