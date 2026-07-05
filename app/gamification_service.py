from datetime import timedelta
from sqlalchemy.orm import Session
from app.models import (
    User, CoinLedger, CoinLedgerTypeEnum,
    DifficultyEnum, Task, Goal, SubGoal, Habit, HabitTypeEnum,
    GamificationEvent, HabitLog, UserAchievement
)
from app.utils.time import local_date_for_user, utc_now


# =============================================================================
# KONFIGURASI REWARD - BISA DIUBAH SESUAI KEBUTUHAN
# =============================================================================

class GamificationConfig:
    """Konfigurasi reward XP dan Coin untuk setiap kategori."""

    # Task rewards berdasarkan difficulty
    TASK_REWARDS = {
        DifficultyEnum.easy: {"xp": 10, "coins": 5},
        DifficultyEnum.medium: {"xp": 25, "coins": 15},
        DifficultyEnum.hard: {"xp": 50, "coins": 30},
    }
    TASK_ON_TIME_COIN_BONUS = {
        DifficultyEnum.easy: 2,
        DifficultyEnum.medium: 5,
        DifficultyEnum.hard: 10,
    }

    # Goal & SubGoal rewards
    SUBGOAL_REWARD = {"xp": 50, "coins": 25}
    GOAL_REWARD = {"xp": 100, "coins": 50}

    # Habit rewards
    GOOD_HABIT_DAILY = {"xp": 15, "coins": 10}
    GOOD_HABIT_STREAK_BONUS_MULTIPLIER = 0.1  # 10% bonus per streak

    # Bad habit penalty
    BAD_HABIT_BASE_PENALTY = 10
    BAD_HABIT_REPEAT_MULTIPLIER = 2
    BAD_HABIT_REPEAT_THRESHOLD = 3
    BAD_HABIT_REVIEW_WINDOW_DAYS = 7

    # Level formula
    XP_BASE = 100


# =============================================================================
# GAMIFICATION SERVICE
# =============================================================================

