"""
tests/core/test_ledger.py — Тесты двойной записи

Это самые важные тесты в системе.
Если двойная запись работает неправильно — всё остальное бессмысленно.
"""
from decimal import Decimal

import pytest

from core.ledger.service import LedgerService, LedgerValidationError


class TestDoubleEntryValidation:
    """Проверяем что система не принимает несбалансированные проводки."""

    def test_balanced_entry_passes(self):
        """Корректная проводка Dr=Cr должна проходить валидацию."""
        lines = [
            {"account_code": "1110", "debit": Decimal("1000.00")},
            {"account_code": "4100", "credit": Decimal("1000.00")},
        ]
        # Валидация не должна бросить исключение
        LedgerService._validate_balance([(line, None) for line in lines])

    def test_unbalanced_entry_raises_error(self):
        """Несбалансированная проводка должна быть отклонена."""
        lines = [
            {"account_code": "1110", "debit": Decimal("1000.00")},
            {"account_code": "4100", "credit": Decimal("999.00")},  # ошибка!
        ]
        with pytest.raises(LedgerValidationError, match="Дебет.*Кредит"):
            LedgerService._validate_balance([(line, None) for line in lines])

    def test_zero_amount_raises_error(self):
        """Нулевая проводка не имеет смысла."""
        lines = [
            {"account_code": "1110", "debit": Decimal("0")},
            {"account_code": "4100", "credit": Decimal("0")},
        ]
        with pytest.raises(LedgerValidationError, match="нулевую сумму"):
            LedgerService._validate_balance([(line, None) for line in lines])

    def test_multi_line_balanced_entry(self):
        """Проводка с несколькими строками — всё равно Dr=Cr."""
        lines = [
            {"account_code": "1110", "debit": Decimal("300.00")},
            {"account_code": "1120", "debit": Decimal("700.00")},
            {"account_code": "4100", "credit": Decimal("1000.00")},
        ]
        # Не должно бросить исключение: 300+700 = 1000
        LedgerService._validate_balance([(line, None) for line in lines])

    def test_decimal_precision(self):
        """Проверяем что копейки считаются правильно (не float!)."""
        lines = [
            {"account_code": "1110", "debit": Decimal("0.10")},
            {"account_code": "4100", "credit": Decimal("0.10")},
        ]
        # С float это могло бы дать ошибку из-за округления
        LedgerService._validate_balance([(line, None) for line in lines])
