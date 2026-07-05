from datetime import date, datetime, timedelta
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.gamification_service import GamificationService
from app.models import (
    BillReminder,
    Budget,
    Goal,
    Habit,
    HabitLog,
    HabitTypeEnum,
    SubGoal,
    Task,
    Transaction,
    TransactionTypeEnum,
    User,
    Wallet,
)
from app.schemas import DashboardSummaryResponse, MonthlyComparisonSummaryResponse
from app.utils.money import to_money
from app.utils.time import local_date_for_user, local_day_bounds_utc, local_period_bounds_utc, utc_now

router = APIRouter(prefix="/analytics", tags=["Analytics"])
logger = logging.getLogger("life_os.analytics")

FINANCIAL_CATEGORIES = [
    "Kebutuhan Pokok",
    "Transportasi",
    "Gaya Hidup",
    "Tagihan",
    "Investasi",
    "Lainnya",
]

def empty_dashboard_summary() -> dict:
    now = utc_now()
    weekly_cashflow = [
        {
            "date": (now - timedelta(days=i)).date().isoformat(),
            "income": 0.0,
            "expense": 0.0,
        }
        for i in range(6, -1, -1)
    ]

    return {
        "level": 1,
        "xp_balance": 0,
        "total_xp_earned": 0,
        "coin_balance": 0,
        "xp_needed_for_next_level": GamificationService.xp_for_next_level(1),
        "total_goals": 0,
        "completed_goals": 0,
        "total_tasks": 0,
        "completed_tasks": 0,
        "good_habits": 0,
        "bad_habits": 0,
        "total_habit_completions": 0,
        "total_wallets": 0,
        "total_balance": 0.0,
        "total_income_month": 0.0,
        "total_expense_month": 0.0,
        "weekly_task_metrics": {
            "total": 0,
            "completed": 0,
            "completion_rate": 0.0,
            "due_total": 0,
            "due_completed": 0,
            "completed_this_week": 0,
            "created_this_week": 0,
            "overdue": 0,
            "due_today": 0,
        },
        "weekly_cashflow": weekly_cashflow,
        "financial_breakdown": [
            {"category": category, "income": 0.0, "expense": 0.0}
            for category in FINANCIAL_CATEGORIES
        ],
        "weekly_comparison": [
            {"label": "Income", "current": 0.0, "previous": 0.0, "change": 0.0},
            {"label": "Expense", "current": 0.0, "previous": 0.0, "change": 0.0},
            {"label": "Task Due Completion", "current": 0.0, "previous": 0.0, "change": 0.0},
        ],
        "task_completion_rates": [
            {"period": "Due This Week", "total": 0, "completed": 0, "completion_rate": 0.0},
            {"period": "Due Last Week", "total": 0, "completed": 0, "completion_rate": 0.0},
            {"period": "Due Last 30 Days", "total": 0, "completed": 0, "completion_rate": 0.0},
        ],
        "daily_task_trend": [],
        "upcoming_deadlines": [],
        "recent_activities": [],
        "numeric_goal_progress": [],
        "time_allocation": [],
        "productivity_score": 0.0,
        "finance_score": 0.0,
        "life_score": 0.0,
    }



def dashboard_fallback_for_user(current_user: User | None = None) -> dict:
    """
    Fallback aman agar halaman dashboard tetap bisa dibuka ketika salah satu query
    analytics gagal, misalnya karena data lama, nilai NULL, atau tabel belum lengkap.

    Catatan desain:
    - Fallback hanya boleh dipakai saat production, agar user tidak melihat halaman blank.
    - Saat development/test, error harus dinaikkan supaya akar masalah dashboard terlihat.
    """
    summary = empty_dashboard_summary()
    summary["is_fallback"] = True
    summary["warning"] = "Dashboard analytics gagal dihitung. Data ini adalah fallback dan belum tentu lengkap."

    if current_user is None:
        return summary

    summary.update({
        "level": current_user.level or 1,
        "xp_balance": current_user.xp_balance or 0,
        "total_xp_earned": current_user.total_xp_earned or 0,
        "coin_balance": current_user.coin_balance or 0,
        "xp_needed_for_next_level": GamificationService.xp_for_next_level(current_user.level or 1),
    })
    return summary


