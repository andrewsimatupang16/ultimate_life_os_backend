from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import BillReminder, Budget, FinanceEvent, Transaction, TransactionTypeEnum, User, Wallet
from app.schemas import (
    BillReminderCreate,
    BillReminderResponse,
    BillReminderUpdate,
    BudgetCreate,
    BudgetResponse,
    BudgetUpdate,
    FinanceEventResponse,
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
    WalletCreate,
    WalletResponse,
    WalletUpdate,
)
from app.utils.money import to_money
from app.utils.time import local_period_bounds_utc, to_utc_naive, utc_now

router = APIRouter(prefix="/finance", tags=["Finance"])


def default_budget_window(user: User, period: str) -> tuple[datetime, datetime]:
    start, exclusive_end = local_period_bounds_utc(user, period)
    return start, exclusive_end - timedelta(microseconds=1)


def normalize_budget_window(user: User, period: Optional[str], start_date: Optional[datetime], end_date: Optional[datetime]) -> tuple[str, datetime, datetime]:
    normalized_period = period or "monthly"
    start_date = to_utc_naive(start_date)
    end_date = to_utc_naive(end_date)
    if normalized_period not in {"daily", "weekly", "monthly", "custom"}:
        raise HTTPException(status_code=400, detail="Budget period must be daily, weekly, monthly, or custom")

    if normalized_period == "custom":
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="Custom budget requires start_date and end_date")
        if end_date <= start_date:
            raise HTTPException(status_code=400, detail="Budget end_date must be after start_date")
        return normalized_period, start_date, end_date

    if start_date and end_date and end_date <= start_date:
        raise HTTPException(status_code=400, detail="Budget end_date must be after start_date")

    default_start, default_end = default_budget_window(user, normalized_period)
    return normalized_period, start_date or default_start, end_date or default_end


def get_wallet_or_404(db: Session, wallet_id: UUID, user_id: UUID, *, lock: bool = False) -> Wallet:
    q = db.query(Wallet).filter(
        Wallet.id == wallet_id,
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
    )
    if lock:
        q = q.with_for_update()
    wallet = q.first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return wallet


def apply_transaction_to_wallet(wallet: Wallet, transaction: Transaction, reverse: bool = False) -> None:
    multiplier = -1 if reverse else 1
    if transaction.type == TransactionTypeEnum.income:
        wallet.balance = to_money(wallet.balance) + (to_money(transaction.amount) * multiplier)
    else:
        wallet.balance = to_money(wallet.balance) - (to_money(transaction.amount) * multiplier)
    wallet.balance = to_money(wallet.balance)
    wallet.updated_at = utc_now()


def transaction_balance_delta(transaction: Transaction, *, reverse: bool = False) -> Decimal:
    amount = to_money(transaction.amount)
    delta = amount if transaction.type == TransactionTypeEnum.income else -amount
    return -delta if reverse else delta


def record_finance_event(
    db: Session,
    *,
    user_id: UUID,
    event_type: str,
    wallet: Wallet | None = None,
    transaction: Transaction | None = None,
    bill: BillReminder | None = None,
    amount_delta: Decimal | float = 0.0,
    description: str | None = None,
) -> None:
    db.add(FinanceEvent(
        user_id=user_id,
        wallet_id=wallet.id if wallet else None,
        transaction_id=transaction.id if transaction else None,
        bill_id=bill.id if bill else None,
        event_type=event_type,
        amount_delta=to_money(amount_delta),
        balance_after=to_money(wallet.balance) if wallet else None,
        description=description,
    ))


def wallet_total_balance(db: Session, user_id: UUID) -> Decimal:
    total = db.query(func.coalesce(func.sum(Wallet.balance), 0.0)).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
    ).scalar()
    return to_money(total)


def wallet_count(db: Session, user_id: UUID) -> int:
    return db.query(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
    ).count()


