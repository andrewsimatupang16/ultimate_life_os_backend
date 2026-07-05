from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.gamification_service import GamificationConfig, GamificationService
from app.models import DifficultyEnum, Reward, User
from app.schemas import GamificationConfigResponse, RewardCreate, RewardPurchaseResponse, RewardResponse, RewardUpdate
from app.utils.time import utc_now

router = APIRouter(prefix="/rewards", tags=["Reward Shop"])


@router.get("/config", response_model=GamificationConfigResponse)
def get_gamification_config():
    return GamificationConfigResponse(
        task_easy_xp=GamificationConfig.TASK_REWARDS[DifficultyEnum.easy]["xp"],
        task_easy_coins=GamificationConfig.TASK_REWARDS[DifficultyEnum.easy]["coins"],
        task_medium_xp=GamificationConfig.TASK_REWARDS[DifficultyEnum.medium]["xp"],
        task_medium_coins=GamificationConfig.TASK_REWARDS[DifficultyEnum.medium]["coins"],
        task_hard_xp=GamificationConfig.TASK_REWARDS[DifficultyEnum.hard]["xp"],
        task_hard_coins=GamificationConfig.TASK_REWARDS[DifficultyEnum.hard]["coins"],
        task_easy_on_time_bonus_coins=GamificationConfig.TASK_ON_TIME_COIN_BONUS[DifficultyEnum.easy],
        task_medium_on_time_bonus_coins=GamificationConfig.TASK_ON_TIME_COIN_BONUS[DifficultyEnum.medium],
        task_hard_on_time_bonus_coins=GamificationConfig.TASK_ON_TIME_COIN_BONUS[DifficultyEnum.hard],
        goal_complete_xp=GamificationConfig.GOAL_REWARD["xp"],
        goal_complete_coins=GamificationConfig.GOAL_REWARD["coins"],
        subgoal_complete_xp=GamificationConfig.SUBGOAL_REWARD["xp"],
        subgoal_complete_coins=GamificationConfig.SUBGOAL_REWARD["coins"],
        good_habit_daily_xp=GamificationConfig.GOOD_HABIT_DAILY["xp"],
        good_habit_daily_coins=GamificationConfig.GOOD_HABIT_DAILY["coins"],
        good_habit_streak_bonus_multiplier=GamificationConfig.GOOD_HABIT_STREAK_BONUS_MULTIPLIER,
        bad_habit_penalty_coins=GamificationConfig.BAD_HABIT_BASE_PENALTY,
        bad_habit_penalty_multiplier=GamificationConfig.BAD_HABIT_REPEAT_MULTIPLIER,
        bad_habit_penalty_threshold=GamificationConfig.BAD_HABIT_REPEAT_THRESHOLD,
        bad_habit_penalty_window_days=GamificationConfig.BAD_HABIT_REVIEW_WINDOW_DAYS,
        level_up_formula_base=GamificationConfig.XP_BASE,
    )


@router.get("/my", response_model=List[RewardResponse])
def get_my_rewards(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Reward).filter(Reward.user_id == current_user.id, Reward.deleted_at.is_(None)).order_by(Reward.created_at.desc()).all()


@router.post("/create", response_model=RewardResponse, status_code=status.HTTP_201_CREATED)
def create_reward(payload: RewardCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reward = Reward(user_id=current_user.id, title=payload.title, description=payload.description, price=payload.price, icon=payload.icon)
    db.add(reward)
    db.commit()
    db.refresh(reward)
    return reward


@router.put("/{reward_id}", response_model=RewardResponse)
def update_reward(reward_id: UUID, payload: RewardUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reward = db.query(Reward).filter(Reward.id == reward_id, Reward.user_id == current_user.id, Reward.deleted_at.is_(None)).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    if payload.title is not None:
        reward.title = payload.title
    if payload.description is not None:
        reward.description = payload.description
    if payload.price is not None:
        reward.price = payload.price
    if payload.icon is not None:
        reward.icon = payload.icon
    if payload.is_active is not None:
        reward.is_active = payload.is_active
    reward.updated_at = utc_now()
    db.commit()
    db.refresh(reward)
    return reward


@router.delete("/{reward_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reward(reward_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reward = db.query(Reward).filter(Reward.id == reward_id, Reward.user_id == current_user.id, Reward.deleted_at.is_(None)).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    now = utc_now()
    reward.deleted_at = now
    reward.updated_at = now
    db.commit()
    return None


@router.post("/{reward_id}/purchase", response_model=RewardPurchaseResponse)
def purchase_reward(reward_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reward = db.query(Reward).filter(Reward.id == reward_id, Reward.user_id == current_user.id, Reward.deleted_at.is_(None)).first()
    if not reward:
        raise HTTPException(status_code=404, detail="Reward not found")
    result = GamificationService.purchase_reward(db, current_user, reward)
    if not result.get("success"):
        return RewardPurchaseResponse(
            success=False,
            message=result.get("message", "Failed to purchase reward"),
            remaining_coins=current_user.coin_balance,
            reward_title=reward.title,
        )
    db.commit()
    db.refresh(current_user)
    return RewardPurchaseResponse(
        success=True,
        message=result.get("message", "Reward purchased"),
        remaining_coins=current_user.coin_balance,
        reward_title=reward.title,
    )
