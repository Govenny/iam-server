from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.schemas.common import ResponseModel

router = APIRouter(tags=["契约回调与影子路由"])


@router.post("/perm/grant/trusted-data-callback", response_model=ResponseModel[dict], summary="可信数据流通交易契约回调")
def trusted_data_callback(db: Session = Depends(get_db)):
    return ResponseModel(data={"status": "callback_processed"})


@router.get("/internal/v1/uc/shadow-route/resolve", response_model=ResponseModel[dict], summary="查询或JIT创建影子账号")
def resolve_shadow_route(db: Session = Depends(get_db)):
    return ResponseModel(data={"shadow_user_id": "shadow_usr_sample"})
