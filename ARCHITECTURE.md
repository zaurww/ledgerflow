# ARCHITECTURE.md — Архитектура LedgerFlow

## Базовая концепция: Event-Driven Modular Monolith

LedgerFlow — это **модульный монолит** с событийной архитектурой.
Одно приложение, но внутри чётко разделённые модули которые общаются через события.

### Почему не микросервисы
- Один разработчик — микросервисы создают операционную сложность
- Проще деплоить, проще отлаживать, проще понимать
- При необходимости модуль можно вынести в сервис позже

---

## Поток данных

```
Внешний мир (Excel, форма, API)
        ↓
    Validation (Pydantic)
        ↓
   Business Event (неизменяемый факт)
        ↓
   Event Store (PostgreSQL)
        ↓
  Posting Rules Engine
        ↓
  ┌─────────────────────┐
  │  GL Book: GAAP      │  ← финансовая отчётность
  │  GL Book: TAX       │  ← налоговый учёт (позже)
  │  GL Book: MGMT      │  ← управленческий (позже)
  └─────────────────────┘
        ↓
     Reports
```

---

## Слои приложения

### 1. Core (ядро)
Не знает ни о каких модулях. Предоставляет фундаментальные сервисы.

```
core/
├── ledger/
│   ├── models.py       # Account, JournalEntry, JournalLine
│   ├── service.py      # LedgerService — запись проводок
│   └── validator.py    # Проверка баланса Dr=Cr
│
├── events/
│   ├── models.py       # BusinessEvent — неизменяемый факт
│   ├── bus.py          # EventBus — emit/subscribe
│   └── store.py        # EventStore — запись в БД
│
├── books/
│   ├── models.py       # LedgerBook — GAAP/TAX/MGMT
│   └── service.py      # BookService
│
└── rules/
    ├── models.py       # PostingRule
    └── engine.py       # PostingRulesEngine — событие → проводки
```

### 2. Modules (модули)
Знают о `core`, не знают друг о друге.

```
modules/
├── chart_of_accounts/  # план счетов
├── counterparties/     # контрагенты
├── import_module/      # загрузка файлов
└── reports/            # отчёты
```

Каждый модуль — одинаковая структура:
```
module_name/
├── module.py     # контракт: register(), get_routes(), get_migrations()
├── models.py     # SQLAlchemy модели
├── schemas.py    # Pydantic схемы (входящие/исходящие данные)
├── service.py    # бизнес-логика
└── router.py     # FastAPI роуты
```

### 3. API
Только маршрутизация. Никакой бизнес-логики.

```python
# router.py — правильно
@router.post("/journal-entries")
async def create_entry(data: JournalEntryCreate, service: LedgerService = Depends()):
    return await service.create(data)  # логика в service, не здесь
```

---

## База данных

### Стратегия
- Одна база данных PostgreSQL
- Каждый модуль владеет своими таблицами (префикс: `coa_`, `cp_`, `gl_`, etc.)
- Миграции через Alembic, один файл на изменение
- UUID как первичные ключи везде (portable, без конфликтов)

### Ключевые таблицы Core

```sql
-- Неизменяемые бизнес-события
CREATE TABLE events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type        VARCHAR(100) NOT NULL,   -- 'service.invoice_created'
    payload     JSONB NOT NULL,          -- все данные события
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  UUID NOT NULL            -- пользователь
    -- нет updated_at — события не меняются
);

-- Книги учёта
CREATE TABLE gl_books (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(20) UNIQUE NOT NULL,  -- 'GAAP', 'TAX', 'MGMT'
    name        VARCHAR(200) NOT NULL,
    is_active   BOOLEAN DEFAULT true,
    currency    VARCHAR(3) NOT NULL DEFAULT 'AZN'
);

-- Счета плана счетов
CREATE TABLE gl_accounts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(20) UNIQUE NOT NULL,   -- '1110', '4100'
    name        VARCHAR(300) NOT NULL,
    type        VARCHAR(20) NOT NULL,           -- asset/liability/equity/revenue/expense
    parent_id   UUID REFERENCES gl_accounts(id),
    is_leaf     BOOLEAN DEFAULT true,           -- только листья принимают проводки
    currency    VARCHAR(3),                     -- NULL = мультивалютный
    book_id     UUID REFERENCES gl_books(id)
);

-- Журнал проводок (header)
CREATE TABLE gl_journal_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    number      SERIAL UNIQUE,              -- человекочитаемый номер
    date        DATE NOT NULL,
    description TEXT,
    event_id    UUID REFERENCES events(id), -- источник
    book_id     UUID REFERENCES gl_books(id),
    is_reversed BOOLEAN DEFAULT false,
    reversed_by UUID REFERENCES gl_journal_entries(id),
    created_at  TIMESTAMPTZ DEFAULT now(),
    created_by  UUID NOT NULL
);

-- Строки проводки (lines)
CREATE TABLE gl_journal_lines (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id    UUID NOT NULL REFERENCES gl_journal_entries(id),
    account_id  UUID NOT NULL REFERENCES gl_accounts(id),
    debit       NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit      NUMERIC(18,2) NOT NULL DEFAULT 0,
    currency    VARCHAR(3) NOT NULL,
    description TEXT,
    -- CHECK: либо debit>0 либо credit>0, не оба
    CONSTRAINT one_side_only CHECK (
        (debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)
    )
);

-- Правила проводок
CREATE TABLE gl_posting_rules (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  VARCHAR(100) NOT NULL,  -- 'service.invoice_created'
    book_id     UUID REFERENCES gl_books(id),
    debit_account_code  VARCHAR(20) NOT NULL,
    credit_account_code VARCHAR(20) NOT NULL,
    amount_formula      VARCHAR(200) NOT NULL,  -- 'payload.amount'
    is_active   BOOLEAN DEFAULT true,
    valid_from  DATE,
    valid_to    DATE
);
```

---

## Соглашения по событиям

### Именование событий
```
{модуль}.{сущность}_{действие}

Примеры:
service.invoice_created
service.invoice_paid
inventory.goods_received
inventory.goods_shipped
manufacturing.production_order_completed
```

### Структура события
```python
@dataclass
class BusinessEvent:
    type: str          # 'service.invoice_created'
    payload: dict      # все данные
    created_at: datetime
    created_by: UUID
    # id генерируется при сохранении
```

---

## Правила написания тестов

```
tests/
├── core/
│   ├── test_ledger.py       # тест двойной записи
│   ├── test_events.py       # тест event store
│   └── test_rules_engine.py # тест правил проводок
└── modules/
    ├── test_chart_of_accounts.py
    ├── test_counterparties.py
    ├── test_import.py
    └── test_reports.py
```

Каждый тест файл — структура:
```python
# 1. Тесты happy path (нормальный сценарий)
# 2. Тесты edge cases (граничные случаи)
# 3. Тесты ошибок (что должно упасть)
```

---

## Масштабирование (когда понадобится)

Текущая архитектура легко масштабируется:

| Проблема | Решение | Когда добавлять |
|----------|---------|-----------------|
| Медленные отчёты | Redis кэш | > 10 000 строк |
| Тяжёлый импорт | Celery очередь | > 1 000 строк в файле |
| Медленные запросы | PostgreSQL индексы | при первых жалобах |
| Много пользователей | Read replica | > 100 активных |
| Разные сервисы | Вынести модуль | когда реально нужно |