def create_wallet_balance_adjustment(
    db: Session,
    *,
    user_id: UUID,
    wallet: Wallet,
    old_balance: Decimal,
    new_balance: Decimal,
) -> Transaction | None:
    old_balance = to_money(old_balance)
    new_balance = to_money(new_balance)
    delta = to_money(new_balance - old_balance)
    if delta == 0:
        return None

    adjustment_type = TransactionTypeEnum.income if delta > 0 else TransactionTypeEnum.expense
    adjustment_amount = to_money(abs(delta))
    transaction = Transaction(
        wallet_id=wallet.id,
        type=adjustment_type,
        amount=adjustment_amount,
        category="Wallet Adjustment",
        transaction_date=utc_now(),
        description=(
            f"Manual balance adjustment for {wallet.name}: "
            f"{old_balance} → {new_balance}"
        ),
        is_private=True,
    )
    apply_transaction_to_wallet(wallet, transaction)
    db.add(transaction)
    db.flush()
    record_finance_event(
        db,
        user_id=user_id,
        event_type="wallet_balance_adjusted",
        wallet=wallet,
        transaction=transaction,
        amount_delta=delta,
        description=f"Manual balance adjustment: {wallet.name}",
    )
    recalc_budget_spent(db, user_id, transaction.category)
    return transaction


def get_user_transaction_or_404(db: Session, transaction_id: UUID, user_id: UUID) -> Transaction:
    transaction = db.query(Transaction).join(Wallet).filter(
        Transaction.id == transaction_id,
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.deleted_at.is_(None),
    ).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


def get_active_bill_for_payment_transaction(db: Session, transaction_id: UUID, user_id: UUID) -> BillReminder | None:
    return db.query(BillReminder).filter(
        BillReminder.user_id == user_id,
        BillReminder.paid_transaction_id == transaction_id,
        BillReminder.deleted_at.is_(None),
    ).first()


def ensure_transaction_not_bill_payment(db: Session, transaction: Transaction, user_id: UUID) -> None:
    bill = get_active_bill_for_payment_transaction(db, transaction.id, user_id)
    if bill:
        raise HTTPException(
            status_code=409,
            detail="This transaction was generated by a paid bill. Unpay or edit the bill instead of modifying the transaction directly.",
        )


def count_active_paid_bills_using_wallet(db: Session, wallet_id: UUID, user_id: UUID) -> int:
    return db.query(BillReminder).join(
        Transaction, BillReminder.paid_transaction_id == Transaction.id
    ).filter(
        BillReminder.user_id == user_id,
        BillReminder.is_paid.is_(True),
        BillReminder.deleted_at.is_(None),
        Transaction.wallet_id == wallet_id,
        Transaction.deleted_at.is_(None),
    ).count()


def recalc_budget_spent(db: Session, user_id: UUID, category: str) -> None:
    budgets = db.query(Budget).filter(
        Budget.user_id == user_id,
        Budget.category == category,
        Budget.deleted_at.is_(None),
    ).all()
    if not budgets:
        return
    for budget in budgets:
        q = db.query(func.coalesce(func.sum(Transaction.amount), 0.0)).join(Wallet).filter(
            Wallet.user_id == user_id,
            Wallet.deleted_at.is_(None),
            Transaction.category == category,
            Transaction.type == TransactionTypeEnum.expense,
            Transaction.deleted_at.is_(None),
        )
        if budget.start_date is not None:
            q = q.filter(Transaction.transaction_date >= budget.start_date)
        if budget.end_date is not None:
            q = q.filter(Transaction.transaction_date <= budget.end_date)
        spent = q.scalar() or 0
        budget.current_spent = to_money(spent)


