"""Serve the existing dashboard and BudgetAI together for a private browser trial."""

from datetime import date as CalendarDate
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from api import CHAT_EXAMPLE_PROFILE, app as budgetai_app
from main import BudgetOrganizerAPI


app = FastAPI(title="BahayBulsa browser preview", docs_url=None, redoc_url=None, openapi_url=None)
organizer = BudgetOrganizerAPI(persist=False)

EntryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EntryAmount = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class Transaction(BaseModel):
    category: EntryName
    amount: EntryAmount
    type: Literal["income", "expense"]
    date: CalendarDate


class Bill(BaseModel):
    name: EntryName
    amount: EntryAmount
    due_date: CalendarDate


class SortRequest(BaseModel):
    algorithm: Literal["merge", "insertion"]


class BenchmarkRequest(BaseModel):
    dataset_size: Literal[100, 1000, 5000]


class UndoRequest(BaseModel):
    pass


@app.get("/")
async def index():
    return RedirectResponse("/preview/")


@app.get("/api/dashboard")
async def dashboard():
    return organizer.get_dashboard_data()


@app.post("/api/dashboard/transactions")
async def add_transaction(transaction: Transaction):
    return organizer.add_transaction(
        transaction.category, transaction.amount, transaction.type, transaction.date.isoformat()
    )


@app.delete("/api/dashboard/transactions/{transaction_id}")
async def delete_transaction(transaction_id: int):
    return organizer.delete_transaction(transaction_id)


@app.post("/api/dashboard/undo")
async def undo(_request: UndoRequest):
    return organizer.undo_last_action()


@app.post("/api/dashboard/bills")
async def add_bill(bill: Bill):
    return organizer.add_upcoming_bill(bill.name, bill.amount, bill.due_date.isoformat())


@app.post("/api/dashboard/sort")
async def sort_transactions(request: SortRequest):
    return organizer.sort_transactions(request.algorithm)


@app.post("/api/dashboard/benchmark")
def benchmark(request: BenchmarkRequest):
    return organizer.benchmark_runtime(request.dataset_size)


@app.get("/api/demo-profile")
async def demo_profile():
    return CHAT_EXAMPLE_PROFILE


app.mount("/api", budgetai_app)
app.mount("/preview", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True))
