import enum
import uuid

from sqlalchemy import (
    Boolean,
    CHAR,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base
from app.utils.time import utc_now


class GUID(TypeDecorator):
    """
    UUID type yang bisa dipakai di PostgreSQL dan SQLite.
    PostgreSQL memakai UUID native, SQLite memakai CHAR(36).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# =============================================================================
# ENUM DEFINITIONS
# =============================================================================

class DifficultyEnum(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class PriorityEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class HabitTypeEnum(str, enum.Enum):
    good = "good"
    bad = "bad"


class WalletTypeEnum(str, enum.Enum):
    cash = "cash"
    bank = "bank"
    ewallet = "ewallet"


class TransactionTypeEnum(str, enum.Enum):
    income = "income"
    expense = "expense"


class CoinLedgerTypeEnum(str, enum.Enum):
    earned = "earned"
    spent = "spent"
    penalty = "penalty"


class ConnectionStatusEnum(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class ActivityCategoryEnum(str, enum.Enum):
    kerja = "kerja"
    belajar = "belajar"
    istirahat = "istirahat"
    olahraga = "olahraga"
    lainnya = "lainnya"


# =============================================================================
# USER MODEL
# =============================================================================

class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    friend_code = Column(String(12), unique=True, index=True, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)

    level = Column(Integer, default=1)
    xp_balance = Column(Integer, default=0)
    total_xp_earned = Column(Integer, default=0)
    coin_balance = Column(Integer, default=0)
    active_title = Column(String, nullable=True)
    timezone = Column(String, default="Asia/Jakarta")
    task_current_streak = Column(Integer, default=0)
    task_best_streak = Column(Integer, default=0)
    task_last_completed_date = Column(Date, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    goals = relationship("Goal", back_populates="user", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="user", cascade="all, delete-orphan")
    habits = relationship("Habit", back_populates="user", cascade="all, delete-orphan")
    wallets = relationship("Wallet", back_populates="user", cascade="all, delete-orphan")
    budgets = relationship("Budget", back_populates="user", cascade="all, delete-orphan")
    finance_events = relationship("FinanceEvent", back_populates="user", cascade="all, delete-orphan")
    rewards = relationship("Reward", back_populates="user", cascade="all, delete-orphan")
    coin_ledger = relationship("CoinLedger", back_populates="user", cascade="all, delete-orphan")
    bill_reminders = relationship("BillReminder", back_populates="user", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="user", cascade="all, delete-orphan")
    habit_logs = relationship("HabitLog", back_populates="user", cascade="all, delete-orphan")
    time_sessions = relationship("TimeSession", back_populates="user", cascade="all, delete-orphan")
    gamification_events = relationship("GamificationEvent", back_populates="user", cascade="all, delete-orphan")
    achievements = relationship("UserAchievement", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")

    sent_requests = relationship(
        "AccountabilityConnection",
        foreign_keys="AccountabilityConnection.requester_id",
        back_populates="requester",
        cascade="all, delete-orphan",
    )
    received_requests = relationship(
        "AccountabilityConnection",
        foreign_keys="AccountabilityConnection.receiver_id",
        back_populates="receiver",
        cascade="all, delete-orphan",
    )


# =============================================================================
# GOAL MODEL
# =============================================================================

class Goal(Base):
    __tablename__ = "goals"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    target_date = Column(DateTime, nullable=True)
    target_value = Column(Float, nullable=True)
    current_value = Column(Float, default=0.0)
    target_unit = Column(String, nullable=True)
    progress_mode = Column(String, default="manual")
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    xp_rewarded = Column(Integer, default=0)
    coin_rewarded = Column(Integer, default=0)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="goals")
    sub_goals = relationship("SubGoal", back_populates="goal", cascade="all, delete-orphan")


# =============================================================================
# SUB GOAL MODEL
# =============================================================================

class SubGoal(Base):
    __tablename__ = "sub_goals"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    goal_id = Column(GUID(), ForeignKey("goals.id"), nullable=False)
    title = Column(String, nullable=False)
    weight = Column(Integer, default=1)
    target_value = Column(Float, nullable=True)
    current_value = Column(Float, default=0.0)
    progress_mode = Column(String, default="manual")
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    xp_rewarded = Column(Integer, default=0)
    coin_rewarded = Column(Integer, default=0)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    goal = relationship("Goal", back_populates="sub_goals")
    tasks = relationship("Task", back_populates="sub_goal", cascade="all, delete-orphan")
    history = relationship("KeyResultHistory", back_populates="key_result", cascade="all, delete-orphan")


# =============================================================================
# KEY RESULT HISTORY MODEL
# =============================================================================

class KeyResultHistory(Base):
    __tablename__ = "key_result_history"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    key_result_id = Column(GUID(), ForeignKey("sub_goals.id"), nullable=False)
    nilai_perubahan = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=utc_now)

    key_result = relationship("SubGoal", back_populates="history")


# =============================================================================
# TASK MODEL
# =============================================================================

class Task(Base):
    __tablename__ = "tasks"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    sub_goal_id = Column(GUID(), ForeignKey("sub_goals.id"), nullable=True)
    title = Column(String, nullable=False)
    difficulty = Column(Enum(DifficultyEnum, native_enum=False), nullable=False, default=DifficultyEnum.medium)
    priority = Column(Enum(PriorityEnum, native_enum=False), nullable=False, default=PriorityEnum.medium)
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)
    is_private = Column(Boolean, default=False)
    used_timer = Column(Boolean, default=False)
    is_daily = Column(Boolean, default=False)
    due_date = Column(DateTime, nullable=True)
    last_generated_date = Column(Date, nullable=True)
    xp_rewarded = Column(Integer, default=0)
    coin_rewarded = Column(Integer, default=0)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="tasks")
    sub_goal = relationship("SubGoal", back_populates="tasks")


# =============================================================================
# HABIT MODEL
# =============================================================================

class Habit(Base):
    __tablename__ = "habits"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    habit_type = Column(Enum(HabitTypeEnum, native_enum=False), nullable=False)
    current_streak = Column(Integer, default=0)
    best_streak = Column(Integer, default=0)
    total_completions = Column(Integer, default=0)
    last_logged_at = Column(DateTime, nullable=True)
    reminder_time = Column(String, nullable=True)
    xp_rewarded = Column(Integer, default=0)
    coin_rewarded = Column(Integer, default=0)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="habits")


# =============================================================================
# HABIT LOG MODEL
# =============================================================================

class HabitLog(Base):
    __tablename__ = "habit_logs"
    __table_args__ = (
        UniqueConstraint("habit_id", "local_date", name="uq_habit_logs_habit_local_date"),
        Index("ix_habit_logs_user_local_date", "user_id", "local_date"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    habit_id = Column(GUID(), ForeignKey("habits.id"), nullable=False)
    habit_type = Column(Enum(HabitTypeEnum, native_enum=False), nullable=False)
    local_date = Column(Date, nullable=False)
    logged_at = Column(DateTime, default=utc_now)
    xp_earned = Column(Integer, default=0)
    coin_earned = Column(Integer, default=0)
    penalty = Column(Integer, default=0)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="habit_logs")
    habit = relationship("Habit")


# =============================================================================
# WALLET MODEL
# =============================================================================

class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    balance = Column(Numeric(14, 2), default=0)
    wallet_type = Column(Enum(WalletTypeEnum, native_enum=False), nullable=False)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="wallets")
    transactions = relationship("Transaction", back_populates="wallet", cascade="all, delete-orphan")


# =============================================================================
# TRANSACTION MODEL
# =============================================================================

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        Index("ix_transactions_wallet_transaction_date", "wallet_id", "transaction_date"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    wallet_id = Column(GUID(), ForeignKey("wallets.id"), nullable=False)
    type = Column(Enum(TransactionTypeEnum, native_enum=False), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    category = Column(String, nullable=False)
    transaction_date = Column(DateTime, default=utc_now)
    description = Column(Text, nullable=True)
    is_private = Column(Boolean, default=False)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    wallet = relationship("Wallet", back_populates="transactions")


# =============================================================================
# FINANCE EVENT MODEL
# =============================================================================

class FinanceEvent(Base):
    __tablename__ = "finance_events"
    __table_args__ = (
        Index("ix_finance_events_user_created_at", "user_id", "created_at"),
        Index("ix_finance_events_wallet_created_at", "wallet_id", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    wallet_id = Column(GUID(), ForeignKey("wallets.id"), nullable=True)
    transaction_id = Column(GUID(), ForeignKey("transactions.id"), nullable=True)
    bill_id = Column(GUID(), ForeignKey("bill_reminders.id"), nullable=True)
    event_type = Column(String, nullable=False)
    amount_delta = Column(Numeric(14, 2), default=0)
    balance_after = Column(Numeric(14, 2), nullable=True)
    description = Column(String, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="finance_events")
    wallet = relationship("Wallet")
    transaction = relationship("Transaction")
    bill = relationship("BillReminder")


# =============================================================================
# BUDGET MODEL
# =============================================================================

class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        CheckConstraint("limit_amount > 0", name="ck_budgets_limit_positive"),
        CheckConstraint("current_spent >= 0", name="ck_budgets_current_spent_nonnegative"),
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="ck_budgets_valid_dates"),
        Index("ix_budgets_user_category_period", "user_id", "category", "period"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    category = Column(String, nullable=False)
    limit_amount = Column(Numeric(14, 2), nullable=False)
    current_spent = Column(Numeric(14, 2), default=0)
    period = Column(String, default="monthly")
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="budgets")


# =============================================================================
# BILL REMINDER MODEL
# =============================================================================

class BillReminder(Base):
    __tablename__ = "bill_reminders"
    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount >= 0", name="ck_bill_reminders_amount_nonnegative"),
        Index("ix_bill_reminders_user_due_date", "user_id", "due_date"),
        Index("ix_bill_reminders_paid_transaction_id", "paid_transaction_id"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    category = Column(String, nullable=False)
    amount = Column(Numeric(14, 2), nullable=True)
    due_date = Column(DateTime, nullable=False)
    is_paid = Column(Boolean, default=False)
    paid_at = Column(DateTime, nullable=True)
    paid_transaction_id = Column(GUID(), ForeignKey("transactions.id"), nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="bill_reminders")
    paid_transaction = relationship("Transaction")


# =============================================================================
# ACTIVITY LOG MODEL
# =============================================================================

class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    time_session_id = Column(GUID(), ForeignKey("time_sessions.id", ondelete="SET NULL"), nullable=True)
    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    duration_minutes = Column(Integer, nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    activity_date = Column(DateTime, default=utc_now)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_activity_logs_duration_positive"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds > 0", name="ck_activity_logs_duration_seconds_positive"),
        Index("ix_activity_logs_user_activity_date", "user_id", "activity_date"),
        Index(
            "uq_activity_logs_time_session_active",
            "time_session_id",
            unique=True,
            sqlite_where=deleted_at.is_(None),
            postgresql_where=deleted_at.is_(None),
        ),
    )

    user = relationship("User", back_populates="activity_logs")
    time_session = relationship("TimeSession", back_populates="activity_log")


# =============================================================================
# TIME SESSION MODEL
# =============================================================================

class TimeSession(Base):
    __tablename__ = "time_sessions"
    __table_args__ = (
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="ck_time_sessions_duration_nonnegative"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_time_sessions_valid_dates"),
        Index("ix_time_sessions_user_started_at", "user_id", "started_at"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    task_id = Column(GUID(), ForeignKey("tasks.id"), nullable=True)
    category = Column(String, nullable=False)
    title = Column(String, nullable=False)
    started_at = Column(DateTime, default=utc_now)
    ended_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    source = Column(String, default="timer")
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="time_sessions")
    task = relationship("Task")
    activity_log = relationship("ActivityLog", back_populates="time_session", uselist=False)


# =============================================================================
# COIN LEDGER MODEL
# =============================================================================

class CoinLedger(Base):
    __tablename__ = "coin_ledger"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    transaction_type = Column(Enum(CoinLedgerTypeEnum, native_enum=False), nullable=False)
    amount = Column(Integer, nullable=False)
    source_description = Column(String, nullable=False)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="coin_ledger")


# =============================================================================
# GAMIFICATION EVENT MODEL
# =============================================================================

class GamificationEvent(Base):
    __tablename__ = "gamification_events"
    __table_args__ = (
        UniqueConstraint("user_id", "event_key", name="uq_gamification_events_user_event_key"),
        Index("ix_gamification_events_user_created_at", "user_id", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    event_key = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    source_id = Column(GUID(), nullable=True)
    event_date = Column(Date, nullable=True)
    xp_delta = Column(Integer, default=0)
    coin_delta = Column(Integer, default=0)
    description = Column(String, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="gamification_events")


# =============================================================================
# USER ACHIEVEMENT MODEL
# =============================================================================

class UserAchievement(Base):
    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_key", name="uq_user_achievements_user_key"),
        Index("ix_user_achievements_user_awarded_at", "user_id", "awarded_at"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    achievement_key = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    icon = Column(String, nullable=True)
    awarded_at = Column(DateTime, default=utc_now)
    source_type = Column(String, nullable=True)
    source_id = Column(GUID(), nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="achievements")


# =============================================================================
# NOTIFICATION MODEL
# =============================================================================

class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe_key"),
        Index("ix_notifications_user_created_at", "user_id", "created_at"),
        Index("ix_notifications_user_read_at", "user_id", "read_at"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    notification_type = Column(String, nullable=False, default="general")
    channel = Column(String, nullable=False, default="in_app")
    dedupe_key = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)
    read_at = Column(DateTime, nullable=True)
    scheduled_for = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="notifications")


# =============================================================================
# REWARD SHOP MODEL
# =============================================================================

class Reward(Base):
    __tablename__ = "rewards"
    __table_args__ = (
        CheckConstraint("price > 0", name="ck_rewards_price_positive"),
    )

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Integer, nullable=False)
    icon = Column(String, nullable=True)
    times_purchased = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="rewards")


# =============================================================================
# ACCOUNTABILITY CONNECTION MODEL
# =============================================================================

class AccountabilityConnection(Base):
    __tablename__ = "accountability_connections"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    requester_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    receiver_id = Column(GUID(), ForeignKey("users.id"), nullable=False)
    status = Column(Enum(ConnectionStatusEnum, native_enum=False), default=ConnectionStatusEnum.pending)

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    deleted_at = Column(DateTime, nullable=True)

    requester = relationship("User", foreign_keys=[requester_id], back_populates="sent_requests")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="received_requests")
