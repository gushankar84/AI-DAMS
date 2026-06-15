"""Authentication & RBAC: JWT issuance/verification, password hashing,
current-user dependency, and a role-requirement helper."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db
from .models import AppUser

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# Role hierarchy — higher roles inherit the rights of lower ones.
ROLE_RANK = {"viewer": 0, "contributor": 1, "reviewer": 2, "distributor": 2, "administrator": 3}


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, hashed: str) -> bool:
    return pwd.verify(p, hashed)


def create_access_token(sub: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_ttl_min)
    payload = {"sub": sub, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AppUser:
    cred_exc = HTTPException(status.HTTP_401_UNAUTHORIZED, "Could not validate credentials")
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
        user_id = payload.get("sub")
    except JWTError:
        raise cred_exc
    user = (await db.execute(select(AppUser).where(AppUser.id == user_id))).scalar_one_or_none()
    if not user or not user.is_active:
        raise cred_exc
    return user


def require_role(min_role: str):
    """Dependency factory: enforce a minimum role (RBAC, BRD §9 / NFR-S1)."""
    async def _dep(user: Annotated[AppUser, Depends(get_current_user)]) -> AppUser:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role >= {min_role}")
        return user
    return _dep


CurrentUser = Annotated[AppUser, Depends(get_current_user)]
