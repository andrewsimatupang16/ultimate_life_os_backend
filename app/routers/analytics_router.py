from datetime import date, datetime, timedelta
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.gamification_service import GamificationService
from app.routers.productivity_router import reset_daily_tasks_for_today
from app.models import (
    ActivityLog,
    BillReminder,
    Budget,
    GamificationEvent,
    Goal,
    Habit,
    HabitLog,
    HabitTypeEnum,
    SubGoal,
    Task,
    TaskOccurrenceSkip,
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


def parse_task_recurrence_days(value: str | None) -> set[int]:
    if not value:
        return set()
    days: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            day = int(item)
        except ValueError:
            continue
        if 0 <= day <= 6:
            days.add(day)
    return days


def iter_dates(start_date: date, end_date: date):
    current = start_date
    while current < end_date:
        yield current
        current += timedelta(days=1)


def local_date_window_for_period(user: User, start_datetime: datetime, end_datetime: datetime) -> tuple[date, date]:
    if end_datetime <= start_datetime:
        local_start = local_date_for_user(user, start_datetime)
        return local_start, local_start

    local_start = local_date_for_user(user, start_datetime)
    local_end = local_date_for_user(user, end_datetime - timedelta(microseconds=1)) + timedelta(days=1)
    return local_start, local_end


def daily_task_runs_on_local_date(user: User, task: Task, target_date: date) -> bool:
    if not task.is_daily or task.deleted_at is not None:
        return False
    if task.sub_goal is not None:
        if task.sub_goal.deleted_at is not None or task.sub_goal.is_completed:
            return False
        if task.sub_goal.goal is not None and (task.sub_goal.goal.deleted_at is not None or task.sub_goal.goal.is_completed):
            return False

    created_date = local_date_for_user(user, task.created_at) if task.created_at is not None else target_date
    first_date = task.start_date or created_date
    if target_date < first_date:
        return False

    recurrence_days = parse_task_recurrence_days(task.recurrence_days)
    return not recurrence_days or target_date.weekday() in recurrence_days


def daily_task_expected_pairs(db: Session, user: User, start_datetime: datetime, end_datetime: datetime) -> set[tuple[object, date]]:
    local_start, local_end = local_date_window_for_period(user, start_datetime, end_datetime)
    if local_end <= local_start:
        return set()

    daily_tasks = db.query(Task).options(joinedload(Task.sub_goal).joinedload(SubGoal.goal)).filter(
        Task.user_id == user.id,
        Task.is_daily.is_(True),
        Task.deleted_at.is_(None),
    ).all()
    if not daily_tasks:
        return set()

    skipped_pairs = {
        (row.task_id, row.local_date)
        for row in db.query(TaskOccurrenceSkip.task_id, TaskOccurrenceSkip.local_date).filter(
            TaskOccurrenceSkip.user_id == user.id,
            TaskOccurrenceSkip.local_date >= local_start,
            TaskOccurrenceSkip.local_date < local_end,
            TaskOccurrenceSkip.deleted_at.is_(None),
        ).all()
    }

    expected: set[tuple[object, date]] = set()
    for task in daily_tasks:
        for local_day in iter_dates(local_start, local_end):
            pair = (task.id, local_day)
            if pair in skipped_pairs:
                continue
            if daily_task_runs_on_local_date(user, task, local_day):
                expected.add(pair)
    return expected


def daily_task_completed_pairs(
    db: Session,
    user: User,
    start_datetime: datetime,
    end_datetime: datetime,
    expected_pairs: set[tuple[object, date]] | None = None,
    completed_cutoff: datetime | None = None,
) -> set[tuple[object, date]]:
    if expected_pairs is None:
        expected_pairs = daily_task_expected_pairs(db, user, start_datetime, end_datetime)
    if not expected_pairs:
        return set()

    local_start, local_end = local_date_window_for_period(user, start_datetime, end_datetime)
    task_ids = list({task_id for task_id, _ in expected_pairs})

    event_query = db.query(GamificationEvent.source_id, GamificationEvent.event_date).filter(
        GamificationEvent.user_id == user.id,
        GamificationEvent.source_type == "task",
        GamificationEvent.event_type == "completion_reward",
        GamificationEvent.source_id.in_(task_ids),
        GamificationEvent.event_date >= local_start,
        GamificationEvent.event_date < local_end,
        GamificationEvent.deleted_at.is_(None),
    )
    if completed_cutoff is not None:
        event_query = event_query.filter(GamificationEvent.created_at < completed_cutoff)

    completed = {
        (row.source_id, row.event_date)
        for row in event_query.all()
        if row.event_date is not None
    }

    # Legacy fallback: older data may have a completed daily task without a
    # gamification event. This only adds the current completed_at occurrence
    # and never duplicates event-based history.
    legacy_query = db.query(Task.id, Task.completed_at).filter(
        Task.user_id == user.id,
        Task.id.in_(task_ids),
        Task.is_daily.is_(True),
        Task.is_completed.is_(True),
        Task.completed_at.isnot(None),
        Task.completed_at >= start_datetime,
        Task.completed_at < end_datetime,
        Task.deleted_at.is_(None),
    )
    if completed_cutoff is not None:
        legacy_query = legacy_query.filter(Task.completed_at < completed_cutoff)

    for task_id, completed_at in legacy_query.all():
        completed.add((task_id, local_date_for_user(user, completed_at)))

    return completed.intersection(expected_pairs)


def daily_task_period_metrics(
    db: Session,
    user: User,
    start_datetime: datetime,
    end_datetime: datetime,
    completed_cutoff: datetime | None = None,
) -> dict:
    expected = daily_task_expected_pairs(db, user, start_datetime, end_datetime)
    completed = daily_task_completed_pairs(db, user, start_datetime, end_datetime, expected, completed_cutoff)
    return {
        "total": len(expected),
        "completed": len(completed),
        "completion_rate": round((len(completed) / len(expected) * 100) if expected else 0.0, 2),
    }


def daily_task_missed_count(
    db: Session,
    user: User,
    start_datetime: datetime,
    end_datetime: datetime,
    completed_cutoff: datetime | None = None,
) -> int:
    expected = daily_task_expected_pairs(db, user, start_datetime, end_datetime)
    completed = daily_task_completed_pairs(db, user, start_datetime, end_datetime, expected, completed_cutoff)
    return max(0, len(expected) - len(completed))


def budget_effective_window(
    user: User,
    budget: Budget,
    default_start: datetime,
    default_end: datetime,
    now: datetime,
) -> tuple[datetime, datetime]:
    period = (budget.period or "monthly").lower()
    if period in {"daily", "weekly", "monthly"}:
        return local_period_bounds_utc(user, period, now)

    start_date = budget.start_date or default_start
    end_date = budget.end_date or default_end
    if end_date <= start_date:
        return default_start, default_end
    return start_date, end_date


def budget_spent_for_score(db: Session, user_id, budget: Budget, start_datetime: datetime, end_datetime: datetime) -> float:
    spent = db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.category == budget.category,
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.transaction_date >= start_datetime,
        Transaction.transaction_date < end_datetime,
        Transaction.deleted_at.is_(None),
    ).scalar()
    return float(to_money(spent))


