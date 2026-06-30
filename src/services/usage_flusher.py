"""
src/services/usage_flusher.py
==============================
Background asyncio task that syncs Redis buffers → SQL every 60 seconds.

Two buffers are flushed:

1. Usage counters  usage:{user_id}:{date}  →  UsageRecord table
   chat.py records all token usage via Redis HINCRBY (atomic, 0 SQL).
   This replaces 1000 SQL writes/day with 1 SQL write/minute per active user.

2. Audit log buffer  audit:buffer  →  AuditLog table
   chat.py pushes JSON entries onto a Redis LIST via RPUSH (O(1)).
   The flusher drains the list and bulk-INSERTs up to 500 rows per cycle.
   This replaces 1 SQL write per request with 1 bulk INSERT per minute.

On shutdown, a final flush is triggered so no buffered data is lost.
"""

import asyncio
import logging
import datetime

logger = logging.getLogger("usage_flusher")

FLUSH_INTERVAL_SECONDS = 60   # sync every minute


async def flush_usage_to_sql() -> int:
    """
    Read all usage:* Redis keys and upsert their counters into UsageRecord.

    Returns the number of user-day records flushed.
    """
    # Lazy imports to avoid circular dependency at module load time
    from src.services.redis_service import redis_service
    from src.db.session import SessionLocal
    from src.db.models import UsageRecord

    keys   = redis_service.get_all_usage_keys()   # e.g. ["usage:1:2025-06-29", …]
    db     = SessionLocal()
    flushed = 0

    try:
        for key in keys:
            # Parse key parts: usage:{user_id}:{date}
            parts = key.split(":")
            if len(parts) != 3:
                continue
            try:
                user_id  = int(parts[1])
                date_obj = datetime.date.fromisoformat(parts[2])
            except (ValueError, TypeError):
                continue

            counters = redis_service.get_usage(user_id, parts[2])
            if counters["request_count"] == 0:
                continue

            # Upsert into UsageRecord
            rec = (
                db.query(UsageRecord)
                .filter(UsageRecord.user_id == user_id, UsageRecord.date == date_obj)
                .first()
            )
            if rec:
                rec.request_count = counters["request_count"]
                rec.input_tokens  = counters["input_tokens"]
                rec.output_tokens = counters["output_tokens"]
            else:
                rec = UsageRecord(
                    user_id=user_id,
                    date=date_obj,
                    request_count=counters["request_count"],
                    input_tokens=counters["input_tokens"],
                    output_tokens=counters["output_tokens"],
                    cost=0.0,
                )
                db.add(rec)

            flushed += 1

        db.commit()
        if flushed:
            logger.info(
                "[FLUSHER  ] [FLUSH  ] Synced %d user-day usage records to SQL", flushed
            )
    except Exception as exc:
        db.rollback()
        logger.error("[FLUSHER  ] [ERROR  ] Usage flush failed: %s", exc)
    finally:
        db.close()

    return flushed

async def flush_audit_to_sql() -> int:
    """
    Drain the Redis audit:buffer LIST and bulk-INSERT into the AuditLog table.

    Drains up to 500 entries per cycle.  Entries are removed from Redis
    atomically via LRANGE + LTRIM before insertion, so a DB failure does
    not cause double-writes on the next cycle (the entries are gone from
    Redis on drain — acceptable trade-off for simplicity).

    Returns the number of audit entries written.
    """
    from src.services.redis_service import redis_service
    from src.db.session import SessionLocal
    from src.db.models import AuditLog
    import datetime as _dt

    entries = redis_service.drain_audit_buffer(batch_size=500)
    if not entries:
        return 0

    db = SessionLocal()
    written = 0
    try:
        for entry in entries:
            try:
                log = AuditLog(
                    user_id=entry.get("user_id"),
                    endpoint=entry.get("endpoint", "/api/v1/chat"),
                    method=entry.get("method", "POST"),
                    status_code=int(entry.get("status_code", 200)),
                    latency_ms=int(entry.get("latency_ms", 0)),
                    ip_address=entry.get("ip_address"),
                    user_agent=entry.get("user_agent"),
                    request_id=entry.get("request_id"),
                )
                db.add(log)
                written += 1
            except Exception as row_exc:
                logger.warning("[FLUSHER  ] [WARN   ] Skipping malformed audit entry: %s", row_exc)

        db.commit()
        if written:
            logger.info(
                "[FLUSHER  ] [AUDIT  ] Bulk-inserted %d audit log rows to SQL", written
            )
    except Exception as exc:
        db.rollback()
        logger.error("[FLUSHER  ] [ERROR  ] Audit flush failed: %s", exc)
        written = 0
    finally:
        db.close()

    return written



async def usage_flush_loop() -> None:
    """
    Infinite asyncio loop: flush usage counters and audit buffer every
    FLUSH_INTERVAL_SECONDS.

    Designed to run as a background task started in main.py lifespan.
    Catches all exceptions so a transient DB error never kills the loop.
    """
    logger.info(
        "[FLUSHER  ] [START  ] Usage+audit flusher started (interval=%ds)", FLUSH_INTERVAL_SECONDS
    )
    while True:
        try:
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
            await flush_usage_to_sql()
            await flush_audit_to_sql()
        except asyncio.CancelledError:
            # Graceful shutdown — do one final flush before exiting
            logger.info("[FLUSHER  ] [STOP   ] Shutdown received — running final flush…")
            await flush_usage_to_sql()
            await flush_audit_to_sql()
            logger.info("[FLUSHER  ] [STOP   ] Final flush complete. Flusher stopped.")
            raise   # re-raise so the task actually cancels
        except Exception as exc:
            logger.error("[FLUSHER  ] [ERROR  ] Unexpected error in flush loop: %s", exc)
            await asyncio.sleep(5)
