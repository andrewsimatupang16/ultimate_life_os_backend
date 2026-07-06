from datetime import date
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.gamification_service import GamificationService
from app.models import Goal, Habit, HabitLog, HabitTypeEnum, KeyResultHistory, SubGoal, Task, TaskOccurrenceSkip, User
from app.schemas import (
    CompletionRewardResponse,
    GoalCreate,
    GoalResponse,
    GoalUpdate,
    HabitCreate,
    HabitHistoryItem,
    HabitLogDateRequest,
    HabitLogResponse,
    HabitResponse,
    HabitUpdate,
    KeyResultHistoryResponse,
    SubGoalCreate,
    SubGoalResponse,
    SubGoalUpdate,
    TaskCreate,
    TaskResponse,
    TaskUpdate,
)
from app.utils.time import local_date_for_user, utc_now

router = APIRouter(prefix="/productivity", tags=["Productivity"])


def reward_response(user: User, message: str, reward: dict | None = None, success: bool = True, penalty: int = 0) -> CompletionRewardResponse:
    reward = reward or {}
    return CompletionRewardResponse(
        success=success,
        message=message,
        xp_earned=int(reward.get("xp_earned", 0) or 0),
        coins_earned=int(reward.get("coins_earned", 0) or 0),
        penalty=penalty,
        new_level=user.level,
        new_xp=user.xp_balance,
        new_coins=user.coin_balance,
        xp_needed_for_next_level=GamificationService.xp_for_next_level(user.level),
    )


def merge_rewards(*rewards: dict | None) -> dict:
    merged = {"xp_earned": 0, "coins_earned": 0}
    for reward in rewards:
        if not reward:
            continue
        merged["xp_earned"] += int(reward.get("xp_earned", 0) or 0)
        merged["coins_earned"] += int(reward.get("coins_earned", 0) or 0)
    return merged


def subgoal_progress(subgoal: SubGoal) -> float:
    if subgoal.is_completed:
        return 100.0
    if subgoal.target_value and subgoal.target_value > 0:
        return round(min(100.0, ((subgoal.current_value or 0.0) / subgoal.target_value) * 100), 2)
    active_tasks = [task for task in subgoal.tasks if task.deleted_at is None]
    if active_tasks:
        completed_tasks = sum(1 for task in active_tasks if task.is_completed)
        return round((completed_tasks / len(active_tasks)) * 100, 2)
    return 0.0


def goal_weighted_progress(goal: Goal) -> float:
    active_subgoals = [subgoal for subgoal in goal.sub_goals if subgoal.deleted_at is None]
    if not active_subgoals:
        return 100.0 if goal.is_completed else 0.0
    total_weight = sum(max(1, min(5, subgoal.weight or 1)) for subgoal in active_subgoals)
    if total_weight <= 0:
        return 0.0
    weighted_sum = sum(subgoal_progress(subgoal) * max(1, min(5, subgoal.weight or 1)) for subgoal in active_subgoals)
    return round(weighted_sum / total_weight, 2)


def sync_subgoal_completion(db: Session, user: User, subgoal: SubGoal) -> dict | None:
    if not subgoal.is_completed and subgoal_progress(subgoal) >= 100.0:
        subgoal.is_completed = True
        subgoal.completed_at = utc_now()
        return GamificationService.reward_subgoal_completion(db, user, subgoal)
    return None


def sync_goal_completion(db: Session, user: User, goal: Goal) -> dict | None:
    progress = goal_weighted_progress(goal)
    if progress >= 100.0 and not goal.is_completed:
        rewards = []
        goal.is_completed = True
        goal.completed_at = utc_now()
        for subgoal in goal.sub_goals:
            if subgoal.deleted_at is None:
                if subgoal_progress(subgoal) >= 100.0:
                    subgoal.is_completed = True
                    subgoal.completed_at = subgoal.completed_at or utc_now()
                    rewards.append(GamificationService.reward_subgoal_completion(db, user, subgoal))
        rewards.append(GamificationService.reward_goal_completion(db, user, goal))
        return merge_rewards(*rewards)
    return None


