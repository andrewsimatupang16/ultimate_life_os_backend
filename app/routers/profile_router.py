from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_password_hash
from app.database import get_db
from app.dependencies import get_current_user
from app.models import CoinLedger, GamificationEvent, User, UserAchievement
from app.schemas import CoinLedgerResponse, GamificationEventResponse, UserAchievementResponse, UserPublicProfile, UserResponse, UserUpdate
from app.services.auth_service import generate_friend_code
from app.utils.time import utc_now

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/update", response_model=UserResponse)
def update_profile(
    update_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if update_data.email is not None and update_data.email != current_user.email:
        existing = db.query(User).filter(User.email == update_data.email, User.id != current_user.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already used")
        current_user.email = update_data.email

    if update_data.full_name is not None:
        current_user.full_name = update_data.full_name
    if update_data.avatar_url is not None:
        current_user.avatar_url = update_data.avatar_url
    if update_data.active_title is not None:
        current_user.active_title = update_data.active_title
    if update_data.timezone is not None:
        current_user.timezone = update_data.timezone
    if update_data.password is not None:
        current_user.hashed_password = get_password_hash(update_data.password)

    if not current_user.friend_code:
        current_user.friend_code = generate_friend_code(db)

    current_user.updated_at = utc_now()
    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/coins/history", response_model=List[CoinLedgerResponse])
def get_coin_history(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(CoinLedger)
        .filter(CoinLedger.user_id == current_user.id, CoinLedger.deleted_at.is_(None))
        .order_by(CoinLedger.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/gamification/history", response_model=List[GamificationEventResponse])
def get_gamification_history(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(GamificationEvent)
        .filter(GamificationEvent.user_id == current_user.id, GamificationEvent.deleted_at.is_(None))
        .order_by(GamificationEvent.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get("/achievements", response_model=List[UserAchievementResponse])
def get_achievements(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(UserAchievement)
        .filter(UserAchievement.user_id == current_user.id, UserAchievement.deleted_at.is_(None))
        .order_by(UserAchievement.awarded_at.desc())
        .all()
    )


@router.get("/find/{friend_code}", response_model=UserPublicProfile)
def find_by_friend_code(
    friend_code: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.friend_code == friend_code.upper(), User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/public/{user_id}", response_model=UserPublicProfile)
def get_public_profile(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
