"""Auth endpoints: login (OAuth2 password flow) + current user."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import AppUser
from ..schemas import Token, UserOut
from ..security import CurrentUser, create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=Token)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # OAuth2 form uses "username"; we treat it as email.
    user = (await db.execute(select(AppUser).where(AppUser.email == form.username))).scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_pw):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return Token(access_token=create_access_token(user.id, user.role))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut(id=user.id, email=user.email, display_name=user.display_name, role=user.role)
