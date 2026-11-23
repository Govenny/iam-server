from fastapi import APIRouter
from app.api.v1.portal_sync import router as portal_sync_router
from app.api.v1.sub_account import router as sub_account_router
from app.api.v1.workspace_perm import router as workspace_perm_router
from app.api.v1.jump_ticket import router as jump_ticket_router
from app.api.v1.api_key import router as api_key_router
from app.api.v1.shadow_grant import router as shadow_grant_router
from app.api.v1.admin_ops import router as admin_ops_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(portal_sync_router)
api_v1_router.include_router(sub_account_router)
api_v1_router.include_router(workspace_perm_router)
api_v1_router.include_router(jump_ticket_router)
api_v1_router.include_router(api_key_router)
api_v1_router.include_router(shadow_grant_router)
api_v1_router.include_router(admin_ops_router)
