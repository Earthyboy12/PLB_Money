import sqlite3
import os
import tempfile
from datetime import datetime
from typing import List, Dict, Any, Optional

def get_db_path() -> str:
    """Find a writable database path (supports local, Render, Vercel serverless, etc.)."""
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expenses.db")
    try:
        test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".write_test")
        with open(test_file, "w") as f:
            f.write("1")
        os.remove(test_file)
        return local_path
    except Exception:
        tmp_dir = os.environ.get("TMPDIR") or tempfile.gettempdir()
        tmp_db = os.path.join(tmp_dir, "expenses.db")
        if os.path.exists(local_path) and not os.path.exists(tmp_db):
            try:
                import shutil
                shutil.copyfile(local_path, tmp_db)
            except Exception:
                pass
        return tmp_db

DB_PATH = get_db_path()

def get_connection():
    global DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        # Fallback to in-memory or temp if DB_PATH fails
        DB_PATH = os.path.join(tempfile.gettempdir(), "expenses.db")
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref_id TEXT UNIQUE,
            date_time TEXT,
            bank TEXT,
            transaction_type TEXT,
            sender_name TEXT,
            sender_account TEXT,
            receiver_name TEXT,
            receiver_account TEXT,
            amount REAL NOT NULL,
            fee REAL DEFAULT 0.0,
            category TEXT,
            note TEXT,
            image_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def add_transaction(item: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO transactions (
            ref_id, date_time, bank, transaction_type,
            sender_name, sender_account, receiver_name, receiver_account,
            amount, fee, category, note, image_path
        ) VALUES (
            :ref_id, :date_time, :bank, :transaction_type,
            :sender_name, :sender_account, :receiver_name, :receiver_account,
            :amount, :fee, :category, :note, :image_path
        )
    """, {
        "ref_id": item.get("ref_id"),
        "date_time": item.get("date_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "bank": item.get("bank", "ไม่ระบุ"),
        "transaction_type": item.get("transaction_type", "โอนเงิน"),
        "sender_name": item.get("sender_name", ""),
        "sender_account": item.get("sender_account", ""),
        "receiver_name": item.get("receiver_name", ""),
        "receiver_account": item.get("receiver_account", ""),
        "amount": float(item.get("amount", 0.0)),
        "fee": float(item.get("fee", 0.0)),
        "category": item.get("category", "อื่นๆ"),
        "note": item.get("note", ""),
        "image_path": item.get("image_path", "")
    })
    tx_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return tx_id

def check_duplicate_transaction(
    ref_id: Optional[str] = None,
    amount: float = 0.0,
    date_time: Optional[str] = None,
    bank: Optional[str] = None,
    receiver_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Check if slip is already recorded (matching by ref_id or matching amount + date + bank/receiver)."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Match by ref_id if provided and not generic fallback
    if ref_id and len(ref_id) >= 6 and not ref_id.startswith("TXN20"):
        cursor.execute("SELECT id, ref_id, amount, date_time, bank, receiver_name FROM transactions WHERE ref_id = ? LIMIT 1", (ref_id,))
        row = cursor.fetchone()
        if row:
            conn.close()
            return dict(row)

    # 2. Match by exact amount + same calendar date + bank or receiver
    if amount > 0 and date_time and len(date_time) >= 10:
        day_str = date_time[:10]  # YYYY-MM-DD
        cursor.execute("""
            SELECT id, ref_id, amount, date_time, bank, receiver_name 
            FROM transactions 
            WHERE ABS(amount - ?) < 0.001 AND SUBSTR(date_time, 1, 10) = ? AND (bank = ? OR receiver_name = ?)
            LIMIT 1
        """, (amount, day_str, bank or "", receiver_name or ""))
        row = cursor.fetchone()
        if row:
            conn.close()
            return dict(row)

    conn.close()
    return None

def clear_all_transactions() -> int:
    """Clear all transactions history."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions")
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count