def ensure_goal_unlocked(goal: Goal) -> None:
    if goal.is_completed:
        raise HTTPException(status_code=409, detail="Objective is completed and locked")


def ensure_subgoal_unlocked(subgoal: SubGoal) -> None:
    if subgoal.is_completed:
        raise HTTPException(status_code=409, detail="Sub-goal is completed and locked")
    ensure_goal_unlocked(subgoal.goal)


def serialize_recurrence_days(days: list[int] | None) -> str | None:
    if not days:
        return None
    normalized_days = sorted({int(day) for day in days if 0 <= int(day) <= 6})
    return ",".join(str(day) for day in normalized_days) if normalized_days else None


def parse_recurrence_days(value: str | None) -> set[int]:
    if not value:
        return set()
    return {int(day) for day in value.split(",") if day.strip()}


def task_has_started(task: Task, target_date) -> bool:
    return task.start_date is None or task.start_date <= target_date


def task_runs_on_date(task: Task, target_date) -> bool:
    if not task.is_daily:
        return True
    recurrence_days = parse_recurrence_days(task.recurrence_days)
    if not recurrence_days:
        return True
    return target_date.weekday() in recurrence_days


def reset_daily_task_for_today(user: User, task: Task) -> bool:
    if not task.is_daily:
        return False
    if task.sub_goal and (task.sub_goal.is_completed or task.sub_goal.goal.is_completed):
        return False

    today = local_date_for_user(user)
    if not task_has_started(task, today):
        return False
    if not task_runs_on_date(task, today):
        return False

    cycle_date = task.last_generated_date
    if cycle_date is None and task.completed_at is not None:
        cycle_date = local_date_for_user(user, task.completed_at)
        task.last_generated_date = cycle_date

    if cycle_date is None:
        task.last_generated_date = today
        return True

    if cycle_date < today:
        task.last_generated_date = today
        if task.is_completed:
            task.is_completed = False
            task.completed_at = None
            task.xp_rewarded = 0
            task.coin_rewarded = 0
            task.used_timer = False
        task.updated_at = utc_now()
        return True

    return False


def reset_daily_tasks_for_today(db: Session, user: User) -> None:
    tasks = db.query(Task).options(joinedload(Task.sub_goal).joinedload(SubGoal.goal)).filter(
        Task.user_id == user.id,
        Task.is_daily.is_(True),
        Task.deleted_at.is_(None),
    ).all()
    changed = False
    for task in tasks:
        changed = reset_daily_task_for_today(user, task) or changed
    if changed:
        db.commit()


def skipped_task_ids_for_today(db: Session, user: User) -> set[UUID]:
    today = local_date_for_user(user)
    return {
        row[0]
        for row in db.query(TaskOccurrenceSkip.task_id).filter(
            TaskOccurrenceSkip.user_id == user.id,
            TaskOccurrenceSkip.local_date == today,
            TaskOccurrenceSkip.deleted_at.is_(None),
        ).all()
    }


def task_is_skipped_today(db: Session, user: User, task: Task) -> bool:
    today = local_date_for_user(user)
    return db.query(TaskOccurrenceSkip.id).filter(
        TaskOccurrenceSkip.user_id == user.id,
        TaskOccurrenceSkip.task_id == task.id,
        TaskOccurrenceSkip.local_date == today,
        TaskOccurrenceSkip.deleted_at.is_(None),
    ).first() is not None


def skip_daily_task_for_today(db: Session, user: User, task: Task) -> None:
    today = local_date_for_user(user)
    existing_skip = db.query(TaskOccurrenceSkip).filter(
        TaskOccurrenceSkip.user_id == user.id,
        TaskOccurrenceSkip.task_id == task.id,
        TaskOccurrenceSkip.local_date == today,
    ).first()

    now = utc_now()
    if existing_skip:
        existing_skip.deleted_at = None
        existing_skip.skipped_at = now
        existing_skip.updated_at = now
        return

    db.add(TaskOccurrenceSkip(
        user_id=user.id,
        task_id=task.id,
        local_date=today,
        skipped_at=now,
    ))


