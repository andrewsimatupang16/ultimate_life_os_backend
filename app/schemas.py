from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.utils.time import to_utc_naive



def strip_non_empty_string(value, field_name: str = "field"):
    """Trim user-facing text fields and reject blank strings.

    Empty categories/titles are a data-integrity problem because dashboards,
    budgets, and reminders group records by these labels.
    """
    if value is None:
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{field_name} must not be empty")
        return cleaned
    return value


def validate_period(value):
    if value is None:
        return value
    value = strip_non_empty_string(value, "period")
    if value not in {"daily", "weekly", "monthly", "custom"}:
        raise ValueError("period must be daily, weekly, monthly, or custom")
    return value


def strip_optional_string(value):
    """Trim optional text and normalize blank strings to None."""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return value


def normalize_email_value(value):
    if value is None:
        return value
    if isinstance(value, str):
        return value.strip().lower()
    return value


def validate_datetime_range(start_date: datetime | None, end_date: datetime | None, field_prefix: str = "Date range") -> None:
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError(f"{field_prefix} end_date must be after start_date")


def validate_budget_datetime_range(start_date: datetime | None, end_date: datetime | None) -> None:
    """Validate budget active windows.

    A budget range with identical start and end timestamps is not useful for
    spend aggregation and makes daily-limit calculations ambiguous.
    """
    if start_date is not None and end_date is not None and end_date <= start_date:
        raise ValueError("Budget end_date must be after start_date")