def budget_usage_score(usage_rate: float) -> float:
    if usage_rate <= 0.80:
        return 100.0
    if usage_rate <= 1.0:
        return 80.0 + ((1.0 - usage_rate) / 0.20 * 20.0)
    return max(0.0, 80.0 - ((usage_rate - 1.0) * 200.0))


def build_time_allocation(db: Session, user_id, start_datetime: datetime, end_datetime: datetime) -> list[dict]:
    rows = db.query(
        ActivityLog.category,
        func.sum(func.coalesce(ActivityLog.duration_seconds, ActivityLog.duration_minutes * 60)).label("duration_seconds"),
    ).filter(
        ActivityLog.user_id == user_id,
        ActivityLog.activity_date >= start_datetime,
        ActivityLog.activity_date < end_datetime,
        ActivityLog.deleted_at.is_(None),
    ).group_by(ActivityLog.category).all()

    items = []
    total_seconds = 0
    for row in rows:
        seconds = int(row.duration_seconds or 0)
        if seconds <= 0:
            continue
        total_seconds += seconds
        items.append({"category": row.category or "Lainnya", "duration_seconds": seconds})

    if total_seconds <= 0:
        return []

    return [
        {
            "category": item["category"],
            "duration_minutes": round(item["duration_seconds"] / 60),
            "percentage": round((item["duration_seconds"] / total_seconds) * 100, 2),
        }
        for item in sorted(items, key=lambda x: x["duration_seconds"], reverse=True)
    ]