def dashboard_fallback_enabled() -> bool:
    """Return True only when dashboard fallback is explicitly safe to use.

    Default behaviour:
    - development/test/staging: strict mode, raise a 500 so bugs are visible.
    - production/prod: allow fallback to keep the UI available.

    Optional override:
    - DASHBOARD_ALLOW_FALLBACK=true/false
    """
    explicit_value = os.getenv("DASHBOARD_ALLOW_FALLBACK")
    if explicit_value is not None:
        return explicit_value.strip().lower() in {"1", "true", "yes", "on"}

    app_env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
    return app_env in {"production", "prod"}


def normalize_financial_category(category: str | None) -> str:
    text = (category or "").strip().lower()
    if any(keyword in text for keyword in ["makan", "food", "groceries", "belanja", "sembako", "pokok", "dapur"]):
        return "Kebutuhan Pokok"
    if any(keyword in text for keyword in ["transport", "bensin", "parkir", "ojek", "grab", "gojek", "tol"]):
        return "Transportasi"
    if any(keyword in text for keyword in ["hiburan", "lifestyle", "gaya", "kopi", "cafe", "jajan", "movie", "shopping"]):
        return "Gaya Hidup"
    if any(keyword in text for keyword in ["tagihan", "listrik", "air", "internet", "pulsa", "sewa", "cicilan"]):
        return "Tagihan"
    if any(keyword in text for keyword in ["invest", "saham", "reksa", "emas", "deposito", "tabungan"]):
        return "Investasi"
    return "Lainnya"


def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


def task_due_rate(
    db: Session,
    user_id,
    start_date: datetime,
    end_date: datetime,
    completed_cutoff: datetime | None = None,
) -> dict:
    """Return completion rate for tasks whose due_date falls in [start_date, end_date).

    This intentionally does not use ``created_at``. ``created_at`` answers
    "when was the task entered", not "which task belongs to this period".
    The dashboard's productivity denominator is therefore based on due dates,
    while completed counters use ``completed_at`` separately.
    """
    completed_cutoff = completed_cutoff or end_date
    base_filters = (
        Task.user_id == user_id,
        Task.due_date.isnot(None),
        Task.due_date >= start_date,
        Task.due_date < end_date,
        Task.deleted_at.is_(None),
    )
    total = db.query(Task).filter(*base_filters).count()
    completed = db.query(Task).filter(
        *base_filters,
        Task.is_completed.is_(True),
        # Legacy rows may have is_completed=True without completed_at.
        # Count them as completed rather than dropping historical data.
        or_(Task.completed_at.is_(None), Task.completed_at < completed_cutoff),
    ).count()
    return {
        "total": total,
        "completed": completed,
        "completion_rate": round((completed / total * 100) if total else 0.0, 2),
    }


def task_created_count(db: Session, user_id, start_date: datetime, end_date: datetime) -> int:
    return db.query(Task).filter(
        Task.user_id == user_id,
        Task.created_at >= start_date,
        Task.created_at < end_date,
        Task.deleted_at.is_(None),
    ).count()


def task_completed_count(db: Session, user_id, start_date: datetime, end_date: datetime) -> int:
    return db.query(Task).filter(
        Task.user_id == user_id,
        Task.is_completed.is_(True),
        Task.completed_at.isnot(None),
        Task.completed_at >= start_date,
        Task.completed_at < end_date,
        Task.deleted_at.is_(None),
    ).count()


def task_overdue_count(db: Session, user_id, now: datetime) -> int:
    return db.query(Task).filter(
        Task.user_id == user_id,
        Task.is_completed.is_(False),
        Task.due_date.isnot(None),
        Task.due_date < now,
        Task.deleted_at.is_(None),
    ).count()


def finance_total(db: Session, user_id, transaction_type: TransactionTypeEnum, start_date: datetime, end_date: datetime) -> float:
    return float(to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.type == transaction_type,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).scalar()))