def sync_budget_period_windows(db: Session, user: User) -> None:
    budgets = db.query(Budget).filter(
        Budget.user_id == user.id,
        Budget.period.in_(("daily", "weekly", "monthly")),
        Budget.deleted_at.is_(None),
    ).all()
    changed_categories = set()
    for budget in budgets:
        start_date, end_date = default_budget_window(user, budget.period)
        if budget.start_date != start_date or budget.end_date != end_date:
            budget.start_date = start_date
            budget.end_date = end_date
            budget.updated_at = utc_now()
            changed_categories.add(budget.category)
    for category in changed_categories:
        recalc_budget_spent(db, user.id, category)


def get_bill_or_404(db: Session, bill_id: UUID, user_id: UUID) -> BillReminder:
    bill = db.query(BillReminder).filter(
        BillReminder.id == bill_id,
        BillReminder.user_id == user_id,
        BillReminder.deleted_at.is_(None),
    ).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill reminder not found")
    return bill


def reverse_bill_payment(db: Session, bill: BillReminder, user_id: UUID) -> None:
    if bill.paid_transaction_id is None:
        bill.is_paid = False
        bill.paid_at = None
        bill.updated_at = utc_now()
        return

    transaction = db.query(Transaction).join(Wallet).filter(
        Transaction.id == bill.paid_transaction_id,
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.deleted_at.is_(None),
    ).first()
    if transaction is None:
        bill.paid_transaction_id = None
        bill.is_paid = False
        bill.paid_at = None
        bill.updated_at = utc_now()
        return

    wallet = get_wallet_or_404(db, transaction.wallet_id, user_id, lock=True)
    apply_transaction_to_wallet(wallet, transaction, reverse=True)
    now = utc_now()
    transaction.deleted_at = now
    transaction.updated_at = now
    bill.paid_transaction_id = None
    bill.is_paid = False
    bill.paid_at = None
    bill.updated_at = now
    record_finance_event(
        db,
        user_id=user_id,
        event_type="bill_unpaid",
        wallet=wallet,
        transaction=transaction,
        bill=bill,
        amount_delta=transaction_balance_delta(transaction, reverse=True),
        description=f"Bill payment reversed: {bill.title}",
    )
    recalc_budget_spent(db, user_id, transaction.category)


@router.get("/wallets", response_model=List[WalletResponse])
def get_wallets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Wallet).filter(Wallet.user_id == current_user.id, Wallet.deleted_at.is_(None)).order_by(Wallet.created_at.desc()).all()


