import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db.session import SessionLocal
from src.db.models import AuditLog

db = SessionLocal()
try:
    print("Querying audit logs...")
    logs = db.query(AuditLog).all()
    print(f"Successfully retrieved {len(logs)} audit logs.")
    for l in logs[:10]:
        print(f"Log: ID={l.id}, user_id={l.user_id}, endpoint={l.endpoint}, timestamp={l.timestamp}, type={type(l.timestamp)}")
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    db.close()