def productivity_score(db: Session, user_id, start_date: datetime | None = None, end_date: datetime | None = None) -> float:
    now = utc_now()
    period_start = start_date or (now - timedelta(days=7))
    period_end = end_date or now

    due_tasks = db.query(Task).filter(
        Task.user_id == user_id,
        Task.due_date.isnot(None),
        Task.due_date >= period_start,
        Task.due_date < period_end,
        Task.deleted_at.is_(None),
    ).count()
    completed_due_tasks = db.query(Task).filter(
        Task.user_id == user_id,
        Task.is_completed.is_(True),
        Task.due_date.isnot(None),
        Task.due_date >= period_start,
        Task.due_date < period_end,
        Task.deleted_at.is_(None),
    ).count()
    completed_recent_tasks = db.query(Task).filter(
        Task.user_id == user_id,
        Task.is_completed.is_(True),
        Task.completed_at.isnot(None),
        Task.completed_at >= period_start,
        Task.completed_at < min(now, period_end),
        Task.deleted_at.is_(None),
    ).count()
    open_goal_count = db.query(Goal).filter(
        Goal.user_id == user_id,
        Goal.is_completed.is_(False),
        Goal.deleted_at.is_(None),
    ).count()
    overdue_tasks = task_overdue_count(db, user_id, now)
    good_streak_sum = db.query(func.coalesce(func.sum(Habit.current_streak), 0)).filter(
        Habit.user_id == user_id,
        Habit.habit_type == HabitTypeEnum.good,
        Habit.deleted_at.is_(None),
    ).scalar() or 0

    task_rate = (completed_due_tasks / due_tasks * 100) if due_tasks else min(100.0, completed_recent_tasks * 18.0)
    habit_points = min(20.0, float(good_streak_sum) * 1.5)
    goal_focus_points = 10.0 if open_goal_count > 0 else 0.0
    overdue_penalty = min(30.0, overdue_tasks * 7.5)

    return round(min(100.0, max(0.0, (task_rate * 0.70) + habit_points + goal_focus_points - overdue_penalty)), 2)


def finance_score(db: Session, user_id, start_date: datetime | None = None) -> float:
    start_date = start_date or (utc_now() - timedelta(days=30))
    income = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.income,
        Transaction.transaction_date >= start_date,
        Transaction.deleted_at.is_(None),
    ).scalar())
    expense = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.transaction_date >= start_date,
        Transaction.deleted_at.is_(None),
    ).scalar())
    income = float(income)
    expense = float(expense)
    budget_rows = db.query(Budget).filter(
        Budget.user_id == user_id,
        Budget.deleted_at.is_(None),
    ).all()
    if budget_rows:
        budget_scores = []
        for budget in budget_rows:
            if not budget.limit_amount or budget.limit_amount <= 0:
                continue
            usage_rate = float(budget.current_spent or 0) / float(budget.limit_amount)
            budget_scores.append(max(0.0, min(100.0, 100.0 - max(0.0, usage_rate - 1.0) * 100.0)))
        budget_compliance = sum(budget_scores) / len(budget_scores) if budget_scores else 70.0
    else:
        budget_compliance = 70.0

    if income > 0:
        savings_rate = ((income - expense) / income) * 100
        savings_score = min(100.0, max(0.0, 50.0 + savings_rate))
        return round(min(100.0, max(0.0, (budget_compliance * 0.65) + (savings_score * 0.35))), 2)

    if expense > 0:
        return round(min(60.0, max(0.0, budget_compliance * 0.55)), 2)

    return 0.0


def dashboard_goal_progress(goal: Goal) -> float:
    if goal.is_completed:
        return 100.0
    if goal.progress_mode != "weighted_subgoals" and goal.target_value and goal.target_value > 0:
        return round(min(100.0, max(0.0, ((goal.current_value or 0.0) / goal.target_value) * 100)), 2)

    active_subgoals = [subgoal for subgoal in goal.sub_goals if subgoal.deleted_at is None]
    if not active_subgoals:
        return 0.0

    total_weight = sum(max(1, min(5, subgoal.weight or 1)) for subgoal in active_subgoals)
    if total_weight <= 0:
        return 0.0

    weighted_progress = 0.0
    for subgoal in active_subgoals:
        if subgoal.is_completed:
            subgoal_rate = 100.0
        elif subgoal.target_value and subgoal.target_value > 0:
            subgoal_rate = min(100.0, max(0.0, ((subgoal.current_value or 0.0) / subgoal.target_value) * 100))
        else:
            active_tasks = [task for task in subgoal.tasks if task.deleted_at is None]
            completed_tasks = sum(1 for task in active_tasks if task.is_completed)
            subgoal_rate = (completed_tasks / len(active_tasks) * 100) if active_tasks else 0.0
        weighted_progress += subgoal_rate * max(1, min(5, subgoal.weight or 1))

    return round(weighted_progress / total_weight, 2)


