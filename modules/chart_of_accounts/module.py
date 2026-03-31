"""
modules/chart_of_accounts/module.py — Контракт модуля CoA

Каждый модуль обязан иметь этот файл с классом, реализующим:
- register() — имя, версия, события
- get_routes() — API роуты
"""
from dataclasses import dataclass

from fastapi import APIRouter


@dataclass
class ModuleInfo:
    name: str
    version: str
    description: str
    events_emitted: list[str]
    events_consumed: list[str]


class ChartOfAccountsModule:
    """Модуль управления планом счетов."""

    def register(self) -> ModuleInfo:
        return ModuleInfo(
            name="chart_of_accounts",
            version="0.1.0",
            description="Управление планом счетов (CoA)",
            events_emitted=[
                "coa.account_created",
                "coa.account_updated",
                "coa.account_deactivated",
            ],
            events_consumed=[],
        )

    def get_routes(self) -> list[APIRouter]:
        from modules.chart_of_accounts.router import router
        return [router]
