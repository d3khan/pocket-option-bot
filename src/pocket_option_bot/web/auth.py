"""Authentication logic (JWT, login, cookie with sliding 24h idle expiry)."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.hash import bcrypt
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)
auth_router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

# Cookie settings
COOKIE_NAME = "access_token"
COOKIE_MAX_AGE = 24 * 60 * 60  # 24 hours in seconds (idle timeout)

class TokenData(BaseModel):
    username: str

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt.secret_key, algorithm=settings.jwt.algorithm)
    return encoded_jwt

def set_auth_cookie(response: Response, token: str):
    """Set the auth cookie with 24h sliding expiry."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=False,          # False for local HTTP; set True in production with HTTPS
        samesite="lax",
        max_age=COOKIE_MAX_AGE,
    )

def clear_auth_cookie(response: Response):
    """Delete the auth cookie."""
    response.delete_cookie(COOKIE_NAME)

@auth_router.post("/login", include_in_schema=False)
async def login(request: Request, response: Response):
    """Form‑based login (for WebUI)."""
    form_data = await request.form()
    username = form_data.get("username")
    password = form_data.get("password")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing credentials")
    if username != settings.auth.username:
        raise HTTPException(status_code=401, detail="Invalid username")
    if not verify_password(password, settings.auth.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")
    # Create JWT
    access_token = create_access_token(data={"sub": username})
    set_auth_cookie(response, access_token)
    # Redirect to dashboard
    response.headers["Location"] = "/"
    response.status_code = status.HTTP_303_SEE_OTHER
    return response

@auth_router.post("/logout")
async def logout(response: Response):
    clear_auth_cookie(response)
    # Redirect to login page
    response.headers["Location"] = "/login"
    response.status_code = status.HTTP_303_SEE_OTHER
    return response

@auth_router.post("/token", response_model=dict)
async def token_login(form_data: OAuth2PasswordRequestForm = Depends()):
    """OAuth2 token endpoint for API clients."""
    if form_data.username != settings.auth.username or not verify_password(form_data.password, settings.auth.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token(data={"sub": form_data.username})
    return {"access_token": access_token, "token_type": "bearer"}

async def get_current_user(request: Request):
    """Dependency to extract and validate JWT from cookie. Raises 401 if invalid."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])
        username: str = payload.get("sub")
        if username is None or username != settings.auth.username:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user_redirect(request: Request, response: Response):
    """Dependency that redirects to login if not authenticated. Refreshes cookie on success (sliding expiry)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        payload = jwt.decode(token, settings.jwt.secret_key, algorithms=[settings.jwt.algorithm])
        username: str = payload.get("sub")
        if username is None or username != settings.auth.username:
            return RedirectResponse(url="/login", status_code=303)
        # Valid token - refresh cookie for sliding 24h idle expiry
        set_auth_cookie(response, token)
        return username
    except JWTError:
        return RedirectResponse(url="/login", status_code=303)