def recalculate_daily_task_cycle_for_today(user: User, task: Task) -> None:
    if not task.is_daily:
        task.last_generated_date = None
        return

    today = local_date_for_user(user)
    if task_has_started(task, today) and task_runs_on_date(task, today):
        task.last_generated_date = task.last_generated_date or today
        return

    task.last_generated_date = None


def task_visible_on_date(task: Task, target_date, skipped_task_ids: set[UUID]) -> bool:
    if task.id in skipped_task_ids:
        return False
    if not task_has_started(task, target_date):
        return False
    return task_runs_on_date(task, target_date)


def filter_tasks_visible_today(tasks: list[Task], skipped_task_ids: set[UUID], today) -> list[Task]:
    target_date = today
    return [task for task in tasks if task_visible_on_date(task, target_date, skipped_task_ids)]


def goal_to_dict(goal: Goal, skipped_task_ids: set[UUID] | None = None, today=None) -> dict:
    data = GoalResponse.model_validate(goal).model_dump(mode="json")
    data["progress_rate"] = goal_weighted_progress(goal)
    data["status"] = "Completed" if goal.is_completed else "In Progress"
    data["sub_goals"] = []
    for sg in sorted([x for x in goal.sub_goals if x.deleted_at is None], key=lambda x: x.created_at):
        sg_data = SubGoalResponse.model_validate(sg).model_dump(mode="json")
        sg_data["progress_rate"] = subgoal_progress(sg)
        sg_data["is_locked"] = bool(goal.is_completed)
        target_date = today
        skipped_ids = skipped_task_ids or set()
        sg_data["tasks"] = [
            TaskResponse.model_validate(t).model_dump(mode="json")
            for t in sorted(
                [
                    task
                    for task in sg.tasks
                    if task.deleted_at is None
                    and (target_date is None or task_visible_on_date(task, target_date, skipped_ids))
                ],
                key=lambda task: task.created_at,
            )
        ]
        data["sub_goals"].append(sg_data)
    return data


def subgoal_to_dict(subgoal: SubGoal) -> dict:
    data = SubGoalResponse.model_validate(subgoal).model_dump(mode="json")
    data["progress_rate"] = subgoal_progress(subgoal)
    data["is_locked"] = bool(subgoal.goal.is_completed)
    return data


