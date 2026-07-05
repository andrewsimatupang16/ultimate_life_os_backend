from decimal import Decimal
from datetime import timedelta
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
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
    Transaction,
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
from app.utils.money import to_money
from app.utils.time import local_date_for_user, local_day_bounds_utc, local_period_bounds_utc, utc_now

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


def partner_goal_progress(goal: Goal) -> float:
    if goal.is_completed:
        return 100.0
    if goal.progress_mode != "weighted_subgoals" and goal.target_value and goal.target_value > 0:
        return round(min(100.0, max(0.0, ((goal.current_value or 0.0) / goal.target_value) * 100)), 2)

    active_subgoals = [subgoal for subgoal in goal.sub_goals if subgoal.deleted_at is None]
    if not active_subgoals:
        return 0.0

    progress_values = []
    for subgoal in active_subgoals:
        if subgoal.is_completed:
            progress_values.append(100.0)
            continue
        visible_tasks = [task for task in subgoal.tasks if task.deleted_at is None and not task.is_private]
        if visible_tasks:
            completed_tasks = sum(1 for task in visible_tasks if task.is_completed)
            progress_values.append((completed_tasks / len(visible_tasks)) * 100)
        elif subgoal.target_value and subgoal.target_value > 0:
            progress_values.append(min(100.0, max(0.0, ((subgoal.current_value or 0.0) / subgoal.target_value) * 100)))
        else:
            progress_values.append(0.0)

    return round(sum(progress_values) / len(progress_values), 2)


def visible_partner_transactions_query(db: Session, partner_id: UUID):
    return db.query(Transaction).join(Wallet).filter(
        Wallet.user_id == partner_id,
        Wallet.deleted_at.is_(None),
        Transaction.is_private.is_(False),
        Transaction.deleted_at.is_(None),
    )


def partner_transaction_total(
    db: Session,
    partner_id: UUID,
    transaction_type: TransactionTypeEnum,
    start_date,
    end_date,
) -> float:
    return float(to_money(visible_partner_transactions_query(db, partner_id).filter(
        Transaction.type == transaction_type,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
    ).with_entities(func.sum(Transaction.amount)).scalar()))


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