def get_available_months() -> List[str]:
    """Get list of distinct months (YYYY-MM) with transactions."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT SUBSTR(date_time, 1, 7) as month
        FROM transactions
        WHERE date_time IS NOT NULL AND LENGTH(date_time) >= 7
        ORDER BY month DESC
    """)
    months = [r["month"] for r in cursor.fetchall() if r["month"]]
    conn.close()
    return months

def get_transactions(
    category: Optional[str] = None,
    bank: Optional[str] = None,
    search: Optional[str] = None,
    month: Optional[str] = None
) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM transactions WHERE 1=1"
    params = []
    
    if month and month != "ทั้งหมด":
        query += " AND SUBSTR(date_time, 1, 7) = ?"
        params.append(month)
    if category and category != "ทั้งหมด":
        query += " AND category = ?"
        params.append(category)
    if bank and bank != "ทั้งหมด":
        query += " AND bank = ?"
        params.append(bank)
    if search:
        query += " AND (receiver_name LIKE ? OR sender_name LIKE ? OR note LIKE ? OR ref_id LIKE ?)"
        term = f"%{search}%"
        params.extend([term, term, term, term])
        
    query += " ORDER BY date_time DESC, id DESC"
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_transaction_by_id(tx_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_transaction(tx_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def update_transaction(tx_id: int, item: Dict[str, Any]) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE transactions SET
            date_time = :date_time,
            bank = :bank,
            transaction_type = :transaction_type,
            receiver_name = :receiver_name,
            amount = :amount,
            category = :category,
            note = :note
        WHERE id = :id
    """, {
        "id": tx_id,
        "date_time": item.get("date_time"),
        "bank": item.get("bank"),
        "transaction_type": item.get("transaction_type"),
        "receiver_name": item.get("receiver_name"),
        "amount": float(item.get("amount", 0.0)),
        "category": item.get("category"),
        "note": item.get("note", "")
    })
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated

def get_statistics(month: Optional[str] = None):
    conn = get_connection()
    cursor = conn.cursor()
    
    where_clause = ""
    params = []
    if month and month != "ทั้งหมด":
        where_clause = "WHERE SUBSTR(date_time, 1, 7) = ?"
        params = [month]

    cursor.execute(f"SELECT COUNT(*) as count, SUM(amount) as total, AVG(amount) as average FROM transactions {where_clause}", params)
    summary = cursor.fetchone()
    total_amount = summary["total"] or 0.0
    total_count = summary["count"] or 0
    avg_amount = summary["average"] or 0.0

    cursor.execute(f"""
        SELECT category, SUM(amount) as total, COUNT(*) as count
        FROM transactions
        {where_clause}
        GROUP BY category
        ORDER BY total DESC
    """, params)
    categories = [dict(r) for r in cursor.fetchall()]

    cursor.execute(f"""
        SELECT bank, SUM(amount) as total, COUNT(*) as count
        FROM transactions
        {where_clause}
        GROUP BY bank
        ORDER BY total DESC
    """, params)
    banks = [dict(r) for r in cursor.fetchall()]

    day_where = f"{where_clause} AND" if where_clause else "WHERE"
    cursor.execute(f"""
        SELECT SUBSTR(date_time, 1, 10) as day, SUM(amount) as total, COUNT(*) as count
        FROM transactions
        {day_where} date_time IS NOT NULL AND date_time != ''
        GROUP BY day
        ORDER BY day ASC
    """, params)
    daily = [dict(r) for r in cursor.fetchall()]

    rec_where = f"{where_clause} AND" if where_clause else "WHERE"
    cursor.execute(f"""
        SELECT receiver_name, SUM(amount) as total, COUNT(*) as count
        FROM transactions
        {rec_where} receiver_name IS NOT NULL AND receiver_name != ''
        GROUP BY receiver_name
        ORDER BY total DESC
        LIMIT 5
    """, params)
    top_receivers = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {
        "month": month or "ทั้งหมด",
        "total_amount": round(total_amount, 2),
        "total_count": total_count,
        "avg_amount": round(avg_amount, 2),
        "categories": categories,
        "banks": banks,
        "daily": daily,
        "top_receivers": top_receivers
    }
