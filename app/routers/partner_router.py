from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    AccountabilityConnection,
    BillReminder,
    Budget,
    ConnectionStatusEnum,
    Goal,
    Habit,
    SubGoal,
    Task,
    TransactionTypeEnum,
    User,
    Wallet,
)
from app.schemas import (
    AccountabilityConnectionResponse,
    BillReminderResponse,
    BudgetResponse,
    GoalResponse,
    HabitResponse,
    PartnerConsentRequest,
    PartnerSharingScopeResponse,
    SubGoalResponse,
    TaskResponse,
    TransactionResponse,
    UserPublicProfile,
    WalletResponse,
)
from app.utils.time import utc_now

router = APIRouter(prefix="/partner", tags=["Partner"])

PARTNER_SHARED_DATA = [
    {
        "key": "profile",
        "label": "Profile dan gamification",
        "description": "Nama, avatar, level, XP, coin, title aktif, timezone, dan streak task.",
    },
    {
        "key": "productivity",
        "label": "Produktivitas",
        "description": "Goal, sub-goal, habit, serta task non-private.",
    },
    {
        "key": "finance",
        "label": "Finance dashboard",
        "description": "Wallet, saldo, ringkasan income, expense, net, transaksi non-private, budget, dan tagihan.",
    },
]


def sharing_scope_payload() -> dict:
    return {
        "consent_required": True,
        "visibility_note": "Koneksi partner bersifat dua arah. Setelah diterima, kedua user dapat saling melihat dashboard partner sampai salah satu melakukan disconnect.",
        "shared_data": PARTNER_SHARED_DATA,
    }


def serialize_connection(connection: AccountabilityConnection) -> dict:
    data = AccountabilityConnectionResponse.model_validate(connection).model_dump(mode="json")
    return data


def accepted_connection_or_404(db: Session, current_user_id: UUID, partner_id: UUID) -> AccountabilityConnection:
    conn = db.query(AccountabilityConnection).filter(
        AccountabilityConnection.deleted_at.is_(None),
        AccountabilityConnection.status == ConnectionStatusEnum.accepted,
        or_(
            (AccountabilityConnection.requester_id == current_user_id) & (AccountabilityConnection.receiver_id == partner_id),
            (AccountabilityConnection.requester_id == partner_id) & (AccountabilityConnection.receiver_id == current_user_id),
        ),
    ).first()
    if not conn:
        raise HTTPException(status_code=403, detail="You are not connected with this user")
    return conn


def goal_to_dict(goal: Goal) -> dict:
    data = GoalResponse.model_validate(goal).model_dump(mode="json")
    data["sub_goals"] = []
    for sg in [x for x in goal.sub_goals if x.deleted_at is None]:
        sg_data = SubGoalResponse.model_validate(sg).model_dump(mode="json")
        sg_data["tasks"] = [TaskResponse.model_validate(t).model_dump(mode="json") for t in sg.tasks if t.deleted_at is None and not t.is_private]
        data["sub_goals"].append(sg_data)
    return data


@router.get("/sharing-scope", response_model=PartnerSharingScopeResponse)
def get_sharing_scope():
    return sharing_scope_payload()


