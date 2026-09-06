"""Authenticated access to the persistent global notice inbox."""

from fastapi import APIRouter, Depends

from tweetnook.web.deps import server_state, verify_credentials

router = APIRouter(
    prefix="/api/notices", tags=["notices"], dependencies=[Depends(verify_credentials)]
)


@router.get("")
def list_notices():
    notices = server_state.get("notices")
    return {"notices": notices.list() if notices else []}


@router.post("/{notice_id}/dismiss")
def dismiss_notice(notice_id: str):
    if notices := server_state.get("notices"):
        notices.dismiss(notice_id)
    return {"dismissed": True}