def add_months(local_month_start: date, offset: int) -> date:
    month_index = (local_month_start.year * 12 + local_month_start.month - 1) + offset
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def build_monthly_comparison_summary(current_user: User, db: Session, months: int = 6) -> dict:
    months = max(1, min(months, 12))
    now = utc_now()
    local_today = local_date_for_user(current_user, now)
    current_month_start = local_today.replace(day=1)
    items = []

    for offset in range(-(months - 1), 1):
        local_start = add_months(current_month_start, offset)
        local_end = add_months(local_start, 1)
        start_date, _ = local_day_bounds_utc(current_user, local_start)
        end_date, _ = local_day_bounds_utc(current_user, local_end)

        income = finance_total(db, current_user.id, TransactionTypeEnum.income, start_date, end_date)
        expense = finance_total(db, current_user.id, TransactionTypeEnum.expense, start_date, end_date)
        net = income - expense
        savings_rate = round(((income - expense) / income * 100) if income > 0 else 0.0, 2)
        finance_month_score = round(min(100.0, max(0.0, (savings_rate / 30.0) * 100)) if income > 0 else 0.0, 2)

        due_rate = task_due_rate(db, current_user.id, start_date, end_date, completed_cutoff=end_date)
        goals_completed = db.query(Goal).filter(
            Goal.user_id == current_user.id,
            Goal.is_completed.is_(True),
            Goal.completed_at.isnot(None),
            Goal.completed_at >= start_date,
            Goal.completed_at < end_date,
            Goal.deleted_at.is_(None),
        ).count()
        habit_completions = db.query(HabitLog).filter(
            HabitLog.user_id == current_user.id,
            HabitLog.local_date >= local_start,
            HabitLog.local_date < local_end,
            HabitLog.deleted_at.is_(None),
        ).count()
        tracked_minutes = 0
        productivity_month_score = round(min(100.0, max(0.0,
            (due_rate["completion_rate"] * 0.70)
            + min(20.0, habit_completions * 1.5)
            + min(10.0, goals_completed * 5.0)
        )), 2)

        items.append({
            "year_month": f"{local_start.year:04d}-{local_start.month:02d}",
            "label": local_start.strftime("%b %Y"),
            "start_date": start_date,
            "end_date": end_date,
            "finance": {
                "income": round(income, 2),
                "expense": round(expense, 2),
                "net": round(net, 2),
                "savings_rate": savings_rate,
                "score": finance_month_score,
            },
            "productivity": {
                "due_tasks": due_rate["total"],
                "completed_tasks": due_rate["completed"],
                "completion_rate": due_rate["completion_rate"],
                "goals_completed": goals_completed,
                "habit_completions": habit_completions,
                "tracked_minutes": tracked_minutes,
                "score": productivity_month_score,
            },
        })

    return {
        "months": months,
        "generated_at": now,
        "items": items,
    }


@router.get("/life-score")
def get_life_score(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    now = utc_now()
    week_start, week_end = local_period_bounds_utc(current_user, "weekly", now)
    p_score = productivity_score(db, current_user.id, week_start, week_end)
    f_score = finance_score(db, current_user.id)
    life = round((p_score + f_score) / 2, 2)
    return {"productivity_score": p_score, "finance_score": f_score, "life_score": life}


@router.get("/monthly-summary", response_model=MonthlyComparisonSummaryResponse)
def get_monthly_summary(
    months: int = 6,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return build_monthly_comparison_summary(current_user, db, months)


@router.get("/dashboard", response_model=DashboardSummaryResponse)
def get_dashboard_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        summary = build_dashboard_summary(current_user, db)
        summary.setdefault("is_fallback", False)
        summary.setdefault("warning", None)
        return summary
    except Exception as exc:
        db.rollback()
        logger.exception("dashboard_summary_failed user_id=%s", getattr(current_user, "id", None))

        if dashboard_fallback_enabled():
            return dashboard_fallback_for_user(current_user)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Dashboard calculation failed. "
                "Fallback is disabled outside production so the real analytics error can be fixed. "
                "Check backend logs for 'dashboard_summary_failed'."
            ),
        ) from exc


