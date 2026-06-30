"""
src/core/schemas.py
===================
Lightweight in-memory representations of authenticated state.

UserSession is returned by get_current_user() when the Redis session cache
is warm (the 95% fast path).  It is duck-type compatible with the
SQLAlchemy User ORM object for every field that endpoints actually read,
so no endpoint code needs to change.

On Redis cold-path (session expired / first login) get_current_user()
falls back to SQL and returns a real User ORM object.
"""

from dataclasses import dataclass, field


@dataclass
class UserSession:
    """
    Populated from Redis HGETALL session:{user_id}.

    Fields mirror the SQLAlchemy User model attributes that are used by
    endpoints and FastAPI dependencies:
        user.id         → id
        user.email      → email
        user.role       → role  (str, matches UserRole enum values)
        user.is_active  → is_active

    Extra plan fields allow deps.check_rate_limit() to run entirely in
    Redis without touching SQL.
    """

    # --- Core identity (mirrored from User ORM) ---
    id: int
    email: str
    role: str
    is_active: bool = True

    # --- Plan / quota data (stored in session hash on login) ---
    plan: str = "free"
    rps: int = 5               # requests per second
    daily_quota: int = 1000
    monthly_quota: int = 30_000
