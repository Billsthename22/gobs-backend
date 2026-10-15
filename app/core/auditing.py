"""Persist write attempts before executing them; never record bodies or tokens."""
from fastapi import APIRouter, HTTPException
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from app.core.security import decode_access_token
from app.models.user import User
from app.core.database import SessionLocal
from app.models.audit import AuditLog


class AuditedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def audited(request):
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return await handler(request)
            # An unavailable audit database prevents an unaudited mutation.
            with SessionLocal() as db:
                entry = AuditLog(action=f"{request.method} {self.path}", resource=request.url.path[:255], result="attempted")
                authorization = request.headers.get("authorization", "")
                user_id = decode_access_token(authorization[7:]) if authorization.startswith("Bearer ") else None
                user = db.get(User, user_id) if user_id else None
                if user:
                    entry.actor_email = user.email
                db.add(entry)
                db.commit()
                db.refresh(entry)
                audit_id = entry.id
            code = 500
            try:
                response = await handler(request)
                code = response.status_code
                return response
            except HTTPException as exc:
                code = exc.status_code
                raise
            except RequestValidationError:
                code = 422
                raise
            finally:
                with SessionLocal() as db:
                    entry = db.get(AuditLog, audit_id)
                    entry.actor_email = getattr(request.state, "actor_email", None) or entry.actor_email
                    entry.status_code = code
                    entry.result = "succeeded" if code < 400 else "denied" if code in {401, 403} else "failed"
                    db.commit()
        return audited


class AuditedRouter(APIRouter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, route_class=AuditedRoute, **kwargs)
