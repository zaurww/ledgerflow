"""
api/main.py — Точка входа FastAPI приложения

Здесь только регистрация роутеров и конфигурация.
Никакой бизнес-логики.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="LedgerFlow",
    description="Open-source бухгалтерская система",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Проверка что сервер работает."""
    return {"status": "ok", "version": "0.1.0"}


# Роутеры модулей
from modules.chart_of_accounts.router import router as coa_router

app.include_router(coa_router, prefix="/api/v1/accounts", tags=["Chart of Accounts"])