class GamificationService:
    ACHIEVEMENTS = {
        "first_task_completed": {
            "title": "First Task Completed",
            "description": "Menyelesaikan task pertama.",
            "icon": "check-circle",
        },
        "three_day_task_streak": {
            "title": "3-Day Task Streak",
            "description": "Menyelesaikan minimal satu task selama 3 hari berturut-turut.",
            "icon": "flame",
        },
        "seven_day_task_streak": {
            "title": "7-Day Focus Streak",
            "description": "Menyelesaikan minimal satu task selama 7 hari berturut-turut.",
            "icon": "flame",
        },
        "fourteen_day_task_streak": {
            "title": "14-Day Consistency",
            "description": "Menyelesaikan minimal satu task selama 14 hari berturut-turut.",
            "icon": "trophy",
        },
        "on_time_finisher": {
            "title": "On-Time Finisher",
            "description": "Menyelesaikan task sebelum atau tepat deadline.",
            "icon": "clock",
        },
        "hard_task_completed": {
            "title": "Hard Task Completed",
            "description": "Menyelesaikan task dengan tingkat kesulitan hard.",
            "icon": "zap",
        },
    }

    @classmethod
    def award_achievement(cls, db: Session, user: User, achievement_key: str, *, source_type: str | None = None, source_id=None) -> UserAchievement | None:
        achievement = cls.ACHIEVEMENTS.get(achievement_key)
        if not achievement:
            return None
        existing = db.query(UserAchievement).filter(
            UserAchievement.user_id == user.id,
            UserAchievement.achievement_key == achievement_key,
            UserAchievement.deleted_at.is_(None),
        ).first()
        if existing:
            return None
        row = UserAchievement(
            user_id=user.id,
            achievement_key=achievement_key,
            title=achievement["title"],
            description=achievement.get("description"),
            icon=achievement.get("icon"),
            source_type=source_type,
            source_id=source_id,
        )
        db.add(row)
        return row

    @classmethod
    def update_task_streak(cls, user: User, task: Task) -> None:
        if not task.completed_at:
            return
        completed_date = local_date_for_user(user, task.completed_at)
        if user.task_last_completed_date == completed_date:
            return
        if user.task_last_completed_date == completed_date - timedelta(days=1):
            user.task_current_streak = (user.task_current_streak or 0) + 1
        else:
            user.task_current_streak = 1
        user.task_best_streak = max(user.task_best_streak or 0, user.task_current_streak)
        user.task_last_completed_date = completed_date

    @staticmethod
    def event_key(event_type: str, source_type: str, source_id, event_date=None) -> str:
        date_part = event_date.isoformat() if event_date else "once"
        return f"{event_type}:{source_type}:{source_id}:{date_part}"

    @classmethod
    def record_event(
        cls,
        db: Session,
        user: User,
        *,
        event_type: str,
        source_type: str,
        source_id,
        xp_delta: int = 0,
        coin_delta: int = 0,
        event_date=None,
        description: str | None = None,
    ) -> GamificationEvent | None:
        key = cls.event_key(event_type, source_type, source_id, event_date)
        existing = db.query(GamificationEvent).filter(
            GamificationEvent.user_id == user.id,
            GamificationEvent.event_key == key,
            GamificationEvent.deleted_at.is_(None),
        ).first()
        if existing:
            return None

        event = GamificationEvent(
            user_id=user.id,
            event_key=key,
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            event_date=event_date,
            xp_delta=xp_delta,
            coin_delta=coin_delta,
            description=description,
        )
        db.add(event)
        return event

    @staticmethod
    def xp_for_next_level(level: int) -> int:
        """Hitung XP yang dibutuhkan untuk naik level: 100 * (level ^ 2)"""
        return GamificationConfig.XP_BASE * (level ** 2)

    @classmethod
    def add_xp_and_check_level_up(cls, db: Session, user: User, amount: int) -> dict:
        """
        Tambah XP ke user dan cek apakah naik level.
        Return dict dengan info perubahan.
        """
        if amount <= 0:
            return {"leveled_up": False, "levels_gained": 0, "old_level": user.level}

        old_level = user.level
        user.xp_balance = (user.xp_balance or 0) + amount
        user.total_xp_earned = (user.total_xp_earned or 0) + amount
        levels_gained = 0

        # Cek level up (bisa multiple level up sekaligus)
        while True:
            xp_needed = cls.xp_for_next_level(user.level)
            if user.xp_balance >= xp_needed:
                user.xp_balance -= xp_needed
                user.level += 1
                levels_gained += 1
            else:
                break

        return {
            "leveled_up": levels_gained > 0,
            "levels_gained": levels_gained,
            "old_level": old_level,
            "new_level": user.level,
        }

    @staticmethod
    def add_coins(db: Session, user: User, amount: int, description: str) -> None:
        """Tambah coin ke user dan catat di ledger."""
        if amount <= 0:
            return

        user.coin_balance = (user.coin_balance or 0) + amount

        ledger_entry = CoinLedger(
            user_id=user.id,
            transaction_type=CoinLedgerTypeEnum.earned,
            amount=amount,
            source_description=description
        )
        db.add(ledger_entry)

    @staticmethod
    def deduct_coins(db: Session, user: User, amount: int, description: str) -> bool:
        """
        Kurangi coin dari user dan catat di ledger.
        Return False jika coin tidak cukup.
        """
        if amount <= 0:
            return True

        if (user.coin_balance or 0) < amount:
            return False

        user.coin_balance = (user.coin_balance or 0) - amount

        ledger_entry = CoinLedger(
            user_id=user.id,
            transaction_type=CoinLedgerTypeEnum.spent,
            amount=amount,
            source_description=description
        )
        db.add(ledger_entry)
        return True

    @staticmethod
    def apply_penalty(db: Session, user: User, amount: int, description: str) -> None:
        """Aplikasikan penalty (pengurangan coin)."""
        if amount <= 0:
            return

        user.coin_balance = max(0, (user.coin_balance or 0) - amount)

        ledger_entry = CoinLedger(
            user_id=user.id,
            transaction_type=CoinLedgerTypeEnum.penalty,
            amount=amount,
            source_description=description
        )
        db.add(ledger_entry)

    # =========================================================================
    # TASK COMPLETION REWARD
    # =========================================================================

    @classmethod
    def task_on_time_coin_bonus(cls, task: Task) -> int:
        if not task.due_date or not task.completed_at:
            return 0
        if task.completed_at > task.due_date:
            return 0
        return GamificationConfig.TASK_ON_TIME_COIN_BONUS.get(task.difficulty, 0)

    @classmethod
    def reward_task_completion(cls, db: Session, user: User, task: Task) -> dict:
        """Berikan reward saat task diselesaikan."""
        if task.is_completed and (task.xp_rewarded or 0) == 0:
            rewards = GamificationConfig.TASK_REWARDS.get(task.difficulty, GamificationConfig.TASK_REWARDS[DifficultyEnum.medium])
            xp = rewards["xp"]
            coins = rewards["coins"]
            on_time_bonus = cls.task_on_time_coin_bonus(task)
            coins += on_time_bonus

            reward_date = local_date_for_user(user, task.completed_at) if task.is_daily else None
            event = cls.record_event(
                db,
                user,
                event_type="completion_reward",
                source_type="task",
                source_id=task.id,
                xp_delta=xp,
                coin_delta=coins,
                event_date=reward_date,
                description=f"Completed task: {task.title}",
            )
            if event is None:
                return {"xp_earned": 0, "coins_earned": 0, "leveled_up": False, "new_level": user.level, "levels_gained": 0}

            level_info = cls.add_xp_and_check_level_up(db, user, xp)
            coin_description = f"Completed task: {task.title}"
            if on_time_bonus > 0:
                coin_description += f" (+{on_time_bonus} on-time bonus)"
            cls.add_coins(db, user, coins, coin_description)
            cls.update_task_streak(user, task)
            cls.award_achievement(db, user, "first_task_completed", source_type="task", source_id=task.id)
            if user.task_current_streak >= 3:
                cls.award_achievement(db, user, "three_day_task_streak", source_type="task", source_id=task.id)
            if user.task_current_streak >= 7:
                cls.award_achievement(db, user, "seven_day_task_streak", source_type="task", source_id=task.id)
            if user.task_current_streak >= 14:
                cls.award_achievement(db, user, "fourteen_day_task_streak", source_type="task", source_id=task.id)
            if on_time_bonus > 0:
                cls.award_achievement(db, user, "on_time_finisher", source_type="task", source_id=task.id)
            if task.difficulty == DifficultyEnum.hard:
                cls.award_achievement(db, user, "hard_task_completed", source_type="task", source_id=task.id)

            task.xp_rewarded = xp
            task.coin_rewarded = coins
            task.completed_at = task.completed_at or utc_now()

            return {
                "xp_earned": xp,
                "coins_earned": coins,
                "leveled_up": level_info["leveled_up"],
                "new_level": level_info["new_level"],
                "levels_gained": level_info["levels_gained"],
            }
        return {"xp_earned": 0, "coins_earned": 0, "leveled_up": False, "new_level": user.level, "levels_gained": 0}

    # =========================================================================
    # GOAL & SUBGOAL COMPLETION REWARD
    # =========================================================================

    @classmethod
    def reward_subgoal_completion(cls, db: Session, user: User, subgoal: SubGoal) -> dict:
        """Berikan reward saat sub-goal diselesaikan."""
        if subgoal.is_completed and (subgoal.xp_rewarded or 0) == 0:
            xp = GamificationConfig.SUBGOAL_REWARD["xp"]
            coins = GamificationConfig.SUBGOAL_REWARD["coins"]

            event = cls.record_event(
                db,
                user,
                event_type="completion_reward",
                source_type="subgoal",
                source_id=subgoal.id,
                xp_delta=xp,
                coin_delta=coins,
                description=f"Completed sub-goal: {subgoal.title}",
            )
            if event is None:
                return {"xp_earned": 0, "coins_earned": 0, "leveled_up": False, "new_level": user.level}

            level_info = cls.add_xp_and_check_level_up(db, user, xp)
            cls.add_coins(db, user, coins, f"Completed sub-goal: {subgoal.title}")

            subgoal.xp_rewarded = xp
            subgoal.coin_rewarded = coins
            subgoal.completed_at = subgoal.completed_at or utc_now()

            return {
                "xp_earned": xp,
                "coins_earned": coins,
                "leveled_up": level_info["leveled_up"],
                "new_level": level_info["new_level"],
            }
        return {"xp_earned": 0, "coins_earned": 0, "leveled_up": False, "new_level": user.level}

    @classmethod
    def reward_goal_completion(cls, db: Session, user: User, goal: Goal) -> dict:
        """Berikan reward saat goal diselesaikan."""
        if goal.is_completed and (goal.xp_rewarded or 0) == 0:
            xp = GamificationConfig.GOAL_REWARD["xp"]
            coins = GamificationConfig.GOAL_REWARD["coins"]

            event = cls.record_event(
                db,
                user,
                event_type="completion_reward",
                source_type="goal",
                source_id=goal.id,
                xp_delta=xp,
                coin_delta=coins,
                description=f"Completed goal: {goal.title}",
            )
            if event is None:
                return {"xp_earned": 0, "coins_earned": 0, "leveled_up": False, "new_level": user.level}

            level_info = cls.add_xp_and_check_level_up(db, user, xp)
            cls.add_coins(db, user, coins, f"Completed goal: {goal.title}")

            goal.xp_rewarded = xp
            goal.coin_rewarded = coins
            goal.completed_at = goal.completed_at or utc_now()

            return {
                "xp_earned": xp,
                "coins_earned": coins,
                "leveled_up": level_info["leveled_up"],
                "new_level": level_info["new_level"],
            }
        return {"xp_earned": 0, "coins_earned": 0, "leveled_up": False, "new_level": user.level}

    # =========================================================================
    # HABIT LOGGING REWARD / PENALTY
    # =========================================================================

    @classmethod
    def log_good_habit(cls, db: Session, user: User, habit: Habit) -> dict:
        """Log good habit dan berikan reward."""
        now = utc_now()
        today = local_date_for_user(user, now)

        # Cek apakah sudah log hari ini
        existing_log = db.query(HabitLog).filter(
            HabitLog.habit_id == habit.id,
            HabitLog.local_date == today,
            HabitLog.deleted_at.is_(None),
        ).first()
        if existing_log:
            return {
                "success": False,
                "message": "Already logged today",
                "xp_earned": 0,
                "coins_earned": 0,
            }

        previous_date = local_date_for_user(user, habit.last_logged_at) if habit.last_logged_at else None
        habit.last_logged_at = now
        if previous_date == today - timedelta(days=1):
            habit.current_streak += 1
        else:
            habit.current_streak = 1
        habit.total_completions += 1
        if habit.current_streak > habit.best_streak:
            habit.best_streak = habit.current_streak

        # Hitung reward dengan streak bonus
        base_xp = GamificationConfig.GOOD_HABIT_DAILY["xp"]
        base_coins = GamificationConfig.GOOD_HABIT_DAILY["coins"]
        streak_bonus = 1 + (habit.current_streak * GamificationConfig.GOOD_HABIT_STREAK_BONUS_MULTIPLIER)

        xp = int(base_xp * streak_bonus)
        coins = int(base_coins * streak_bonus)

        event = cls.record_event(
            db,
            user,
            event_type="habit_log",
            source_type="habit",
            source_id=habit.id,
            xp_delta=xp,
            coin_delta=coins,
            event_date=today,
            description=f"Good habit: {habit.title}",
        )
        if event is None:
            return {
                "success": False,
                "message": "Already logged today",
                "xp_earned": 0,
                "coins_earned": 0,
            }

        level_info = cls.add_xp_and_check_level_up(db, user, xp)
        cls.add_coins(db, user, coins, f"Good habit: {habit.title} (streak: {habit.current_streak})")

        habit.xp_rewarded += xp
        habit.coin_rewarded += coins
        db.add(HabitLog(
            user_id=user.id,
            habit_id=habit.id,
            habit_type=habit.habit_type,
            local_date=today,
            logged_at=now,
            xp_earned=xp,
            coin_earned=coins,
            penalty=0,
        ))

        return {
            "success": True,
            "message": f"Good habit logged! Streak: {habit.current_streak}",
            "xp_earned": xp,
            "coins_earned": coins,
            "streak_bonus": streak_bonus,
            "new_streak": habit.current_streak,
            "leveled_up": level_info["leveled_up"],
            "new_level": level_info["new_level"],
        }


    @classmethod
    def calculate_bad_habit_penalty(cls, db: Session, user: User, habit: Habit, *, now=None) -> dict:
        """Return the next penalty for a bad habit from central gamification config."""
        reference_now = now or utc_now()
        today = local_date_for_user(user, reference_now)
        window_start = today - timedelta(days=GamificationConfig.BAD_HABIT_REVIEW_WINDOW_DAYS)

        recent_penalty_count = db.query(HabitLog).filter(
            HabitLog.user_id == user.id,
            HabitLog.habit_id == habit.id,
            HabitLog.local_date >= window_start,
            HabitLog.penalty > 0,
            HabitLog.deleted_at.is_(None),
        ).count()

        base_penalty = int(GamificationConfig.BAD_HABIT_BASE_PENALTY)
        multiplier_active = recent_penalty_count >= GamificationConfig.BAD_HABIT_REPEAT_THRESHOLD
        applied_multiplier = (
            GamificationConfig.BAD_HABIT_REPEAT_MULTIPLIER
            if multiplier_active
            else 1
        )
        final_penalty = int(base_penalty * applied_multiplier)

        return {
            "penalty": final_penalty,
            "base_penalty": base_penalty,
            "repeat_multiplier": float(GamificationConfig.BAD_HABIT_REPEAT_MULTIPLIER),
            "repeat_threshold": int(GamificationConfig.BAD_HABIT_REPEAT_THRESHOLD),
            "review_window_days": int(GamificationConfig.BAD_HABIT_REVIEW_WINDOW_DAYS),
            "recent_penalty_count": int(recent_penalty_count),
            "multiplier_active": bool(multiplier_active),
        }

    @classmethod
    def log_bad_habit(cls, db: Session, user: User, habit: Habit) -> dict:
        """Log bad habit dan aplikasikan penalty."""
        now = utc_now()
        today = local_date_for_user(user, now)

        # Cek apakah sudah log hari ini
        existing_log = db.query(HabitLog).filter(
            HabitLog.habit_id == habit.id,
            HabitLog.local_date == today,
            HabitLog.deleted_at.is_(None),
        ).first()
        if existing_log:
            return {
                "success": False,
                "message": "Already logged today",
                "penalty": 0,
            }

        penalty_info = cls.calculate_bad_habit_penalty(db, user, habit, now=now)
        final_penalty = penalty_info["penalty"]

        # Update habit
        habit.last_logged_at = now
        habit.current_streak += 1
        habit.total_completions += 1

        # Apply penalty
        cls.record_event(
            db,
            user,
            event_type="habit_penalty",
            source_type="habit",
            source_id=habit.id,
            xp_delta=0,
            coin_delta=-final_penalty,
            event_date=today,
            description=f"Bad habit: {habit.title}",
        )
        cls.apply_penalty(db, user, final_penalty, f"Bad habit: {habit.title}")
        db.add(HabitLog(
            user_id=user.id,
            habit_id=habit.id,
            habit_type=habit.habit_type,
            local_date=today,
            logged_at=now,
            xp_earned=0,
            coin_earned=0,
            penalty=final_penalty,
        ))

        return {
            "success": True,
            "message": f"Bad habit logged. Penalty: {final_penalty} coins",
            "penalty": final_penalty,
            "recent_penalty_count": penalty_info["recent_penalty_count"] + 1,
            "review_window_days": penalty_info["review_window_days"],
            "repeat_threshold": penalty_info["repeat_threshold"],
            "repeat_multiplier": penalty_info["repeat_multiplier"],
            "multiplier_active": penalty_info["multiplier_active"],
        }

    # =========================================================================
    # REWARD SHOP - PURCHASE
    # =========================================================================

    @classmethod
    def purchase_reward(cls, db: Session, user: User, reward) -> dict:
        """Beli reward dari shop. Return dict dengan hasil pembelian."""
        if not reward.is_active:
            return {"success": False, "message": "Reward is not active"}

        if (user.coin_balance or 0) < reward.price:
            return {
                "success": False,
                "message": f"Not enough coins. Need {reward.price}, have {user.coin_balance}",
            }

        # Deduct coins
        success = cls.deduct_coins(db, user, reward.price, f"Purchased reward: {reward.title}")
        if not success:
            return {"success": False, "message": "Failed to deduct coins"}

        # Update reward stats
        reward.times_purchased += 1

        return {
            "success": True,
            "message": f"Successfully purchased: {reward.title}!",
            "remaining_coins": user.coin_balance,
            "reward_title": reward.title,
        }
