from sqlalchemy.orm import Session

from app.auth import get_password_hash
from app.models import User
from app.schemas import UserCreate
from app.services.auth_service import generate_friend_code


class UserService:
    @staticmethod
    def create_user(db: Session, data: UserCreate):
        user = User(
            email=data.email,
            full_name=data.full_name,
            hashed_password=get_password_hash(data.password),
            friend_code=generate_friend_code(db),
            level=1,
            xp_balance=0,
            total_xp_earned=0,
            coin_balance=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def get_user_by_email(db: Session, email: str):
        return db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()

    @staticmethod
    def get_user_by_id(db: Session, user_id):
        return db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