@router.get("/{partner_id}/analytics/dashboard")
def get_partner_dashboard_analytics(partner_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    accepted_connection_or_404(db, current_user.id, partner_id)
    partner = db.query(User).filter(User.id == partner_id, User.deleted_at.is_(None)).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")

    now = utc_now()
    local_today = local_date_for_user(current_user, now)
    week_start, week_end = local_period_bounds_utc(current_user, "weekly", now)
    month_start, month_end = local_period_bounds_utc(current_user, "monthly", now)
    cashflow_start, _ = local_day_bounds_utc(current_user, local_today - timedelta(days=6))
    _, cashflow_end = local_day_bounds_utc(current_user, local_today)

    goals = db.query(Goal).options(joinedload(Goal.sub_goals).joinedload(SubGoal.tasks)).filter(
        Goal.user_id == partner_id,
        Goal.deleted_at.is_(None),
    ).order_by(Goal.is_completed.asc(), Goal.target_date.asc().nullslast(), Goal.created_at.asc()).all()
    visible_tasks_query = db.query(Task).filter(
        Task.user_id == partner_id,
        Task.is_private.is_(False),
        Task.deleted_at.is_(None),
    )
    total_tasks = visible_tasks_query.count()
    completed_tasks = visible_tasks_query.filter(Task.is_completed.is_(True)).count()
    completed_this_week = visible_tasks_query.filter(
        Task.is_completed.is_(True),
        Task.completed_at.isnot(None),
        Task.completed_at >= week_start,
        Task.completed_at < min(now, week_end),
    ).count()
    created_this_week = visible_tasks_query.filter(
        Task.created_at >= week_start,
        Task.created_at < min(now, week_end),
    ).count()

    total_goals = len(goals)
    completed_goals = sum(1 for goal in goals if goal.is_completed)
    good_habits = db.query(Habit).filter(Habit.user_id == partner_id, Habit.habit_type == HabitTypeEnum.good, Habit.deleted_at.is_(None)).count()
    bad_habits = db.query(Habit).filter(Habit.user_id == partner_id, Habit.habit_type == HabitTypeEnum.bad, Habit.deleted_at.is_(None)).count()
    total_habit_completions = db.query(func.coalesce(func.sum(Habit.total_completions), 0)).filter(Habit.user_id == partner_id, Habit.deleted_at.is_(None)).scalar() or 0

    total_wallets = db.query(Wallet).filter(Wallet.user_id == partner_id, Wallet.deleted_at.is_(None)).count()
    total_balance = to_money(db.query(func.sum(Wallet.balance)).filter(Wallet.user_id == partner_id, Wallet.deleted_at.is_(None)).scalar())
    total_income_month = partner_transaction_total(db, partner_id, TransactionTypeEnum.income, month_start, month_end)
    total_expense_month = partner_transaction_total(db, partner_id, TransactionTypeEnum.expense, month_start, month_end)

    cashflow_map = {
        (local_today - timedelta(days=i)).isoformat(): {"income": 0.0, "expense": 0.0}
        for i in range(6, -1, -1)
    }
    cashflow_rows = visible_partner_transactions_query(db, partner_id).filter(
        Transaction.transaction_date >= cashflow_start,
        Transaction.transaction_date < cashflow_end,
    ).all()
    for tx in cashflow_rows:
        day = local_date_for_user(current_user, tx.transaction_date).isoformat()
        if day not in cashflow_map:
            continue
        key = "income" if tx.type == TransactionTypeEnum.income else "expense"
        cashflow_map[day][key] += float(tx.amount or 0.0)
    weekly_cashflow = [{"date": day, **cashflow_map[day]} for day in sorted(cashflow_map.keys())]

    daily_task_trend = []
    local_week_start = local_today - timedelta(days=local_today.weekday())
    for day_index in range(7):
        local_day = local_week_start + timedelta(days=day_index)
        day_start, day_end = local_day_bounds_utc(current_user, local_day)
        total = visible_tasks_query.filter(
            Task.due_date.isnot(None),
            Task.due_date >= day_start,
            Task.due_date < day_end,
        ).count()
        completed = visible_tasks_query.filter(
            Task.is_completed.is_(True),
            Task.due_date.isnot(None),
            Task.due_date >= day_start,
            Task.due_date < day_end,
        ).count()
        daily_task_trend.append({
            "date": local_day.isoformat(),
            "total": total,
            "completed": completed,
            "completion_rate": round((completed / total * 100) if total else 0.0, 2),
        })

    category_rows = visible_partner_transactions_query(db, partner_id).filter(
        Transaction.transaction_date >= month_start,
        Transaction.transaction_date < month_end,
    ).with_entities(
        Transaction.category,
        Transaction.type,
        func.coalesce(func.sum(Transaction.amount), 0.0).label("total"),
    ).group_by(Transaction.category, Transaction.type).all()
    financial_breakdown_map = {}
    for row in category_rows:
        category = row.category or "Lainnya"
        financial_breakdown_map.setdefault(category, {"category": category, "income": 0.0, "expense": 0.0})
        key = "income" if row.type == TransactionTypeEnum.income else "expense"
        financial_breakdown_map[category][key] += float(row.total or 0.0)

    numeric_goal_progress = [
        {
            "id": str(goal.id),
            "title": goal.title,
            "target_value": float(goal.target_value or 100.0),
            "current_value": float(goal.current_value or 0.0),
            "target_unit": goal.target_unit,
            "progress_mode": goal.progress_mode or "manual",
            "progress_rate": partner_goal_progress(goal),
        }
        for goal in goals[:6]
    ]

    completion_rate = round((completed_tasks / total_tasks * 100) if total_tasks else 0.0, 2)
    productivity_score = min(100.0, max(0.0, completion_rate * 0.75 + min(25.0, total_habit_completions * 1.5)))
    finance_score = 0.0
    if total_income_month > 0:
        finance_score = min(100.0, max(0.0, 50.0 + ((total_income_month - total_expense_month) / total_income_month * 100)))
    elif total_expense_month > 0:
        finance_score = 35.0
    life_score = round((productivity_score + finance_score) / 2, 2)

    return {
        "level": partner.level or 1,
        "xp_balance": partner.xp_balance or 0,
        "total_xp_earned": partner.total_xp_earned or 0,
        "coin_balance": partner.coin_balance or 0,
        "xp_needed_for_next_level": 0,
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "good_habits": good_habits,
        "bad_habits": bad_habits,
        "total_habit_completions": int(total_habit_completions),
        "total_wallets": total_wallets,
        "total_balance": float(total_balance),
        "total_income_month": round(total_income_month, 2),
        "total_expense_month": round(total_expense_month, 2),
        "weekly_task_metrics": {
            "total": total_tasks,
            "completed": completed_tasks,
            "completion_rate": completion_rate,
            "completed_this_week": completed_this_week,
            "created_this_week": created_this_week,
            "overdue": visible_tasks_query.filter(Task.is_completed.is_(False), Task.due_date.isnot(None), Task.due_date < now).count(),
            "due_today": 0,
        },
        "weekly_cashflow": weekly_cashflow,
        "financial_breakdown": list(financial_breakdown_map.values()),
        "weekly_comparison": [],
        "task_completion_rates": [],
        "daily_task_trend": daily_task_trend,
        "upcoming_deadlines": [],
        "recent_activities": [],
        "numeric_goal_progress": numeric_goal_progress,
        "time_allocation": [],
        "productivity_score": round(productivity_score, 2),
        "finance_score": round(finance_score, 2),
        "life_score": life_score,
    }


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