@router.get("/events", response_model=List[FinanceEventResponse])
def get_finance_events(
    limit: int = Query(50, ge=1, le=200),
    wallet_id: Optional[UUID] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(FinanceEvent).filter(
        FinanceEvent.user_id == current_user.id,
        FinanceEvent.deleted_at.is_(None),
    )
    if wallet_id:
        q = q.filter(FinanceEvent.wallet_id == wallet_id)
    return q.order_by(FinanceEvent.created_at.desc()).limit(limit).all()


@router.get("/wallets/{wallet_id}")
def get_wallet(wallet_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = db.query(Wallet).options(joinedload(Wallet.transactions)).filter(
        Wallet.id == wallet_id,
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
    ).first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found")
    transactions = [tx for tx in wallet.transactions if tx.deleted_at is None]
    return {**WalletResponse.model_validate(wallet).model_dump(mode="json"), "transactions": [TransactionResponse.model_validate(tx).model_dump(mode="json") for tx in transactions]}


@router.post("/wallets", response_model=WalletResponse, status_code=status.HTTP_201_CREATED)
def create_wallet(payload: WalletCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = Wallet(user_id=current_user.id, name=payload.name, wallet_type=payload.wallet_type, balance=to_money(payload.balance))
    db.add(wallet)
    db.flush()
    record_finance_event(
        db,
        user_id=current_user.id,
        event_type="wallet_created",
        wallet=wallet,
        amount_delta=to_money(wallet.balance),
        description=f"Wallet created: {wallet.name}",
    )
    db.commit()
    db.refresh(wallet)
    return wallet


@router.put("/wallets/{wallet_id}", response_model=WalletResponse)
def update_wallet(wallet_id: UUID, payload: WalletUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_wallet_or_404(db, wallet_id, current_user.id, lock=True)
    old_balance = to_money(wallet.balance)
    requested_balance = to_money(payload.balance) if payload.balance is not None else None

    if payload.name is not None:
        wallet.name = payload.name
    if payload.wallet_type is not None:
        wallet.wallet_type = payload.wallet_type
    if requested_balance is not None:
        create_wallet_balance_adjustment(
            db,
            user_id=current_user.id,
            wallet=wallet,
            old_balance=old_balance,
            new_balance=requested_balance,
        )
    wallet.updated_at = utc_now()
    db.commit()
    db.refresh(wallet)
    return wallet


@router.delete("/wallets/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_wallet(wallet_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_wallet_or_404(db, wallet_id, current_user.id, lock=True)
    linked_paid_bills = count_active_paid_bills_using_wallet(db, wallet.id, current_user.id)
    if linked_paid_bills:
        raise HTTPException(
            status_code=409,
            detail="Unpay paid bills linked to this wallet before deleting the wallet",
        )

    now = utc_now()
    affected_categories = {tx.category for tx in wallet.transactions if tx.deleted_at is None}
    wallet.deleted_at = now
    wallet.updated_at = now
    record_finance_event(
        db,
        user_id=current_user.id,
        event_type="wallet_deleted",
        wallet=wallet,
        amount_delta=0.0,
        description=f"Wallet deleted: {wallet.name}",
    )
    for tx in wallet.transactions:
        if tx.deleted_at is None:
            tx.deleted_at = now
            tx.updated_at = now
    db.flush()
    for category in affected_categories:
        recalc_budget_spent(db, current_user.id, category)
    db.commit()
    return None


@router.get("/transactions", response_model=List[TransactionResponse])
def get_transactions(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    category: Optional[str] = None,
    tx_type: Optional[TransactionTypeEnum] = None,
    wallet_id: Optional[UUID] = None,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    start_date = to_utc_naive(start_date)
    end_date = to_utc_naive(end_date)
    normalized_category = category.strip() if category else None
    normalized_search = search.strip() if search else None

    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    q = db.query(Transaction).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Transaction.deleted_at.is_(None),
        Wallet.deleted_at.is_(None),
    )
    if start_date:
        q = q.filter(Transaction.transaction_date >= start_date)
    if end_date:
        q = q.filter(Transaction.transaction_date <= end_date)
    if wallet_id:
        q = q.filter(Wallet.id == wallet_id)
    if normalized_category:
        q = q.filter(Transaction.category == normalized_category)
    if tx_type:
        q = q.filter(Transaction.type == tx_type)
    if normalized_search:
        like_search = f"%{normalized_search}%"
        q = q.filter(or_(
            Transaction.category.ilike(like_search),
            Transaction.description.ilike(like_search),
        ))

    return q.order_by(Transaction.transaction_date.desc(), Transaction.created_at.desc()).all()


@router.post("/transactions", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(payload: TransactionCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wallet = get_wallet_or_404(db, payload.wallet_id, current_user.id, lock=True)
    transaction = Transaction(
        wallet_id=wallet.id,
        type=payload.type,
        amount=to_money(payload.amount),
        category=payload.category,
        transaction_date=payload.transaction_date or utc_now(),
        description=payload.description,
        is_private=payload.is_private,
    )
    apply_transaction_to_wallet(wallet, transaction)
    db.add(transaction)
    db.flush()
    record_finance_event(
        db,
        user_id=current_user.id,
        event_type="transaction_created",
        wallet=wallet,
        transaction=transaction,
        amount_delta=transaction_balance_delta(transaction),
        description=transaction.description or transaction.category,
    )
    recalc_budget_spent(db, current_user.id, transaction.category)
    db.commit()
    db.refresh(transaction)
    return transaction


@router.put("/transactions/{transaction_id}", response_model=TransactionResponse)
def update_transaction(transaction_id: UUID, payload: TransactionUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transaction = get_user_transaction_or_404(db, transaction_id, current_user.id)
    ensure_transaction_not_bill_payment(db, transaction, current_user.id)
    old_category = transaction.category
    old_wallet = get_wallet_or_404(db, transaction.wallet_id, current_user.id, lock=True)
    old_delta = transaction_balance_delta(transaction)
    target_wallet = old_wallet

    if payload.wallet_id is not None:
        target_wallet = get_wallet_or_404(db, payload.wallet_id, current_user.id, lock=True)

    apply_transaction_to_wallet(old_wallet, transaction, reverse=True)

    if payload.wallet_id is not None:
        transaction.wallet_id = target_wallet.id
    if payload.type is not None:
        transaction.type = payload.type
    if payload.amount is not None:
        transaction.amount = to_money(payload.amount)
    if payload.category is not None:
        transaction.category = payload.category
    if payload.transaction_date is not None:
        transaction.transaction_date = payload.transaction_date
    if payload.description is not None:
        transaction.description = payload.description
    if payload.is_private is not None:
        transaction.is_private = payload.is_private

    transaction.updated_at = utc_now()
    apply_transaction_to_wallet(target_wallet, transaction)
    new_delta = transaction_balance_delta(transaction)

    db.flush()
    if old_wallet.id == target_wallet.id:
        record_finance_event(
            db,
            user_id=current_user.id,
            event_type="transaction_updated",
            wallet=target_wallet,
            transaction=transaction,
            amount_delta=new_delta - old_delta,
            description=transaction.description or transaction.category,
        )
    else:
        record_finance_event(
            db,
            user_id=current_user.id,
            event_type="transaction_moved_out",
            wallet=old_wallet,
            transaction=transaction,
            amount_delta=-old_delta,
            description=transaction.description or old_category,
        )
        record_finance_event(
            db,
            user_id=current_user.id,
            event_type="transaction_moved_in",
            wallet=target_wallet,
            transaction=transaction,
            amount_delta=new_delta,
            description=transaction.description or transaction.category,
        )
    recalc_budget_spent(db, current_user.id, old_category)
    recalc_budget_spent(db, current_user.id, transaction.category)
    db.commit()
    db.refresh(transaction)
    return transaction


@router.delete("/transactions/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(transaction_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    transaction = get_user_transaction_or_404(db, transaction_id, current_user.id)
    ensure_transaction_not_bill_payment(db, transaction, current_user.id)
    wallet = get_wallet_or_404(db, transaction.wallet_id, current_user.id, lock=True)
    old_delta = transaction_balance_delta(transaction)
    apply_transaction_to_wallet(wallet, transaction, reverse=True)
    now = utc_now()
    transaction.deleted_at = now
    transaction.updated_at = now
    record_finance_event(
        db,
        user_id=current_user.id,
        event_type="transaction_deleted",
        wallet=wallet,
        transaction=transaction,
        amount_delta=-old_delta,
        description=transaction.description or transaction.category,
    )
    recalc_budget_spent(db, current_user.id, transaction.category)
    db.commit()
    return None


@router.get("/transactions/summary")
def get_transaction_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total_income = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Transaction.type == TransactionTypeEnum.income,
        Transaction.deleted_at.is_(None),
        Wallet.deleted_at.is_(None),
    ).scalar())
    total_expense = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.deleted_at.is_(None),
        Wallet.deleted_at.is_(None),
    ).scalar())
    by_category = db.query(Transaction.category, func.sum(Transaction.amount).label("total")).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.deleted_at.is_(None),
        Wallet.deleted_at.is_(None),
    ).group_by(Transaction.category).all()
    return {
        "total_income": float(total_income),
        "total_expense": float(total_expense),
        "net": float(total_income - total_expense),
        "total_balance": float(wallet_total_balance(db, current_user.id)),
        "total_wallets": wallet_count(db, current_user.id),
        "by_category": [{"category": row.category, "total": float(to_money(row.total))} for row in by_category],
    }


@router.get("/budgets", response_model=List[BudgetResponse])
def get_budgets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sync_budget_period_windows(db, current_user)
    db.commit()
    return db.query(Budget).filter(Budget.user_id == current_user.id, Budget.deleted_at.is_(None)).order_by(Budget.created_at.desc()).all()


@router.post("/budgets", response_model=BudgetResponse, status_code=status.HTTP_201_CREATED)
def create_budget(payload: BudgetCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    period, start_date, end_date = normalize_budget_window(current_user, payload.period, payload.start_date, payload.end_date)
    limit_amount = to_money(payload.limit_amount)
    if limit_amount <= 0:
        raise HTTPException(status_code=400, detail="Budget limit_amount must be at least 0.01")

    budget = Budget(
        user_id=current_user.id,
        category=payload.category,
        limit_amount=limit_amount,
        current_spent=to_money(0),
        period=period,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(budget)
    db.flush()
    recalc_budget_spent(db, current_user.id, payload.category)
    db.commit()
    db.refresh(budget)
    return budget


@router.put("/budgets/{budget_id}", response_model=BudgetResponse)
def update_budget(budget_id: UUID, payload: BudgetUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    budget = db.query(Budget).filter(Budget.id == budget_id, Budget.user_id == current_user.id, Budget.deleted_at.is_(None)).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    old_category = budget.category
    if payload.category is not None:
        budget.category = payload.category
    if payload.limit_amount is not None:
        limit_amount = to_money(payload.limit_amount)
        if limit_amount <= 0:
            raise HTTPException(status_code=400, detail="Budget limit_amount must be at least 0.01")
        budget.limit_amount = limit_amount
    if payload.period is not None or "start_date" in payload.model_fields_set or "end_date" in payload.model_fields_set:
        period_changed = payload.period is not None and payload.period != budget.period
        start_date_payload = payload.start_date if "start_date" in payload.model_fields_set else (None if period_changed else budget.start_date)
        end_date_payload = payload.end_date if "end_date" in payload.model_fields_set else (None if period_changed else budget.end_date)
        period, start_date, end_date = normalize_budget_window(
            current_user,
            payload.period or budget.period,
            start_date_payload,
            end_date_payload,
        )
        budget.period = period
        budget.start_date = start_date
        budget.end_date = end_date
    budget.updated_at = utc_now()
    recalc_budget_spent(db, current_user.id, old_category)
    recalc_budget_spent(db, current_user.id, budget.category)
    db.commit()
    db.refresh(budget)
    return budget


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(budget_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    budget = db.query(Budget).filter(Budget.id == budget_id, Budget.user_id == current_user.id, Budget.deleted_at.is_(None)).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    now = utc_now()
    budget.deleted_at = now
    budget.updated_at = now
    db.commit()
    return None


@router.get("/bills", response_model=List[BillReminderResponse])
def get_bill_reminders(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(BillReminder)
        .filter(BillReminder.user_id == current_user.id, BillReminder.deleted_at.is_(None))
        .order_by(BillReminder.due_date.asc())
        .all()
    )


@router.post("/bills", response_model=BillReminderResponse, status_code=status.HTTP_201_CREATED)
def create_bill_reminder(payload: BillReminderCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill = BillReminder(
        user_id=current_user.id,
        title=payload.title,
        category=payload.category,
        amount=to_money(payload.amount) if payload.amount is not None else None,
        due_date=payload.due_date,
    )
    db.add(bill)
    db.commit()
    db.refresh(bill)
    return bill


@router.put("/bills/{bill_id}", response_model=BillReminderResponse)
def update_bill_reminder(bill_id: UUID, payload: BillReminderUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill = get_bill_or_404(db, bill_id, current_user.id)
    changed_fields = payload.model_fields_set

    if bill.is_paid and ({"category", "amount"} & changed_fields):
        raise HTTPException(status_code=400, detail="Unpay the bill before changing amount or category")

    if payload.is_paid is True and not bill.is_paid:
        bill_amount = to_money(bill.amount) if bill.amount is not None else None
        if bill_amount is not None and bill_amount > 0:
            raise HTTPException(status_code=400, detail="Use the bill payment endpoint with wallet_id to mark an amounted bill as paid")
        bill.is_paid = True
        bill.paid_at = utc_now()

    if payload.is_paid is False and bill.is_paid:
        reverse_bill_payment(db, bill, current_user.id)

    if payload.title is not None:
        bill.title = payload.title
    if payload.category is not None:
        bill.category = payload.category
    if "amount" in changed_fields:
        bill.amount = to_money(payload.amount) if payload.amount is not None else None
    if payload.due_date is not None:
        bill.due_date = payload.due_date
    bill.updated_at = utc_now()
    db.commit()
    db.refresh(bill)
    return bill


@router.post("/bills/{bill_id}/pay", response_model=BillReminderResponse)
def mark_bill_paid(
    bill_id: UUID,
    wallet_id: Optional[UUID] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = get_bill_or_404(db, bill_id, current_user.id)
    if bill.is_paid:
        return bill
    if not isinstance(wallet_id, UUID):
        wallet_id = None
    now = utc_now()
    bill_amount = to_money(bill.amount) if bill.amount is not None else None
    requires_wallet = bill_amount is not None and bill_amount > 0

    if bill.paid_transaction_id is None and requires_wallet and wallet_id is None:
        raise HTTPException(status_code=400, detail="wallet_id is required to pay a bill with amount")

    if wallet_id and bill.paid_transaction_id is None:
        if not requires_wallet:
            raise HTTPException(status_code=400, detail="Bill amount must be greater than zero to create payment transaction")
        wallet = get_wallet_or_404(db, wallet_id, current_user.id, lock=True)
        transaction = Transaction(
            wallet_id=wallet.id,
            type=TransactionTypeEnum.expense,
            amount=bill_amount,
            category=bill.category,
            transaction_date=now,
            description=f"Bill payment: {bill.title}",
        )
        apply_transaction_to_wallet(wallet, transaction)
        db.add(transaction)
        db.flush()
        bill.paid_transaction_id = transaction.id
        record_finance_event(
            db,
            user_id=current_user.id,
            event_type="bill_paid",
            wallet=wallet,
            transaction=transaction,
            bill=bill,
            amount_delta=transaction_balance_delta(transaction),
            description=f"Bill payment: {bill.title}",
        )
        recalc_budget_spent(db, current_user.id, bill.category)
    bill.is_paid = True
    bill.paid_at = bill.paid_at or now
    bill.updated_at = now
    db.commit()
    db.refresh(bill)
    return bill


@router.post("/bills/{bill_id}/unpay", response_model=BillReminderResponse)
def mark_bill_unpaid(
    bill_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bill = get_bill_or_404(db, bill_id, current_user.id)
    if not bill.is_paid:
        return bill
    reverse_bill_payment(db, bill, current_user.id)
    db.commit()
    db.refresh(bill)
    return bill


@router.delete("/bills/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bill_reminder(bill_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    bill = db.query(BillReminder).filter(BillReminder.id == bill_id, BillReminder.user_id == current_user.id, BillReminder.deleted_at.is_(None)).first()
    if not bill:
        raise HTTPException(status_code=404, detail="Bill reminder not found")
    if bill.is_paid:
        reverse_bill_payment(db, bill, current_user.id)
    now = utc_now()
    bill.deleted_at = now
    bill.updated_at = now
    db.commit()
    return None