def build_dashboard_summary(current_user: User, db: Session) -> dict:
    now = utc_now()
    local_today = local_date_for_user(current_user, now)
    month_start, month_end = local_period_bounds_utc(current_user, "monthly", now)
    week_start, week_end = local_period_bounds_utc(current_user, "weekly", now)
    previous_week_start = week_start - timedelta(days=7)
    previous_week_end = week_start
    elapsed_week_span = max(timedelta(0), min(now, week_end) - week_start)
    same_span_previous_week_end = min(previous_week_end, previous_week_start + elapsed_week_span)
    last_30_days_start = now - timedelta(days=30)
    today_start, today_end = local_day_bounds_utc(current_user, local_today)
    deadline_limit = now + timedelta(days=14)

    total_goals = db.query(Goal).filter(Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).count()
    completed_goals = db.query(Goal).filter(Goal.user_id == current_user.id, Goal.is_completed.is_(True), Goal.deleted_at.is_(None)).count()
    total_tasks = db.query(Task).filter(Task.user_id == current_user.id, Task.deleted_at.is_(None)).count()
    completed_tasks = db.query(Task).filter(Task.user_id == current_user.id, Task.is_completed.is_(True), Task.deleted_at.is_(None)).count()
    good_habits = db.query(Habit).filter(Habit.user_id == current_user.id, Habit.habit_type == HabitTypeEnum.good, Habit.deleted_at.is_(None)).count()
    bad_habits = db.query(Habit).filter(Habit.user_id == current_user.id, Habit.habit_type == HabitTypeEnum.bad, Habit.deleted_at.is_(None)).count()
    total_habit_completions = db.query(func.coalesce(func.sum(Habit.total_completions), 0)).filter(Habit.user_id == current_user.id, Habit.deleted_at.is_(None)).scalar() or 0

    total_wallets = db.query(Wallet).filter(Wallet.user_id == current_user.id, Wallet.deleted_at.is_(None)).count()
    total_balance = to_money(db.query(func.sum(Wallet.balance)).filter(Wallet.user_id == current_user.id, Wallet.deleted_at.is_(None)).scalar())
    total_income_month = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.income,
        Transaction.transaction_date >= month_start,
        Transaction.transaction_date < month_end,
        Transaction.deleted_at.is_(None),
    ).scalar())
    total_expense_month = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.transaction_date >= month_start,
        Transaction.transaction_date < month_end,
        Transaction.deleted_at.is_(None),
    ).scalar())

    weekly_due_task_rate = task_due_rate(db, current_user.id, week_start, week_end, completed_cutoff=now)
    completed_this_week = task_completed_count(db, current_user.id, week_start, min(now, week_end))
    created_this_week = task_created_count(db, current_user.id, week_start, min(now, week_end))
    overdue_tasks = task_overdue_count(db, current_user.id, now)
    due_today_tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.is_completed.is_(False),
        Task.due_date.isnot(None),
        Task.due_date >= today_start,
        Task.due_date < today_end,
        Task.deleted_at.is_(None),
    ).count()

    cashflow_start, _ = local_day_bounds_utc(current_user, local_today - timedelta(days=6))
    _, cashflow_end = local_day_bounds_utc(current_user, local_today)
    cashflow_rows = db.query(
        Transaction.transaction_date,
        Transaction.type,
        Transaction.amount,
    ).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.transaction_date >= cashflow_start,
        Transaction.transaction_date < cashflow_end,
        Transaction.deleted_at.is_(None),
    ).all()

    cashflow_map = {
        (local_today - timedelta(days=i)).isoformat(): {"income": 0.0, "expense": 0.0}
        for i in range(6, -1, -1)
    }
    for tx in cashflow_rows:
        day = local_date_for_user(current_user, tx.transaction_date).isoformat()
        if day not in cashflow_map:
            continue
        key = "income" if tx.type == TransactionTypeEnum.income else "expense"
        cashflow_map[day][key] += float(tx.amount or 0.0)
    weekly_cashflow = [
        {"date": day, **cashflow_map[day]}
        for day in sorted(cashflow_map.keys())
    ]

    financial_breakdown_map = {
        category: {"category": category, "income": 0.0, "expense": 0.0}
        for category in FINANCIAL_CATEGORIES
    }
    category_rows = db.query(
        Transaction.category,
        Transaction.type,
        func.coalesce(func.sum(Transaction.amount), 0.0).label("total"),
    ).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.transaction_date >= month_start,
        Transaction.transaction_date < month_end,
        Transaction.deleted_at.is_(None),
    ).group_by(Transaction.category, Transaction.type).all()
    for row in category_rows:
        category = normalize_financial_category(row.category)
        key = "income" if row.type == TransactionTypeEnum.income else "expense"
        financial_breakdown_map[category][key] += float(row.total or 0.0)
    financial_breakdown = list(financial_breakdown_map.values())

    current_income = finance_total(db, current_user.id, TransactionTypeEnum.income, week_start, now)
    previous_income = finance_total(db, current_user.id, TransactionTypeEnum.income, previous_week_start, same_span_previous_week_end)
    current_expense = finance_total(db, current_user.id, TransactionTypeEnum.expense, week_start, now)
    previous_expense = finance_total(db, current_user.id, TransactionTypeEnum.expense, previous_week_start, same_span_previous_week_end)

    current_task_rate = task_due_rate(db, current_user.id, week_start, min(now, week_end), completed_cutoff=now)
    previous_task_rate = task_due_rate(
        db,
        current_user.id,
        previous_week_start,
        same_span_previous_week_end,
        completed_cutoff=same_span_previous_week_end,
    )
    current_week_task_rate = weekly_due_task_rate
    previous_week_task_rate = task_due_rate(db, current_user.id, previous_week_start, previous_week_end, completed_cutoff=previous_week_end)
    monthly_task_rate = task_due_rate(db, current_user.id, last_30_days_start, now, completed_cutoff=now)
    local_week_start = local_today - timedelta(days=local_today.weekday())
    daily_task_trend = []
    for day_index in range(7):
        local_day = local_week_start + timedelta(days=day_index)
        day_start, day_end = local_day_bounds_utc(current_user, local_day)
        day_rate = task_due_rate(db, current_user.id, day_start, day_end, completed_cutoff=min(now, day_end))
        daily_task_trend.append({
            "date": local_day.isoformat(),
            "total": day_rate["total"],
            "completed": day_rate["completed"],
            "completion_rate": day_rate["completion_rate"],
        })
    weekly_comparison = [
        {
            "label": "Income",
            "current": current_income,
            "previous": previous_income,
            "change": pct_change(current_income, previous_income),
        },
        {
            "label": "Expense",
            "current": current_expense,
            "previous": previous_expense,
            "change": pct_change(current_expense, previous_expense),
        },
        {
            "label": "Task Due Completion",
            "current": current_task_rate["completion_rate"],
            "previous": previous_task_rate["completion_rate"],
            "change": round(current_task_rate["completion_rate"] - previous_task_rate["completion_rate"], 2),
        },
    ]
    task_completion_rates = [
        {"period": "Due This Week", **current_week_task_rate},
        {"period": "Due Last Week", **previous_week_task_rate},
        {"period": "Due Last 30 Days", **monthly_task_rate},
    ]

    numeric_goals = db.query(Goal).filter(
        Goal.user_id == current_user.id,
        Goal.deleted_at.is_(None),
    ).order_by(Goal.is_completed.asc(), Goal.target_date.asc().nullslast(), Goal.created_at.asc()).limit(6).all()
    numeric_goal_progress = [
        {
            "id": goal.id,
            "title": goal.title,
            "target_value": float(goal.target_value or 100.0),
            "current_value": float(goal.current_value or 0.0),
            "target_unit": goal.target_unit,
            "progress_mode": goal.progress_mode or "manual",
            "progress_rate": dashboard_goal_progress(goal),
        }
        for goal in numeric_goals
    ]

    time_allocation = []

    upcoming_goals = db.query(Goal).filter(
        Goal.user_id == current_user.id,
        Goal.is_completed.is_(False),
        Goal.target_date.isnot(None),
        Goal.target_date >= now,
        Goal.target_date <= deadline_limit,
        Goal.deleted_at.is_(None),
    ).order_by(Goal.target_date.asc()).limit(8).all()
    upcoming_deadlines = [
        {
            "id": goal.id,
            "type": "goal",
            "title": goal.title,
            "due_date": goal.target_date,
            "days_left": max(0, (local_date_for_user(current_user, goal.target_date) - local_today).days),
        }
        for goal in upcoming_goals
    ]
    upcoming_tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.is_completed.is_(False),
        Task.due_date.isnot(None),
        Task.due_date >= now,
        Task.due_date <= deadline_limit,
        Task.deleted_at.is_(None),
    ).order_by(Task.due_date.asc()).limit(8).all()
    upcoming_bills = db.query(BillReminder).filter(
        BillReminder.user_id == current_user.id,
        BillReminder.is_paid.is_(False),
        BillReminder.due_date >= now,
        BillReminder.due_date <= deadline_limit,
        BillReminder.deleted_at.is_(None),
    ).order_by(BillReminder.due_date.asc()).limit(8).all()
    habit_reminders = db.query(Habit).filter(
        Habit.user_id == current_user.id,
        Habit.reminder_time.isnot(None),
        Habit.deleted_at.is_(None),
    ).order_by(Habit.updated_at.desc()).limit(5).all()
    upcoming_deadlines.extend([
        {
            "id": task.id,
            "type": "task",
            "title": task.title,
            "due_date": task.due_date,
            "days_left": max(0, (local_date_for_user(current_user, task.due_date) - local_today).days),
        }
        for task in upcoming_tasks
    ])
    upcoming_deadlines.extend([
        {
            "id": bill.id,
            "type": "bill",
            "title": bill.title,
            "due_date": bill.due_date,
            "days_left": max(0, (local_date_for_user(current_user, bill.due_date) - local_today).days),
        }
        for bill in upcoming_bills
    ])
    upcoming_deadlines.extend([
        {
            "id": habit.id,
            "type": "habit",
            "title": f"Checklist habit: {habit.title}",
            "due_date": now,
            "days_left": 0,
        }
        for habit in habit_reminders
    ])
    upcoming_deadlines = sorted(upcoming_deadlines, key=lambda item: item["due_date"] or now)[:12]

    recent_transactions = db.query(Transaction).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.deleted_at.is_(None),
    ).order_by(Transaction.created_at.desc()).limit(8).all()
    recent_tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.is_completed.is_(True),
        Task.completed_at.isnot(None),
        Task.deleted_at.is_(None),
    ).order_by(Task.completed_at.desc()).limit(8).all()
    recent_habits = db.query(Habit).filter(
        Habit.user_id == current_user.id,
        Habit.last_logged_at.isnot(None),
        Habit.deleted_at.is_(None),
    ).order_by(Habit.last_logged_at.desc()).limit(8).all()
    recent_goals = db.query(Goal).filter(
        Goal.user_id == current_user.id,
        Goal.is_completed.is_(True),
        Goal.completed_at.isnot(None),
        Goal.deleted_at.is_(None),
    ).order_by(Goal.completed_at.desc()).limit(8).all()
    recent_activities = []
    for tx in recent_transactions:
        recent_activities.append({
            "id": str(tx.id),
            "type": "transaction",
            "title": "Income added" if tx.type == TransactionTypeEnum.income else "Expense recorded",
            "description": tx.category,
            "amount": float(tx.amount),
            "occurred_at": tx.transaction_date,
        })
    for task in recent_tasks:
        recent_activities.append({
            "id": str(task.id),
            "type": "task",
            "title": "Task completed",
            "description": task.title,
            "amount": None,
            "occurred_at": task.completed_at,
        })
    for habit in recent_habits:
        recent_activities.append({
            "id": str(habit.id),
            "type": "habit",
            "title": "Habit checked",
            "description": habit.title,
            "amount": None,
            "occurred_at": habit.last_logged_at,
        })
    for goal in recent_goals:
        recent_activities.append({
            "id": str(goal.id),
            "type": "goal",
            "title": "Goal completed",
            "description": goal.title,
            "amount": None,
            "occurred_at": goal.completed_at,
        })
    recent_activities = sorted(
        recent_activities,
        key=lambda item: item["occurred_at"],
        reverse=True,
    )[:10]

    p_score = productivity_score(db, current_user.id, week_start, week_end)
    f_score = finance_score(db, current_user.id)
    life = round((p_score + f_score) / 2, 2)

    return {
        "level": current_user.level or 1,
        "xp_balance": current_user.xp_balance or 0,
        "total_xp_earned": current_user.total_xp_earned or 0,
        "coin_balance": current_user.coin_balance or 0,
        "xp_needed_for_next_level": GamificationService.xp_for_next_level(current_user.level or 1),
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "good_habits": good_habits,
        "bad_habits": bad_habits,
        "total_habit_completions": int(total_habit_completions),
        "total_wallets": total_wallets,
        "total_balance": float(total_balance),
        "total_income_month": float(total_income_month),
        "total_expense_month": float(total_expense_month),
        "weekly_task_metrics": {
            "total": weekly_due_task_rate["total"],
            "completed": weekly_due_task_rate["completed"],
            "completion_rate": weekly_due_task_rate["completion_rate"],
            "due_total": weekly_due_task_rate["total"],
            "due_completed": weekly_due_task_rate["completed"],
            "completed_this_week": completed_this_week,
            "created_this_week": created_this_week,
            "overdue": overdue_tasks,
            "due_today": due_today_tasks,
        },
        "weekly_cashflow": weekly_cashflow,
        "financial_breakdown": financial_breakdown,
        "weekly_comparison": weekly_comparison,
        "task_completion_rates": task_completion_rates,
        "daily_task_trend": daily_task_trend,
        "upcoming_deadlines": upcoming_deadlines,
        "recent_activities": recent_activities,
        "numeric_goal_progress": numeric_goal_progress,
        "time_allocation": time_allocation,
        "productivity_score": p_score,
        "finance_score": f_score,
        "life_score": life,
    }


