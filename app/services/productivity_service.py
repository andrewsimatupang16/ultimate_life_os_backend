from sqlalchemy.orm import Session

from app.models import Goal, Habit, SubGoal, Task
from app.utils.time import utc_now


class ProductivityService:
    @staticmethod
    def create_task(db: Session, user_id, data):
        task = Task(
            user_id=user_id,
            title=data.title,
            difficulty=data.difficulty,
            sub_goal_id=data.sub_goal_id,
            is_private=data.is_private,
            is_daily=data.is_daily,
            is_completed=False,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def complete_task(db: Session, task_id, user_id=None):
        query = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None))
        if user_id is not None:
            query = query.filter(Task.user_id == user_id)
        task = query.first()
        if not task:
            return None
        task.is_completed = True
        task.completed_at = utc_now()
        db.commit()
        db.refresh(task)
        return task
