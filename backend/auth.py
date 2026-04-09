from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from .models import AuthKeyResponse, RegisterRequest, RegisterResponse
from .store import store

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse)
async def register(payload: RegisterRequest) -> RegisterResponse:
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address.")
    existing = store.users_by_email.get(email)
    user = existing or store.register_user(name=payload.name.strip() or "User", email=email)
    return RegisterResponse(**user)


@router.get("/key", response_model=AuthKeyResponse)
async def get_key(email: str) -> AuthKeyResponse:
    user = store.users_by_email.get(email.strip().lower())
    if not user:
        raise HTTPException(status_code=404, detail="Email not found.")
    return AuthKeyResponse(**user)


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> dict:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header.")
    user = store.users_by_key.get(x_api_key)
    if not user or not user.get("is_active", False):
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")
    return {"user_id": user["email"], "name": user["name"], "email": user["email"]}


AuthContext = Depends(verify_api_key)