@router.get("/productivity")
def get_productivity_analytics(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = []
    for difficulty in ["easy", "medium", "hard"]:
        total = db.query(Task).filter(Task.user_id == current_user.id, Task.difficulty == difficulty, Task.deleted_at.is_(None)).count()
        completed = db.query(Task).filter(Task.user_id == current_user.id, Task.difficulty == difficulty, Task.is_completed.is_(True), Task.deleted_at.is_(None)).count()
        result.append({"difficulty": difficulty, "total": total, "completed": completed})

    top_habits = db.query(Habit).filter(Habit.user_id == current_user.id, Habit.deleted_at.is_(None)).order_by(Habit.current_streak.desc()).limit(5).all()
    goals = db.query(Goal).filter(Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).all()
    goals_progress = []
    for goal in goals:
        sub_goals = [sg for sg in goal.sub_goals if sg.deleted_at is None]
        completed_subgoals = sum(1 for sg in sub_goals if sg.is_completed)
        progress = round((completed_subgoals / len(sub_goals) * 100), 1) if sub_goals else (100.0 if goal.is_completed else 0.0)
        goals_progress.append({
            "id": str(goal.id),
            "title": goal.title,
            "is_completed": goal.is_completed,
            "progress": progress,
            "sub_goals_total": len(sub_goals),
            "sub_goals_completed": completed_subgoals,
        })

    return {
        "task_completion_by_difficulty": result,
        "top_habits": [{"id": str(h.id), "title": h.title, "streak": h.current_streak, "type": h.habit_type.value} for h in top_habits],
        "goals_progress": goals_progress,
    }


@router.get("/finance")
def get_finance_analytics(period: str = "month", current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if period == "week":
        start_date, end_date = local_period_bounds_utc(current_user, "weekly")
    elif period == "year":
        start_date, end_date = local_period_bounds_utc(current_user, "yearly")
    else:
        start_date, end_date = local_period_bounds_utc(current_user, "monthly")
        period = "month"

    total_income = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.income,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).scalar())
    total_expense = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).scalar())
    expense_by_category_rows = db.query(Transaction.category, func.sum(Transaction.amount).label("total")).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).group_by(Transaction.category).all()
    cashflow_rows = db.query(
        func.date(Transaction.transaction_date).label("date"),
        func.coalesce(
            func.sum(case((Transaction.type == TransactionTypeEnum.income, Transaction.amount), else_=0.0)),
            0.0,
        ).label("income"),
        func.coalesce(
            func.sum(case((Transaction.type == TransactionTypeEnum.expense, Transaction.amount), else_=0.0)),
            0.0,
        ).label("expense"),
    ).join(Wallet).filter(
        Wallet.user_id == current_user.id,
        Wallet.deleted_at.is_(None),
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).group_by(func.date(Transaction.transaction_date)).order_by(func.date(Transaction.transaction_date)).all()

    return {
        "period": period,
        "total_income": float(total_income),
        "total_expense": float(total_expense),
        "net": float(total_income - total_expense),
        "expense_by_category": [{"category": row.category, "total": float(to_money(row.total))} for row in expense_by_category_rows],
        "daily_cashflow": [{"date": str(row.date), "income": float(row.income or 0), "expense": float(row.expense or 0)} for row in cashflow_rows],
    }