def task_due_rate(
    db: Session,
    user_id,
    start_date: datetime,
    end_date: datetime,
    completed_cutoff: datetime | None = None,
) -> dict:
    """Return task completion rate for a period.

    Non-daily tasks are counted by due_date. Daily/recurring tasks do not
    have one row per day, so they are counted as expected local-date
    occurrences and completion events are read from gamification history.
    This keeps dashboard ratios from becoming 5/0 when users complete daily
    tasks that have no due_date.
    """
    completed_cutoff = completed_cutoff or end_date
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()

    base_filters = (
        Task.user_id == user_id,
        Task.is_daily.is_(False),
        Task.due_date.isnot(None),
        Task.due_date >= start_date,
        Task.due_date < end_date,
        Task.deleted_at.is_(None),
    )
    due_total = db.query(Task).filter(*base_filters).count()
    due_completed = db.query(Task).filter(
        *base_filters,
        Task.is_completed.is_(True),
        # Legacy rows may have is_completed=True without completed_at.
        # Count them as completed rather than dropping historical data.
        or_(Task.completed_at.is_(None), Task.completed_at < completed_cutoff),
    ).count()

    daily_metrics = {"total": 0, "completed": 0}
    if user is not None:
        daily_metrics = daily_task_period_metrics(db, user, start_date, end_date, completed_cutoff)

    total = due_total + daily_metrics["total"]
    completed = due_completed + daily_metrics["completed"]
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
    non_daily_completed = db.query(Task).filter(
        Task.user_id == user_id,
        Task.is_daily.is_(False),
        Task.is_completed.is_(True),
        Task.completed_at.isnot(None),
        Task.completed_at >= start_date,
        Task.completed_at < end_date,
        Task.deleted_at.is_(None),
    ).count()

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if user is None:
        return non_daily_completed

    daily_completed = daily_task_period_metrics(db, user, start_date, end_date, completed_cutoff=end_date)["completed"]
    return non_daily_completed + daily_completed


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

    due_metrics = task_due_rate(db, user_id, period_start, period_end, completed_cutoff=min(now, period_end))
    due_tasks = due_metrics["total"]
    completed_due_tasks = due_metrics["completed"]
    completed_recent_tasks = task_completed_count(db, user_id, period_start, min(now, period_end))
    open_goal_count = db.query(Goal).filter(
        Goal.user_id == user_id,
        Goal.is_completed.is_(False),
        Goal.deleted_at.is_(None),
    ).count()
    overdue_tasks = task_overdue_count(db, user_id, now)
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if user is not None:
        today = local_date_for_user(user, now)
        today_start, _ = local_day_bounds_utc(user, today)
        overdue_tasks += daily_task_missed_count(db, user, period_start, min(today_start, period_end), completed_cutoff=today_start)

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


def finance_score(
    db: Session,
    user_id,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    current_user: User | None = None,
) -> float:
    now = utc_now()
    start_date = start_date or (now - timedelta(days=30))
    end_date = end_date or now

    user = current_user or db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()

    income = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.income,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).scalar())
    expense = to_money(db.query(func.sum(Transaction.amount)).join(Wallet).filter(
        Wallet.user_id == user_id,
        Wallet.deleted_at.is_(None),
        Transaction.type == TransactionTypeEnum.expense,
        Transaction.transaction_date >= start_date,
        Transaction.transaction_date < end_date,
        Transaction.deleted_at.is_(None),
    ).scalar())
    income = float(income)
    expense = float(expense)

    budget_rows = db.query(Budget).filter(
        Budget.user_id == user_id,
        Budget.deleted_at.is_(None),
    ).all()
    if budget_rows and user is not None:
        budget_scores = []
        for budget in budget_rows:
            if not budget.limit_amount or budget.limit_amount <= 0:
                continue
            budget_start, budget_end = budget_effective_window(user, budget, start_date, end_date, now)
            spent = budget_spent_for_score(db, user_id, budget, budget_start, budget_end)
            usage_rate = spent / float(budget.limit_amount)
            budget_scores.append(budget_usage_score(usage_rate))
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
    month_start, month_end = local_period_bounds_utc(current_user, "monthly", now)
    f_score = finance_score(db, current_user.id, month_start, month_end, current_user)
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
    reset_daily_tasks_for_today(db, current_user)
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
    overdue_tasks += daily_task_missed_count(db, current_user, week_start, today_start, completed_cutoff=today_start)
    today_task_rate = task_due_rate(db, current_user.id, today_start, today_end, completed_cutoff=now)
    due_today_tasks = max(0, today_task_rate["total"] - today_task_rate["completed"])

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

    time_allocation = build_time_allocation(db, current_user.id, week_start, min(now, week_end))

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
    month_start, month_end = local_period_bounds_utc(current_user, "monthly", now)
    f_score = finance_score(db, current_user.id, month_start, month_end, current_user)
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
