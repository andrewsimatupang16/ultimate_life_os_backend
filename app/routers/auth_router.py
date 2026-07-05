from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import create_access_token, create_refresh_token, decode_token
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import RefreshTokenRequest, TokenResponse, UserCreate, UserLogin, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(data: UserCreate, db: Session = Depends(get_db)):
    result = AuthService.register(db, data)
    if not result:
        raise HTTPException(status_code=400, detail="Email already registered")
    return result


@router.post("/login", response_model=TokenResponse)
def login(data: UserLogin, db: Session = Depends(get_db)):
    result = AuthService.login(db, data.email, data.password)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return result


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    body: RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    token = body.refresh_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="refresh_token is required")

    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    try:
        user_uuid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = db.query(User).filter(User.id == user_uuid, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    token_payload = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_payload),
        "refresh_token": create_refresh_token(token_payload),
        "token_type": "bearer",
        "user": user,
    }