@router.post("/request/{friend_code}", response_model=AccountabilityConnectionResponse, status_code=status.HTTP_201_CREATED)
def send_request(
    friend_code: str,
    body: PartnerConsentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.consent_acknowledged:
        raise HTTPException(status_code=400, detail="Partner sharing consent is required")

    receiver = db.query(User).filter(User.friend_code == friend_code.upper(), User.deleted_at.is_(None)).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="Friend code not found")
    if receiver.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot connect with yourself")

    existing = db.query(AccountabilityConnection).filter(
        AccountabilityConnection.deleted_at.is_(None),
        or_(
            (AccountabilityConnection.requester_id == current_user.id) & (AccountabilityConnection.receiver_id == receiver.id),
            (AccountabilityConnection.requester_id == receiver.id) & (AccountabilityConnection.receiver_id == current_user.id),
        ),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Connection already exists with status: {existing.status.value}")

    connection = AccountabilityConnection(requester_id=current_user.id, receiver_id=receiver.id, status=ConnectionStatusEnum.pending)
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


@router.put("/accept/{connection_id}", response_model=AccountabilityConnectionResponse)
def accept_request(
    connection_id: UUID,
    body: PartnerConsentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not body.consent_acknowledged:
        raise HTTPException(status_code=400, detail="Partner sharing consent is required")

    connection = db.query(AccountabilityConnection).filter(
        AccountabilityConnection.id == connection_id,
        AccountabilityConnection.receiver_id == current_user.id,
        AccountabilityConnection.status == ConnectionStatusEnum.pending,
        AccountabilityConnection.deleted_at.is_(None),
    ).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Pending request not found")
    connection.status = ConnectionStatusEnum.accepted
    connection.updated_at = utc_now()
    db.commit()
    db.refresh(connection)
    return connection


@router.put("/reject/{connection_id}", response_model=AccountabilityConnectionResponse)
def reject_request(connection_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    connection = db.query(AccountabilityConnection).filter(
        AccountabilityConnection.id == connection_id,
        AccountabilityConnection.receiver_id == current_user.id,
        AccountabilityConnection.status == ConnectionStatusEnum.pending,
        AccountabilityConnection.deleted_at.is_(None),
    ).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Pending request not found")
    connection.status = ConnectionStatusEnum.rejected
    connection.updated_at = utc_now()
    db.commit()
    db.refresh(connection)
    return connection


@router.delete("/disconnect/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(connection_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    connection = db.query(AccountabilityConnection).filter(
        AccountabilityConnection.id == connection_id,
        AccountabilityConnection.deleted_at.is_(None),
        or_(AccountabilityConnection.requester_id == current_user.id, AccountabilityConnection.receiver_id == current_user.id),
    ).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")
    now = utc_now()
    connection.deleted_at = now
    connection.updated_at = now
    db.commit()
    return None


@router.get("/connections", response_model=List[AccountabilityConnectionResponse])
def get_connections(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(AccountabilityConnection).options(
        joinedload(AccountabilityConnection.requester),
        joinedload(AccountabilityConnection.receiver),
    ).filter(
        AccountabilityConnection.deleted_at.is_(None),
        or_(AccountabilityConnection.requester_id == current_user.id, AccountabilityConnection.receiver_id == current_user.id),
    ).order_by(AccountabilityConnection.created_at.desc()).all()


@router.get("/{partner_id}/profile", response_model=UserPublicProfile)
def get_partner_profile(partner_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accepted_connection_or_404(db, current_user.id, partner_id)
    partner = db.query(User).filter(User.id == partner_id, User.deleted_at.is_(None)).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return partner


@router.get("/{partner_id}/productivity")
def get_partner_productivity(partner_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accepted_connection_or_404(db, current_user.id, partner_id)
    goals = db.query(Goal).options(joinedload(Goal.sub_goals).joinedload(SubGoal.tasks)).filter(Goal.user_id == partner_id, Goal.deleted_at.is_(None)).all()
    habits = db.query(Habit).filter(Habit.user_id == partner_id, Habit.deleted_at.is_(None)).all()
    tasks = db.query(Task).filter(Task.user_id == partner_id, Task.is_private.is_(False), Task.deleted_at.is_(None)).order_by(Task.created_at.desc()).all()
    return {
        "goals": [goal_to_dict(g) for g in goals],
        "habits": [HabitResponse.model_validate(h).model_dump(mode="json") for h in habits],
        "tasks": [TaskResponse.model_validate(t).model_dump(mode="json") for t in tasks],
    }


@router.get("/{partner_id}/tasks", response_model=List[TaskResponse])
def get_partner_tasks(partner_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accepted_connection_or_404(db, current_user.id, partner_id)
    return db.query(Task).filter(Task.user_id == partner_id, Task.is_private.is_(False), Task.deleted_at.is_(None)).order_by(Task.created_at.desc()).all()


@router.get("/{partner_id}/finance")
def get_partner_finance(partner_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accepted_connection_or_404(db, current_user.id, partner_id)
    wallets = db.query(Wallet).options(joinedload(Wallet.transactions)).filter(Wallet.user_id == partner_id, Wallet.deleted_at.is_(None)).all()
    budgets = db.query(Budget).filter(Budget.user_id == partner_id, Budget.deleted_at.is_(None)).order_by(Budget.created_at.desc()).all()
    bills = db.query(BillReminder).filter(BillReminder.user_id == partner_id, BillReminder.deleted_at.is_(None)).order_by(BillReminder.due_date.asc()).all()
    wallets_data = []
    transactions_data = []
    total_income = Decimal("0.00")
    total_expense = Decimal("0.00")
    total_balance = Decimal("0.00")

    for wallet in wallets:
        wallet_data = WalletResponse.model_validate(wallet).model_dump(mode="json")
        visible_transactions = [tx for tx in wallet.transactions if tx.deleted_at is None and not tx.is_private]
        wallet_data["transactions"] = [TransactionResponse.model_validate(tx).model_dump(mode="json") for tx in visible_transactions]
        wallets_data.append(wallet_data)
        transactions_data.extend(wallet_data["transactions"])
        total_balance += wallet.balance or Decimal("0.00")

        for tx in visible_transactions:
            amount = tx.amount or Decimal("0.00")
            if tx.type == TransactionTypeEnum.income:
                total_income += amount
            else:
                total_expense += amount

    return {
        "wallets": wallets_data,
        "transactions": sorted(transactions_data, key=lambda tx: tx.get("transaction_date") or "", reverse=True),
        "budgets": [BudgetResponse.model_validate(b).model_dump(mode="json") for b in budgets],
        "bills": [BillReminderResponse.model_validate(b).model_dump(mode="json") for b in bills],
        "summary": {
            "total_income": float(total_income),
            "total_expense": float(total_expense),
            "net": float(total_income - total_expense),
            "total_balance": float(total_balance),
        },
    }
