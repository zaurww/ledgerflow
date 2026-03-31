"""
modules/chart_of_accounts/router.py — API эндпоинты плана счетов

Только маршрутизация. Вся логика в service.py.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.chart_of_accounts.schemas import (
    AccountCreate, AccountUpdate, AccountResponse,
    AccountTreeNode, AccountBalanceResponse,
)
from modules.chart_of_accounts.service import (
    ChartOfAccountsService, CoAServiceError,
)

router = APIRouter()


def _get_service(db: AsyncSession = Depends(get_db)) -> ChartOfAccountsService:
    return ChartOfAccountsService(db)


def _build_response(account) -> AccountResponse:
    """Собрать ответ из модели Account."""
    return AccountResponse(
        id=account.id,
        code=account.code,
        name_az=account.name_az,
        name_en=account.name_en,
        account_type=account.account_type,
        is_leaf=account.is_leaf,
        is_active=account.is_active,
        description=account.description,
        parent_id=account.parent_id,
        parent_code=None,  # заполняется отдельно если нужно
        book_id=account.book_id,
    )


# ── CRUD ────────────────────────────────────────────────────────


@router.post("/", response_model=AccountResponse, status_code=201)
async def create_account(
    data: AccountCreate,
    book_code: str = Query(default="GAAP", description="Код книги учёта"),
    service: ChartOfAccountsService = Depends(_get_service),
):
    """Создать новый счёт в плане счетов."""
    try:
        account = await service.create_account(data, book_code=book_code)
        return _build_response(account)
    except CoAServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=list[AccountResponse])
async def list_accounts(
    book_code: str = Query(default="GAAP"),
    account_type: str | None = Query(default=None, description="Фильтр по типу"),
    is_active: bool | None = Query(default=None),
    is_leaf: bool | None = Query(default=None),
    service: ChartOfAccountsService = Depends(_get_service),
):
    """Получить список счетов с фильтрацией."""
    try:
        accounts = await service.list_accounts(
            book_code=book_code,
            account_type=account_type,
            is_active=is_active,
            is_leaf=is_leaf,
        )
        return [_build_response(a) for a in accounts]
    except CoAServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/tree", response_model=list[AccountTreeNode])
async def get_account_tree(
    book_code: str = Query(default="GAAP"),
    service: ChartOfAccountsService = Depends(_get_service),
):
    """Получить план счетов в виде дерева."""
    try:
        return await service.get_account_tree(book_code=book_code)
    except CoAServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{code}", response_model=AccountResponse)
async def get_account(
    code: str,
    service: ChartOfAccountsService = Depends(_get_service),
):
    """Получить счёт по коду."""
    try:
        account = await service.get_account(code)
        return _build_response(account)
    except CoAServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{code}", response_model=AccountResponse)
async def update_account(
    code: str,
    data: AccountUpdate,
    service: ChartOfAccountsService = Depends(_get_service),
):
    """Обновить счёт (название, описание, статус, родителя)."""
    try:
        account = await service.update_account(code, data)
        return _build_response(account)
    except CoAServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{code}/balance", response_model=AccountBalanceResponse)
async def get_account_balance(
    code: str,
    service: ChartOfAccountsService = Depends(_get_service),
):
    """Рассчитать баланс по счёту."""
    try:
        return await service.get_account_balance(code)
    except CoAServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Массовая загрузка ───────────────────────────────────────────


@router.post("/bulk", response_model=list[AccountResponse], status_code=201)
async def bulk_create_accounts(
    accounts: list[AccountCreate],
    book_code: str = Query(default="GAAP"),
    service: ChartOfAccountsService = Depends(_get_service),
):
    """
    Массовая загрузка счетов.

    Порядок важен: родительские счета должны идти раньше дочерних.
    """
    try:
        accounts_data = [a.model_dump() for a in accounts]
        created = await service.load_from_list(accounts_data, book_code=book_code)
        return [_build_response(a) for a in created]
    except CoAServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