def validate_budget_limit_amount(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if value <= 0:
        raise ValueError("Budget limit_amount must be greater than 0")
    # Values below one cent pass Decimal(gt=0) but become 0.00 when persisted
    # using the money quantization contract. Reject them explicitly.
    if value.quantize(Decimal("0.01")) <= 0:
        raise ValueError("Budget limit_amount must be at least 0.01")
    return value


def validate_reminder_time_value(value):
    value = strip_optional_string(value)
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("reminder_time must use HH:MM format")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("reminder_time must be a valid 24-hour time")
    return f"{hour:02d}:{minute:02d}"

from app.models import (
    ActivityCategoryEnum,
    CoinLedgerTypeEnum,
    ConnectionStatusEnum,
    DifficultyEnum,
    HabitTypeEnum,
    PriorityEnum,
    TransactionTypeEnum,
    WalletTypeEnum,
)


class DateTimeInputSchema(BaseModel):
    """Normalize inbound API datetimes to the backend storage contract.

    The database currently stores UTC datetimes as naive values. This mixin
    keeps every request payload consistent before routers persist values.
    """

    @field_validator("*", mode="after")
    @classmethod
    def normalize_datetime_fields(cls, value):
        if isinstance(value, datetime):
            return to_utc_naive(value)
        return value


class BaseSchema(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# AUTH / USER
# =============================================================================

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    full_name: Optional[str] = None

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return normalize_email_value(value)

    @field_validator("full_name", mode="before")
    @classmethod
    def normalize_full_name(cls, value):
        return strip_optional_string(value)


class UserLogin(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return normalize_email_value(value)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    active_title: Optional[str] = None
    timezone: Optional[str] = None
    password: Optional[str] = Field(None, min_length=6)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return normalize_email_value(value)

    @field_validator("full_name", "avatar_url", "active_title", "timezone", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return strip_optional_string(value)


class UserResponse(BaseSchema):
    email: EmailStr
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    friend_code: Optional[str] = None
    level: int = 1
    xp_balance: int = 0
    total_xp_earned: int = 0
    coin_balance: int = 0
    active_title: Optional[str] = None
    timezone: str = "Asia/Jakarta"
    task_current_streak: int = 0
    task_best_streak: int = 0
    task_last_completed_date: Optional[date] = None


class UserPublicProfile(BaseModel):
    id: UUID
    full_name: Optional[str] = None
    avatar_url: Optional[str] = None
    level: int
    xp_balance: int
    total_xp_earned: int
    coin_balance: int
    active_title: Optional[str] = None
    friend_code: Optional[str] = None
    timezone: str = "Asia/Jakarta"
    task_current_streak: int = 0
    task_best_streak: int = 0

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# PRODUCTIVITY
# =============================================================================

class GoalCreate(DateTimeInputSchema):
    title: str
    description: Optional[str] = None
    target_date: Optional[datetime] = None
    target_value: Optional[float] = Field(None, gt=0)
    current_value: Optional[float] = Field(0.0, ge=0)
    target_unit: Optional[str] = None
    progress_mode: str = "manual"

    @field_validator("title", "progress_mode", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        return strip_non_empty_string(value, "text field")

    @field_validator("description", "target_unit", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return strip_optional_string(value)


class GoalUpdate(DateTimeInputSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    target_date: Optional[datetime] = None
    target_value: Optional[float] = Field(None, gt=0)
    current_value: Optional[float] = Field(None, ge=0)
    target_unit: Optional[str] = None
    progress_mode: Optional[str] = None
    is_completed: Optional[bool] = None

    @field_validator("title", "progress_mode", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        return strip_non_empty_string(value, "text field")

    @field_validator("description", "target_unit", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return strip_optional_string(value)


class GoalResponse(BaseSchema):
    user_id: UUID
    title: str
    description: Optional[str] = None
    target_date: Optional[datetime] = None
    target_value: Optional[float] = None
    current_value: float = 0.0
    target_unit: Optional[str] = None
    progress_mode: str = "manual"
    progress_rate: float = 0.0
    status: str = "In Progress"
    is_completed: bool = False
    completed_at: Optional[datetime] = None
    xp_rewarded: int = 0
    coin_rewarded: int = 0


class SubGoalCreate(BaseModel):
    goal_id: UUID
    title: str
    weight: int = Field(1, ge=1, le=5)
    target_value: Optional[float] = Field(None, gt=0)
    current_value: Optional[float] = Field(0.0, ge=0)
    progress_mode: str = "manual"

    @field_validator("title", "progress_mode", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        return strip_non_empty_string(value, "text field")


class SubGoalUpdate(BaseModel):
    title: Optional[str] = None
    weight: Optional[int] = Field(None, ge=1, le=5)
    target_value: Optional[float] = Field(None, gt=0)
    current_value: Optional[float] = Field(None, ge=0)
    progress_mode: Optional[str] = None
    is_completed: Optional[bool] = None

    @field_validator("title", "progress_mode", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        return strip_non_empty_string(value, "text field")


class SubGoalResponse(BaseSchema):
    goal_id: UUID
    title: str
    weight: int = 1
    target_value: Optional[float] = None
    current_value: float = 0.0
    progress_mode: str = "manual"
    progress_rate: float = 0.0
    is_locked: bool = False
    is_completed: bool = False
    completed_at: Optional[datetime] = None
    xp_rewarded: int = 0
    coin_rewarded: int = 0


class KeyResultHistoryResponse(BaseModel):
    id: UUID
    key_result_id: UUID
    nilai_perubahan: float
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)


class TaskCreate(DateTimeInputSchema):
    title: str
    difficulty: DifficultyEnum = DifficultyEnum.medium
    priority: PriorityEnum = PriorityEnum.medium
    sub_goal_id: Optional[UUID] = None
    is_private: bool = False
    is_daily: bool = False
    due_date: Optional[datetime] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return strip_non_empty_string(value, "title")


class TaskUpdate(DateTimeInputSchema):
    title: Optional[str] = None
    difficulty: Optional[DifficultyEnum] = None
    priority: Optional[PriorityEnum] = None
    is_completed: Optional[bool] = None
    is_private: Optional[bool] = None
    used_timer: Optional[bool] = None
    is_daily: Optional[bool] = None
    due_date: Optional[datetime] = None
    sub_goal_id: Optional[UUID] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return strip_non_empty_string(value, "title")


class TaskResponse(BaseSchema):
    user_id: UUID
    sub_goal_id: Optional[UUID] = None
    title: str
    difficulty: DifficultyEnum
    priority: PriorityEnum
    is_completed: bool = False
    completed_at: Optional[datetime] = None
    is_private: bool = False
    used_timer: bool = False
    is_daily: bool = False
    due_date: Optional[datetime] = None
    last_generated_date: Optional[date] = None
    xp_rewarded: int = 0
    coin_rewarded: int = 0


class HabitCreate(BaseModel):
    title: str
    habit_type: HabitTypeEnum
    reminder_time: Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return strip_non_empty_string(value, "title")

    @field_validator("reminder_time", mode="before")
    @classmethod
    def normalize_reminder_time(cls, value):
        return validate_reminder_time_value(value)


class HabitUpdate(BaseModel):
    title: Optional[str] = None
    habit_type: Optional[HabitTypeEnum] = None
    reminder_time: Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return strip_non_empty_string(value, "title")

    @field_validator("reminder_time", mode="before")
    @classmethod
    def normalize_reminder_time(cls, value):
        return validate_reminder_time_value(value)


class HabitResponse(BaseSchema):
    user_id: UUID
    title: str
    habit_type: HabitTypeEnum
    current_streak: int = 0
    best_streak: int = 0
    total_completions: int = 0
    last_logged_at: Optional[datetime] = None
    reminder_time: Optional[str] = None
    xp_rewarded: int = 0
    coin_rewarded: int = 0
    logged_today: bool = False
    bad_habit_penalty_preview: int = 0
    bad_habit_base_penalty: int = 0
    bad_habit_penalty_multiplier: float = 1.0
    bad_habit_penalty_threshold: int = 0
    bad_habit_penalty_window_days: int = 0
    bad_habit_recent_penalty_count: int = 0
    bad_habit_penalty_multiplier_active: bool = False


class HabitLogResponse(BaseModel):
    success: bool
    message: str
    xp_earned: int = 0
    coins_earned: int = 0
    penalty: int = 0
    new_streak: int = 0
    new_balance: dict = Field(default_factory=dict)


class HabitHistoryItem(BaseSchema):
    user_id: UUID
    habit_id: UUID
    habit_type: HabitTypeEnum
    local_date: date
    logged_at: datetime
    xp_earned: int = 0
    coin_earned: int = 0
    penalty: int = 0
    notes: Optional[str] = None


class CompletionRewardResponse(BaseModel):
    success: bool
    message: str
    xp_earned: int = 0
    coins_earned: int = 0
    penalty: int = 0
    new_level: int
    new_xp: int
    new_coins: int
    xp_needed_for_next_level: int


# =============================================================================
# FINANCE
# =============================================================================

class WalletCreate(BaseModel):
    name: str
    balance: Decimal = Field(Decimal("0.00"), ge=0)
    wallet_type: WalletTypeEnum

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return strip_non_empty_string(value, "name")


class WalletUpdate(BaseModel):
    name: Optional[str] = None
    wallet_type: Optional[WalletTypeEnum] = None
    balance: Optional[Decimal] = Field(None, ge=0)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return strip_non_empty_string(value, "name")


class WalletResponse(BaseSchema):
    user_id: UUID
    name: str
    balance: float
    wallet_type: WalletTypeEnum


class TransactionCreate(DateTimeInputSchema):
    wallet_id: UUID
    type: TransactionTypeEnum
    amount: Decimal = Field(..., gt=0)
    category: str
    transaction_date: Optional[datetime] = None
    description: Optional[str] = None
    is_private: bool = False

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value):
        return strip_non_empty_string(value, "category")


class TransactionUpdate(DateTimeInputSchema):
    wallet_id: Optional[UUID] = None
    type: Optional[TransactionTypeEnum] = None
    amount: Optional[Decimal] = Field(None, gt=0)
    category: Optional[str] = None
    transaction_date: Optional[datetime] = None
    description: Optional[str] = None
    is_private: Optional[bool] = None

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value):
        return strip_non_empty_string(value, "category")


class TransactionResponse(BaseSchema):
    wallet_id: UUID
    type: TransactionTypeEnum
    amount: float
    category: str
    transaction_date: datetime
    description: Optional[str] = None
    is_private: bool = False


class FinanceEventResponse(BaseSchema):
    user_id: UUID
    wallet_id: Optional[UUID] = None
    transaction_id: Optional[UUID] = None
    bill_id: Optional[UUID] = None
    event_type: str
    amount_delta: float = 0.0
    balance_after: Optional[float] = None
    description: Optional[str] = None


class BudgetCreate(DateTimeInputSchema):
    category: str
    limit_amount: Decimal = Field(..., gt=0)
    period: str = "monthly"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value):
        return strip_non_empty_string(value, "category")

    @field_validator("limit_amount")
    @classmethod
    def validate_limit_amount(cls, value):
        return validate_budget_limit_amount(value)

    @field_validator("period", mode="before")
    @classmethod
    def normalize_period(cls, value):
        return validate_period(value)

    @model_validator(mode="after")
    def validate_budget_window(self):
        validate_budget_datetime_range(self.start_date, self.end_date)
        if self.period == "custom" and (self.start_date is None or self.end_date is None):
            raise ValueError("Custom budget requires start_date and end_date")
        return self


class BudgetUpdate(DateTimeInputSchema):
    category: Optional[str] = None
    limit_amount: Optional[Decimal] = Field(None, gt=0)
    period: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value):
        return strip_non_empty_string(value, "category")

    @field_validator("limit_amount")
    @classmethod
    def validate_limit_amount(cls, value):
        return validate_budget_limit_amount(value)

    @field_validator("period", mode="before")
    @classmethod
    def normalize_period(cls, value):
        return validate_period(value)

    @model_validator(mode="after")
    def validate_budget_window(self):
        validate_budget_datetime_range(self.start_date, self.end_date)
        return self


class BudgetResponse(BaseSchema):
    user_id: UUID
    category: str
    limit_amount: float
    current_spent: float = 0.0
    period: str = "monthly"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class BillReminderCreate(DateTimeInputSchema):
    title: str
    category: str
    amount: Optional[Decimal] = Field(None, ge=0)
    due_date: datetime

    @field_validator("title", "category", mode="before")
    @classmethod
    def normalize_required_text(cls, value):
        return strip_non_empty_string(value, "text field")


class BillReminderUpdate(DateTimeInputSchema):
    title: Optional[str] = None
    category: Optional[str] = None
    amount: Optional[Decimal] = Field(None, ge=0)
    due_date: Optional[datetime] = None
    is_paid: Optional[bool] = None

    @field_validator("title", "category", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return strip_non_empty_string(value, "text field")


class BillReminderResponse(BaseSchema):
    user_id: UUID
    title: str
    category: str
    amount: Optional[float] = None
    due_date: datetime
    is_paid: bool = False
    paid_at: Optional[datetime] = None
    paid_transaction_id: Optional[UUID] = None


class ActivityLogCreate(DateTimeInputSchema):
    category: str
    title: str
    duration_minutes: Optional[int] = Field(None, gt=0)
    duration_seconds: Optional[int] = Field(None, gt=0)
    activity_date: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("category", "title", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return strip_non_empty_string(value, "text field")

    @model_validator(mode="after")
    def validate_duration(self):
        if self.duration_seconds is None and self.duration_minutes is None:
            raise ValueError("duration_seconds or duration_minutes is required")
        return self


class ActivityLogUpdate(DateTimeInputSchema):
    category: Optional[str] = None
    title: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, gt=0)
    duration_seconds: Optional[int] = Field(None, gt=0)
    activity_date: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("category", "title", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return strip_non_empty_string(value, "text field")


class ActivityLogResponse(BaseSchema):
    user_id: UUID
    time_session_id: Optional[UUID] = None
    category: str
    title: str
    duration_minutes: int
    duration_seconds: Optional[int] = None
    activity_date: datetime
    notes: Optional[str] = None


class TimeSessionCreate(DateTimeInputSchema):
    category: str
    title: str
    task_id: Optional[UUID] = None
    started_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("category", "title", mode="before")
    @classmethod
    def normalize_text(cls, value):
        return strip_non_empty_string(value, "text field")


class TimeSessionStop(DateTimeInputSchema):
    ended_at: Optional[datetime] = None
    create_activity_log: bool = True


class TimeSessionResponse(BaseSchema):
    user_id: UUID
    task_id: Optional[UUID] = None
    category: str
    title: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    source: str = "timer"
    notes: Optional[str] = None


class TimeSummaryCategoryResponse(BaseModel):
    category: str
    duration_minutes: int
    duration_seconds: int
    percentage: float


class TimeSummaryTrendPointResponse(BaseModel):
    date: date
    label: str
    duration_minutes: int
    duration_seconds: int


class TimeSummaryCategoryComparisonResponse(BaseModel):
    category: str
    current_duration_minutes: int
    current_duration_seconds: int
    previous_duration_minutes: int
    previous_duration_seconds: int
    change_seconds: int
    change_percent: float


class TimeSummaryComparisonResponse(BaseModel):
    previous_start_date: datetime
    previous_end_date: datetime
    current_total_minutes: int
    current_total_seconds: int
    previous_total_minutes: int
    previous_total_seconds: int
    change_seconds: int
    change_percent: float
    by_category: List[TimeSummaryCategoryComparisonResponse]


class TimeSummaryResponse(BaseModel):
    period: str
    start_date: datetime
    end_date: datetime
    total_seconds: int
    total_minutes: int
    total_hours: float
    log_count: int
    by_category: List[TimeSummaryCategoryResponse]
    daily_trend: List[TimeSummaryTrendPointResponse]
    comparison: TimeSummaryComparisonResponse


# =============================================================================
# COINS / REWARDS / PARTNER
# =============================================================================

class CoinLedgerResponse(BaseSchema):
    user_id: UUID
    transaction_type: CoinLedgerTypeEnum
    amount: int
    source_description: str


class GamificationEventResponse(BaseSchema):
    user_id: UUID
    event_key: str
    event_type: str
    source_type: str
    source_id: Optional[UUID] = None
    event_date: Optional[date] = None
    xp_delta: int = 0
    coin_delta: int = 0
    description: Optional[str] = None


class UserAchievementResponse(BaseSchema):
    user_id: UUID
    achievement_key: str
    title: str
    description: Optional[str] = None
    icon: Optional[str] = None
    awarded_at: datetime
    source_type: Optional[str] = None
    source_id: Optional[UUID] = None


class RewardCreate(BaseModel):
    title: str
    description: Optional[str] = None
    price: int = Field(..., gt=0)
    icon: Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return strip_non_empty_string(value, "title")

    @field_validator("description", "icon", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return strip_optional_string(value)


class RewardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[int] = Field(None, gt=0)
    icon: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value):
        return strip_non_empty_string(value, "title")

    @field_validator("description", "icon", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return strip_optional_string(value)


class RewardResponse(BaseSchema):
    user_id: UUID
    title: str
    description: Optional[str] = None
    price: int
    icon: Optional[str] = None
    times_purchased: int = 0
    is_active: bool = True


class RewardPurchaseResponse(BaseModel):
    success: bool
    message: str
    remaining_coins: int
    reward_title: str


class PartnerConsentRequest(BaseModel):
    consent_acknowledged: bool = Field(
        False,
        description="User explicitly understands that accepted partners can view shared dashboard data.",
    )


class PartnerSharingScopeItem(BaseModel):
    key: str
    label: str
    description: str


class PartnerSharingScopeResponse(BaseModel):
    consent_required: bool = True
    visibility_note: str
    shared_data: List[PartnerSharingScopeItem]


class AccountabilityConnectionResponse(BaseSchema):
    requester_id: UUID
    receiver_id: UUID
    status: ConnectionStatusEnum
    requester: Optional[UserPublicProfile] = None
    receiver: Optional[UserPublicProfile] = None


class GamificationConfigResponse(BaseModel):
    task_easy_xp: int = 10
    task_easy_coins: int = 5
    task_medium_xp: int = 25
    task_medium_coins: int = 15
    task_hard_xp: int = 50
    task_hard_coins: int = 30
    task_easy_on_time_bonus_coins: int = 2
    task_medium_on_time_bonus_coins: int = 5
    task_hard_on_time_bonus_coins: int = 10
    goal_complete_xp: int = 100
    goal_complete_coins: int = 50
    subgoal_complete_xp: int = 50
    subgoal_complete_coins: int = 25
    good_habit_daily_xp: int = 15
    good_habit_daily_coins: int = 10
    good_habit_streak_bonus_multiplier: float = 0.1
    bad_habit_penalty_coins: int = 10
    bad_habit_penalty_multiplier: float = 2.0
    bad_habit_penalty_threshold: int = 3
    bad_habit_penalty_window_days: int = 7
    level_up_formula_base: int = 100


# =============================================================================
# DASHBOARD
# =============================================================================

class WeeklyTaskMetrics(BaseModel):
    # Backward-compatible aliases used by the existing dashboard card.
    # From tahap 5 onward these represent tasks due in the user's local week,
    # not tasks created during the last seven days.
    total: int
    completed: int
    completion_rate: float

    # Explicit fields so the frontend can show the correct meaning without
    # overloading created_at-based task counters.
    due_total: int = 0
    due_completed: int = 0
    completed_this_week: int = 0
    created_this_week: int = 0
    overdue: int = 0
    due_today: int = 0


class WeeklyCashflowItem(BaseModel):
    date: str
    income: float
    expense: float


class FinancialCategoryBreakdownItem(BaseModel):
    category: str
    income: float
    expense: float


class WeeklyComparisonMetric(BaseModel):
    label: str
    current: float
    previous: float
    change: float


class TaskCompletionRateItem(BaseModel):
    period: str
    total: int
    completed: int
    completion_rate: float


class DailyTaskTrendItem(BaseModel):
    date: str
    total: int
    completed: int
    completion_rate: float


class UpcomingDeadlineItem(BaseModel):
    id: UUID
    type: str
    title: str
    due_date: datetime
    days_left: int


class RecentActivityItem(BaseModel):
    id: str
    type: str
    title: str
    description: Optional[str] = None
    amount: Optional[float] = None
    occurred_at: datetime


class NumericGoalProgressItem(BaseModel):
    id: UUID
    title: str
    target_value: float
    current_value: float
    target_unit: Optional[str] = None
    progress_mode: str
    progress_rate: float


class TimeAllocationItem(BaseModel):
    category: str
    duration_minutes: int
    percentage: float




class MonthlyFinanceSummaryItem(BaseModel):
    income: float
    expense: float
    net: float
    savings_rate: float
    score: float


class MonthlyProductivitySummaryItem(BaseModel):
    due_tasks: int
    completed_tasks: int
    completion_rate: float
    goals_completed: int
    habit_completions: int
    tracked_minutes: int
    score: float


class MonthlyComparisonItem(BaseModel):
    year_month: str
    label: str
    start_date: datetime
    end_date: datetime
    finance: MonthlyFinanceSummaryItem
    productivity: MonthlyProductivitySummaryItem


class MonthlyComparisonSummaryResponse(BaseModel):
    months: int
    generated_at: datetime
    items: List[MonthlyComparisonItem]


class DashboardSummaryResponse(BaseModel):
    is_fallback: bool = False
    warning: Optional[str] = None

    level: int
    xp_balance: int
    total_xp_earned: int
    coin_balance: int
    xp_needed_for_next_level: int

    total_goals: int
    completed_goals: int
    total_tasks: int
    completed_tasks: int
    good_habits: int
    bad_habits: int
    total_habit_completions: int

    total_wallets: int
    total_balance: float
    total_income_month: float
    total_expense_month: float

    weekly_task_metrics: WeeklyTaskMetrics
    weekly_cashflow: List[WeeklyCashflowItem]
    financial_breakdown: List[FinancialCategoryBreakdownItem]
    weekly_comparison: List[WeeklyComparisonMetric]
    task_completion_rates: List[TaskCompletionRateItem]
    daily_task_trend: List[DailyTaskTrendItem] = Field(default_factory=list)
    upcoming_deadlines: List[UpcomingDeadlineItem]
    recent_activities: List[RecentActivityItem]
    numeric_goal_progress: List[NumericGoalProgressItem]
    time_allocation: List[TimeAllocationItem]

    productivity_score: float
    finance_score: float
    life_score: float


# =============================================================================
# REMINDERS
# =============================================================================

class ReminderItem(BaseModel):
    id: UUID
    type: str
    title: str
    due_at: Optional[datetime] = None
    priority: str = "normal"
    message: str
    metadata: dict = Field(default_factory=dict)


class ReminderCenterResponse(BaseModel):
    today: List[ReminderItem] = Field(default_factory=list)
    tomorrow: List[ReminderItem] = Field(default_factory=list)
    overdue: List[ReminderItem] = Field(default_factory=list)
    habits: List[ReminderItem] = Field(default_factory=list)
    active_timers: List[ReminderItem] = Field(default_factory=list)
    total_count: int = 0


# =============================================================================
# NOTIFICATIONS / ADMIN
# =============================================================================

class NotificationResponse(BaseSchema):
    user_id: UUID
    title: str
    message: str
    notification_type: str = "general"
    channel: str = "in_app"
    dedupe_key: Optional[str] = None
    metadata_json: Optional[str] = None
    read_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    sent_at: Optional[datetime] = None


class NotificationUnreadCountResponse(BaseModel):
    unread_count: int


class AdminHealthResponse(BaseModel):
    status: str
    database: str
    scheduler_enabled: bool


class AdminMetricsResponse(BaseModel):
    users: int
    active_users: int
    tasks: int
    completed_tasks: int
    transactions: int
    notifications_unread: int
