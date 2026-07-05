import secrets
import string
from typing import Optional

from sqlalchemy.orm import Session

from app.auth import create_access_token, create_refresh_token, get_password_hash, verify_password
from app.models import User
from app.schemas import UserCreate


def normalize_email(email: str) -> str:
    return email.strip().lower()


def generate_friend_code(db: Session) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(12))
        exists = db.query(User).filter(User.friend_code == code).first()
        if not exists:
            return code


class AuthService:
    @staticmethod
    def _token_payload(user: User) -> dict:
        return {"sub": str(user.id)}

    @classmethod
    def _auth_response(cls, user: User) -> dict:
        payload = cls._token_payload(user)
        return {
            "access_token": create_access_token(payload),
            "refresh_token": create_refresh_token(payload),
            "token_type": "bearer",
            "user": user,
        }

    @classmethod
    def register(cls, db: Session, data: UserCreate) -> Optional[dict]:
        email = normalize_email(str(data.email))
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            return None

        user = User(
            email=email,
            hashed_password=get_password_hash(data.password),
            full_name=data.full_name,
            friend_code=generate_friend_code(db),
            level=1,
            xp_balance=0,
            total_xp_earned=0,
            coin_balance=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return cls._auth_response(user)

    @classmethod
    def login(cls, db: Session, email: str, password: str) -> Optional[dict]:
        user = db.query(User).filter(User.email == normalize_email(email), User.deleted_at.is_(None)).first()
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.friend_code:
            user.friend_code = generate_friend_code(db)
            db.commit()
            db.refresh(user)
        return cls._auth_response(user)
