from sqlalchemy.orm import Session
from app.models import User, Task


class GamificationService:

    @staticmethod
    def add_xp(db: Session, user_id: str, xp: int):

        user = db.query(User).filter(User.id == user_id).first()

        user.xp_balance += xp
        user.total_xp_earned += xp

        # level up logic
        if user.xp_balance >= user.level * 100:
            user.level += 1
            user.xp_balance = 0

        db.commit()
        return user


    @staticmethod
    def reward_task_completion(db: Session, user_id: str, task: Task):

        if task.difficulty == "easy":
            xp = 10
            coins = 5
        elif task.difficulty == "medium":
            xp = 25
            coins = 15
        else:
            xp = 50
            coins = 30

        user = db.query(User).filter(User.id == user_id).first()

        user.xp_balance += xp
        user.coin_balance += coins
        user.total_xp_earned += xp

        db.commit()

        return {
            "xp": xp,
            "coins": coins
        }