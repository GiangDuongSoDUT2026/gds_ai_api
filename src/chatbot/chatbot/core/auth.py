from jose import JWTError, jwt
from chatbot.config import get_settings


def decode_user_context(token: str | None) -> dict | None:
    """Returns {user_id, role, organization_id, faculty} or None if invalid."""
    if not token:
        return None
    try:
        settings = get_settings()
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != "access":
            return None
        return {
            "user_id": payload.get("sub"),
            "role": payload.get("role", "STUDENT"),
            "organization_id": payload.get("org"),
            "faculty": payload.get("faculty"),
        }
    except JWTError:
        return None
