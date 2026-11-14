from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.common import ResponseModel

router = APIRouter(prefix="/perm/apikey", tags=["能力商店APIKey"])


@router.post("/create", response_model=ResponseModel[dict], summary="新建APIKey凭证")
def create_api_key(db: Session = Depends(get_db)):
    return ResponseModel(data={"access_key": "ak-prod-sample", "secret_key": "sk-sample-secret"})


@router.post("/rotate", response_model=ResponseModel[dict], summary="双Key过渡轮换")
def rotate_api_key(db: Session = Depends(get_db)):
    return ResponseModel(data={"status": "rotating"})


@router.post("/revoke", response_model=ResponseModel[dict], summary="吊销APIKey")
def revoke_api_key(db: Session = Depends(get_db)):
    return ResponseModel(data={"status": "revoked"})