@router.get("/goals")
def get_goals(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reset_daily_tasks_for_today(db, current_user)
    goals = db.query(Goal).options(joinedload(Goal.sub_goals).joinedload(SubGoal.tasks)).filter(
        Goal.user_id == current_user.id,
        Goal.deleted_at.is_(None),
    ).order_by(Goal.is_completed.asc(), Goal.target_date.asc().nullslast(), Goal.created_at.asc()).all()
    skipped_task_ids = skipped_task_ids_for_today(db, current_user)
    today = local_date_for_user(current_user)
    return [goal_to_dict(g, skipped_task_ids, today) for g in goals]


@router.get("/goals/{goal_id}")
def get_goal(goal_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = db.query(Goal).options(joinedload(Goal.sub_goals).joinedload(SubGoal.tasks)).filter(
        Goal.id == goal_id,
        Goal.user_id == current_user.id,
        Goal.deleted_at.is_(None),
    ).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    skipped_task_ids = skipped_task_ids_for_today(db, current_user)
    today = local_date_for_user(current_user)
    return goal_to_dict(goal, skipped_task_ids, today)


@router.post("/goals", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
def create_goal(payload: GoalCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = Goal(
        user_id=current_user.id,
        title=payload.title,
        description=payload.description,
        target_date=payload.target_date,
        target_value=payload.target_value,
        current_value=payload.current_value or 0.0,
        target_unit=payload.target_unit,
        progress_mode=payload.progress_mode,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.put("/goals/{goal_id}", response_model=GoalResponse)
def update_goal(goal_id: UUID, payload: GoalUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    ensure_goal_unlocked(goal)
    sent_fields = payload.model_fields_set
    if payload.title is not None:
        goal.title = payload.title
    if "description" in sent_fields:
        goal.description = payload.description
    if "target_date" in sent_fields:
        goal.target_date = payload.target_date
    if "target_value" in sent_fields:
        goal.target_value = payload.target_value
    if payload.current_value is not None:
        goal.current_value = payload.current_value
    if "target_unit" in sent_fields:
        goal.target_unit = payload.target_unit
    if payload.progress_mode is not None:
        goal.progress_mode = payload.progress_mode
        if payload.progress_mode == "weighted_subgoals":
            goal.target_value = None
            goal.current_value = 0.0
            goal.target_unit = None
    if payload.is_completed is not None:
        was_completed = goal.is_completed
        goal.is_completed = payload.is_completed
        goal.completed_at = utc_now() if payload.is_completed else None
        if payload.is_completed and not was_completed:
            GamificationService.reward_goal_completion(db, current_user, goal)
    elif goal.target_value and goal.current_value >= goal.target_value and not goal.is_completed:
        goal.is_completed = True
        goal.completed_at = utc_now()
        GamificationService.reward_goal_completion(db, current_user, goal)
    goal.updated_at = utc_now()
    db.commit()
    if payload.is_completed:
        db.refresh(current_user)
    db.refresh(goal)
    return goal


@router.patch("/goals/{goal_id}/progress", response_model=GoalResponse)
def update_goal_progress(goal_id: UUID, payload: GoalUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    ensure_goal_unlocked(goal)
    if payload.current_value is None:
        raise HTTPException(status_code=400, detail="current_value is required")
    goal.current_value = payload.current_value
    if goal.target_value and goal.current_value >= goal.target_value and not goal.is_completed:
        goal.is_completed = True
        goal.completed_at = utc_now()
        GamificationService.reward_goal_completion(db, current_user, goal)
    goal.updated_at = utc_now()
    db.commit()
    db.refresh(current_user)
    db.refresh(goal)
    return goal


@router.post("/goals/{goal_id}/complete", response_model=CompletionRewardResponse)
def complete_goal(goal_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if goal.is_completed:
        if (goal.xp_rewarded or 0) == 0:
            reward = GamificationService.reward_goal_completion(db, current_user, goal)
            db.commit()
            db.refresh(current_user)
            return reward_response(current_user, "Goal reward fixed!", reward)
        return reward_response(current_user, "Goal already completed", success=False)
    goal.is_completed = True
    goal.completed_at = utc_now()
    reward = GamificationService.reward_goal_completion(db, current_user, goal)
    db.commit()
    db.refresh(current_user)
    return reward_response(current_user, "Goal completed!", reward)


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(goal_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    ensure_goal_unlocked(goal)
    now = utc_now()
    goal.deleted_at = now
    goal.updated_at = now
    for sg in goal.sub_goals:
        if sg.deleted_at is None:
            sg.deleted_at = now
            sg.updated_at = now
        for task in sg.tasks:
            if task.deleted_at is None:
                task.deleted_at = now
                task.updated_at = now
    db.commit()
    return None


@router.post("/subgoals", response_model=SubGoalResponse, status_code=status.HTTP_201_CREATED)
def create_subgoal(payload: SubGoalCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == payload.goal_id, Goal.user_id == current_user.id, Goal.deleted_at.is_(None)).first()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    ensure_goal_unlocked(goal)
    subgoal = SubGoal(
        goal_id=goal.id,
        title=payload.title,
        weight=payload.weight,
        target_value=payload.target_value,
        current_value=payload.current_value or 0.0,
        progress_mode=payload.progress_mode,
    )
    db.add(subgoal)
    db.commit()
    db.refresh(subgoal)
    return subgoal_to_dict(subgoal)


@router.put("/subgoals/{subgoal_id}", response_model=SubGoalResponse)
def update_subgoal(subgoal_id: UUID, payload: SubGoalUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    subgoal = db.query(SubGoal).join(Goal).filter(
        SubGoal.id == subgoal_id,
        Goal.user_id == current_user.id,
        SubGoal.deleted_at.is_(None),
    ).first()
    if not subgoal:
        raise HTTPException(status_code=404, detail="Sub-goal not found")
    ensure_subgoal_unlocked(subgoal)
    old_current_value = subgoal.current_value or 0.0
    sent_fields = payload.model_fields_set
    if payload.title is not None:
        subgoal.title = payload.title
    if payload.weight is not None:
        subgoal.weight = payload.weight
    if "target_value" in sent_fields:
        subgoal.target_value = payload.target_value
    if payload.progress_mode is not None:
        subgoal.progress_mode = payload.progress_mode
    if payload.current_value is not None:
        subgoal.current_value = payload.current_value
        if subgoal.progress_mode == "manual" and payload.current_value != old_current_value:
            db.add(KeyResultHistory(
                key_result_id=subgoal.id,
                nilai_perubahan=payload.current_value - old_current_value,
            ))
    if payload.is_completed is not None:
        subgoal.is_completed = payload.is_completed
        subgoal.completed_at = utc_now() if payload.is_completed else None
    if subgoal.target_value and (subgoal.current_value or 0.0) >= subgoal.target_value:
        subgoal.is_completed = True
        subgoal.completed_at = subgoal.completed_at or utc_now()
        GamificationService.reward_subgoal_completion(db, current_user, subgoal)
    elif payload.is_completed:
        GamificationService.reward_subgoal_completion(db, current_user, subgoal)
    subgoal.updated_at = utc_now()
    sync_goal_completion(db, current_user, subgoal.goal)
    db.commit()
    db.refresh(current_user)
    db.refresh(subgoal)
    return subgoal_to_dict(subgoal)


@router.post("/subgoals/{subgoal_id}/complete", response_model=CompletionRewardResponse)
def complete_subgoal(subgoal_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    subgoal = db.query(SubGoal).join(Goal).filter(
        SubGoal.id == subgoal_id,
        Goal.user_id == current_user.id,
        SubGoal.deleted_at.is_(None),
    ).first()
    if not subgoal:
        raise HTTPException(status_code=404, detail="Sub-goal not found")
    ensure_subgoal_unlocked(subgoal)
    if subgoal.is_completed:
        return reward_response(current_user, "Sub-goal already completed", success=False)
    subgoal.is_completed = True
    subgoal.completed_at = utc_now()
    reward = GamificationService.reward_subgoal_completion(db, current_user, subgoal)
    goal_reward = sync_goal_completion(db, current_user, subgoal.goal)
    db.commit()
    db.refresh(current_user)
    return reward_response(current_user, "Sub-goal completed!", merge_rewards(reward, goal_reward))


@router.delete("/subgoals/{subgoal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subgoal(subgoal_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    subgoal = db.query(SubGoal).join(Goal).filter(SubGoal.id == subgoal_id, Goal.user_id == current_user.id, SubGoal.deleted_at.is_(None)).first()
    if not subgoal:
        raise HTTPException(status_code=404, detail="Sub-goal not found")
    ensure_subgoal_unlocked(subgoal)
    now = utc_now()
    subgoal.deleted_at = now
    subgoal.updated_at = now
    for task in subgoal.tasks:
        if task.deleted_at is None:
            task.deleted_at = now
            task.updated_at = now
    db.commit()
    return None


@router.get("/subgoals/{subgoal_id}/history", response_model=List[KeyResultHistoryResponse])
def get_subgoal_history(subgoal_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    subgoal = db.query(SubGoal).join(Goal).filter(
        SubGoal.id == subgoal_id,
        Goal.user_id == current_user.id,
        SubGoal.deleted_at.is_(None),
    ).first()
    if not subgoal:
        raise HTTPException(status_code=404, detail="Sub-goal not found")
    return db.query(KeyResultHistory).filter(
        KeyResultHistory.key_result_id == subgoal.id,
    ).order_by(KeyResultHistory.timestamp.desc()).all()


@router.get("/tasks", response_model=List[TaskResponse])
def get_tasks(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reset_daily_tasks_for_today(db, current_user)
    skipped_task_ids = skipped_task_ids_for_today(db, current_user)
    today = local_date_for_user(current_user)
    tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.deleted_at.is_(None),
    ).order_by(Task.created_at.desc()).all()
    return filter_tasks_visible_today(tasks, skipped_task_ids, today)


@router.get("/tasks/standalone", response_model=List[TaskResponse])
def get_standalone_tasks(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    reset_daily_tasks_for_today(db, current_user)
    skipped_task_ids = skipped_task_ids_for_today(db, current_user)
    today = local_date_for_user(current_user)
    tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.sub_goal_id.is_(None),
        Task.deleted_at.is_(None),
    ).order_by(Task.created_at.desc()).all()
    return filter_tasks_visible_today(tasks, skipped_task_ids, today)


@router.get("/tasks/recurring", response_model=List[TaskResponse])
def get_recurring_tasks(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the saved recurring task masters, not today's filtered occurrences."""
    return db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.is_daily.is_(True),
        Task.deleted_at.is_(None),
    ).order_by(Task.created_at.desc()).all()


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.sub_goal_id:
        subgoal = db.query(SubGoal).join(Goal).filter(SubGoal.id == payload.sub_goal_id, Goal.user_id == current_user.id, SubGoal.deleted_at.is_(None)).first()
        if not subgoal:
            raise HTTPException(status_code=404, detail="Sub-goal not found")
        ensure_subgoal_unlocked(subgoal)
    recurrence_days = serialize_recurrence_days(payload.recurrence_days) if payload.is_daily else None
    today = local_date_for_user(current_user)
    start_date = payload.start_date or today
    runs_today = (
        start_date <= today
        and (not payload.is_daily or not recurrence_days or today.weekday() in parse_recurrence_days(recurrence_days))
    )
    task = Task(
        user_id=current_user.id,
        title=payload.title,
        difficulty=payload.difficulty,
        priority=payload.priority,
        sub_goal_id=payload.sub_goal_id,
        is_private=payload.is_private,
        is_daily=payload.is_daily,
        recurrence_days=recurrence_days,
        start_date=start_date,
        due_date=payload.due_date,
        last_generated_date=today if payload.is_daily and runs_today else None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.put("/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: UUID, payload: TaskUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(Task).options(joinedload(Task.sub_goal).joinedload(SubGoal.goal)).filter(Task.id == task_id, Task.user_id == current_user.id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    reset_daily_task_for_today(current_user, task)
    if task_is_skipped_today(db, current_user, task):
        raise HTTPException(status_code=404, detail="Task not found for today")
    if task.sub_goal:
        ensure_subgoal_unlocked(task.sub_goal)
    if task.is_completed:
        raise HTTPException(status_code=409, detail="Task is completed and locked")
    if "sub_goal_id" in payload.model_fields_set:
        if payload.sub_goal_id is None:
            task.sub_goal_id = None
        else:
            subgoal = db.query(SubGoal).join(Goal).filter(SubGoal.id == payload.sub_goal_id, Goal.user_id == current_user.id, SubGoal.deleted_at.is_(None)).first()
            if not subgoal:
                raise HTTPException(status_code=404, detail="Sub-goal not found")
            ensure_subgoal_unlocked(subgoal)
            task.sub_goal_id = payload.sub_goal_id
    if payload.title is not None:
        task.title = payload.title
    if payload.difficulty is not None:
        task.difficulty = payload.difficulty
    if payload.priority is not None:
        task.priority = payload.priority
    if payload.is_private is not None:
        task.is_private = payload.is_private
    if payload.is_daily is not None:
        task.is_daily = payload.is_daily
        if not payload.is_daily:
            task.recurrence_days = None
    if "recurrence_days" in payload.model_fields_set:
        task.recurrence_days = serialize_recurrence_days(payload.recurrence_days) if task.is_daily else None
    if "start_date" in payload.model_fields_set:
        task.start_date = payload.start_date or local_date_for_user(current_user)
    recalculate_daily_task_cycle_for_today(current_user, task)
    if payload.used_timer is not None:
        task.used_timer = payload.used_timer
    if "due_date" in payload.model_fields_set:
        task.due_date = payload.due_date
    if payload.is_completed is not None:
        was_completed = task.is_completed
        task.is_completed = payload.is_completed
        task.completed_at = utc_now() if payload.is_completed else None
        if payload.is_completed and task.is_daily:
            task.last_generated_date = local_date_for_user(current_user)
        if payload.is_completed and not was_completed:
            GamificationService.reward_task_completion(db, current_user, task)
    if task.sub_goal:
        sync_subgoal_completion(db, current_user, task.sub_goal)
        sync_goal_completion(db, current_user, task.sub_goal.goal)
    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/complete", response_model=CompletionRewardResponse)
def complete_task(task_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(Task).options(joinedload(Task.sub_goal).joinedload(SubGoal.goal)).filter(Task.id == task_id, Task.user_id == current_user.id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    reset_daily_task_for_today(current_user, task)
    today = local_date_for_user(current_user)
    if not task_visible_on_date(task, today, set()) or task_is_skipped_today(db, current_user, task):
        raise HTTPException(status_code=404, detail="Task not found for today")
    if task.sub_goal:
        ensure_subgoal_unlocked(task.sub_goal)
    if task.is_completed:
        return reward_response(current_user, "Task already completed", success=False)
    task.is_completed = True
    task.completed_at = utc_now()
    if task.is_daily:
        task.last_generated_date = local_date_for_user(current_user)
    reward = GamificationService.reward_task_completion(db, current_user, task)
    subgoal_reward = None
    goal_reward = None
    if task.sub_goal:
        subgoal_reward = sync_subgoal_completion(db, current_user, task.sub_goal)
        goal_reward = sync_goal_completion(db, current_user, task.sub_goal.goal)
    db.commit()
    db.refresh(current_user)
    return reward_response(current_user, "Task completed!", merge_rewards(reward, subgoal_reward, goal_reward))


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(Task).options(joinedload(Task.sub_goal).joinedload(SubGoal.goal)).filter(Task.id == task_id, Task.user_id == current_user.id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    reset_daily_task_for_today(current_user, task)
    if task.sub_goal:
        ensure_subgoal_unlocked(task.sub_goal)
    if task.is_completed:
        raise HTTPException(status_code=409, detail="Task is completed and locked")

    now = utc_now()
    today = local_date_for_user(current_user)
    if task.is_daily and task_visible_on_date(task, today, set()):
        skip_daily_task_for_today(db, current_user, task)
        task.updated_at = now
        db.commit()
        return None

    task.deleted_at = now
    task.updated_at = now
    db.commit()
    return None


@router.get("/habits", response_model=List[HabitResponse])
def get_habits(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    habits = db.query(Habit).filter(
        Habit.user_id == current_user.id,
        Habit.deleted_at.is_(None),
    ).order_by(Habit.created_at.desc()).all()

    if not habits:
        return []

    today = local_date_for_user(current_user)
    habit_ids = [habit.id for habit in habits]
    logged_habit_ids = {
        row[0]
        for row in db.query(HabitLog.habit_id).filter(
            HabitLog.user_id == current_user.id,
            HabitLog.habit_id.in_(habit_ids),
            HabitLog.local_date == today,
            HabitLog.deleted_at.is_(None),
        ).all()
    }

    for habit in habits:
        habit.logged_today = habit.id in logged_habit_ids

        if habit.habit_type == HabitTypeEnum.bad:
            penalty_info = GamificationService.calculate_bad_habit_penalty(db, current_user, habit)
            habit.bad_habit_penalty_preview = penalty_info["penalty"]
            habit.bad_habit_base_penalty = penalty_info["base_penalty"]
            habit.bad_habit_penalty_multiplier = penalty_info["repeat_multiplier"]
            habit.bad_habit_penalty_threshold = penalty_info["repeat_threshold"]
            habit.bad_habit_penalty_window_days = penalty_info["review_window_days"]
            habit.bad_habit_recent_penalty_count = penalty_info["recent_penalty_count"]
            habit.bad_habit_penalty_multiplier_active = penalty_info["multiplier_active"]
        else:
            habit.bad_habit_penalty_preview = 0
            habit.bad_habit_base_penalty = 0
            habit.bad_habit_penalty_multiplier = 1.0
            habit.bad_habit_penalty_threshold = 0
            habit.bad_habit_penalty_window_days = 0
            habit.bad_habit_recent_penalty_count = 0
            habit.bad_habit_penalty_multiplier_active = False

    return habits


@router.post("/habits", response_model=HabitResponse, status_code=status.HTTP_201_CREATED)
def create_habit(payload: HabitCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    habit = Habit(
        user_id=current_user.id,
        title=payload.title,
        habit_type=payload.habit_type,
        reminder_time=payload.reminder_time,
    )
    db.add(habit)
    db.commit()
    db.refresh(habit)
    return habit


@router.put("/habits/{habit_id}", response_model=HabitResponse)
def update_habit(habit_id: UUID, payload: HabitUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    habit = db.query(Habit).filter(Habit.id == habit_id, Habit.user_id == current_user.id, Habit.deleted_at.is_(None)).first()
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    if payload.title is not None:
        habit.title = payload.title
    if payload.habit_type is not None:
        habit.habit_type = payload.habit_type
    if "reminder_time" in payload.model_fields_set:
        habit.reminder_time = payload.reminder_time
    habit.updated_at = utc_now()
    db.commit()
    db.refresh(habit)
    return habit


def log_habit_for_date(
    habit_id: UUID,
    target_date: date | None,
    current_user: User,
    db: Session,
) -> HabitLogResponse:
    habit = db.query(Habit).filter(Habit.id == habit_id, Habit.user_id == current_user.id, Habit.deleted_at.is_(None)).first()
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")

    if target_date is not None and target_date > local_date_for_user(current_user):
        raise HTTPException(status_code=400, detail="Future habit dates cannot be logged")

    if habit.habit_type == HabitTypeEnum.good:
        result = GamificationService.log_good_habit(db, current_user, habit, target_date=target_date)
        penalty = 0
    else:
        result = GamificationService.log_bad_habit(db, current_user, habit, target_date=target_date)
        penalty = int(result.get("penalty", 0) or 0)
    db.commit()
    db.refresh(current_user)
    db.refresh(habit)
    return HabitLogResponse(
        success=bool(result.get("success", True)),
        message=result.get("message", "Habit logged"),
        xp_earned=int(result.get("xp_earned", 0) or 0),
        coins_earned=int(result.get("coins_earned", 0) or 0),
        penalty=penalty,
        new_streak=habit.current_streak,
        new_balance={"level": current_user.level, "xp": current_user.xp_balance, "coins": current_user.coin_balance},
    )


@router.post("/habits/{habit_id}/log", response_model=HabitLogResponse)
def log_habit(habit_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return log_habit_for_date(habit_id, None, current_user, db)


@router.post("/habits/{habit_id}/log-date", response_model=HabitLogResponse)
def log_habit_date(
    habit_id: UUID,
    payload: HabitLogDateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return log_habit_for_date(habit_id, payload.local_date, current_user, db)


@router.get("/habits/{habit_id}/history", response_model=List[HabitHistoryItem])
def get_habit_history(habit_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    habit = db.query(Habit).filter(Habit.id == habit_id, Habit.user_id == current_user.id, Habit.deleted_at.is_(None)).first()
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    return (
        db.query(HabitLog)
        .filter(HabitLog.habit_id == habit.id, HabitLog.user_id == current_user.id, HabitLog.deleted_at.is_(None))
        .order_by(HabitLog.local_date.desc(), HabitLog.created_at.desc())
        .all()
    )


@router.delete("/habits/{habit_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_habit(habit_id: UUID, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    habit = db.query(Habit).filter(Habit.id == habit_id, Habit.user_id == current_user.id, Habit.deleted_at.is_(None)).first()
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    now = utc_now()
    habit.deleted_at = now
    habit.updated_at = now
    db.commit()
    return None
