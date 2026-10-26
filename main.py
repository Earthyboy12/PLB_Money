import os
import socket
import shutil
import uuid
import base64
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import database as db
import parser

app = FastAPI(title="PLB Money Is King", description="Polar Bear Arctic Slip Expense Analyzer")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
SAMPLES_DIR = os.path.join(STATIC_DIR, "samples")

try:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    os.makedirs(SAMPLES_DIR, exist_ok=True)
except Exception:
    pass

# Mount static files safely
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_local_ip():
    """Detect local LAN IP for mobile access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@app.on_event("startup")
def startup():
    try:
        db.init_db()
    except Exception as e:
        print(f"[DB Init Warning] {e}")

@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>PLB Money Is King is running...</h1>")

@app.get("/api/server-info")
def server_info():
    ip = get_local_ip()
    port = 8000
    return {
        "local_ip": ip,
        "port": port,
        "mobile_url": f"http://{ip}:{port}",
        "status": "online"
    }

@app.get("/api/config")
def get_config():
    key = db.get_setting("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    has_key = bool(key and len(key.strip()) > 5)
    masked = f"{key[:6]}...{key[-4:]}" if has_key and len(key) > 10 else ("Configured" if has_key else "")
    return {
        "has_api_key": has_key,
        "masked_key": masked
    }

@app.post("/api/config")
def set_config(data: Dict[str, str]):
    key = data.get("gemini_api_key", "").strip()
    db.set_setting("gemini_api_key", key)
    return {"status": "saved", "has_api_key": bool(key)}

@app.post("/api/validate-key")
def validate_key(data: Dict[str, str]):
    key = data.get("gemini_api_key", "").strip()
    return parser.validate_gemini_api_key(key)

import io
from PIL import Image

def make_thumbnail_data_url(content: bytes) -> str:
    """Create a lightweight ~12KB base64 JPEG thumbnail to eliminate 413 Payload Too Large."""
    try:
        im = Image.open(io.BytesIO(content))
        im.thumbnail((360, 480))
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=65)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return "data:image/jpeg;base64," + base64.b64encode(content[:15000]).decode("utf-8")

@app.post("/api/upload-and-analyze")
async def upload_and_analyze(
    request: Request,
    files: List[UploadFile] = File(...),
    gemini_api_key: Optional[str] = Form(None),
    key: Optional[str] = None
):
    results = []
    ignored = []
    batch_fingerprints = set()
    
    # Extract API key from Form, Query Param, Header, DB, or OS Environment
    client_api_key = (
        gemini_api_key
        or key
        or request.headers.get("x-gemini-api-key")
        or db.get_setting("gemini_api_key")
        or os.environ.get("GEMINI_API_KEY")
        or ""
    ).strip().replace('"', '').replace("'", "")
    print(f"[Upload] Key present: {bool(client_api_key)} (len: {len(client_api_key)})")
    if client_api_key:
        try:
            db.set_setting("gemini_api_key", client_api_key)
        except Exception:
            pass

    # Check if disk writing is allowed (Vercel serverless has read-only static dir)
    can_save_disk = True
    try:
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        test_file = os.path.join(UPLOADS_DIR, ".write_test")
        with open(test_file, "w") as f:
            f.write("1")
        os.remove(test_file)
    except Exception:
        can_save_disk = False

    for file in files:
        try:
            content = await file.read()
            if not content:
                continue

            # Determine mime type & safe extension
            ext = ".jpg"
            if content.startswith(b"\x89PNG"):
                ext = ".png"
            elif b"WEBP" in content[:20]:
                ext = ".webp"

            # Image path: write to disk if writable, otherwise use lightweight thumbnail data URL (~12KB)
            image_ref = ""
            if can_save_disk:
                unique_name = f"{uuid.uuid4().hex}{ext}"
                save_path = os.path.join(UPLOADS_DIR, unique_name)
                try:
                    with open(save_path, "wb") as f:
                        f.write(content)
                    image_ref = f"/static/uploads/{unique_name}"
                except Exception:
                    image_ref = make_thumbnail_data_url(content)
            else:
                image_ref = make_thumbnail_data_url(content)

            # Analyze slip in-memory directly
            filename = file.filename or "image"
            analysis = parser.analyze_slip(content, filename, api_key_override=client_api_key)
            
            if not analysis.get("is_slip", True):
                ignored.append({
                    "filename": filename,
                    "reason": analysis.get("reason", "ไม่ใช่สลิปการเงิน")
                })
                continue

            data = analysis.get("data", {})
            data["image_path"] = image_ref
            data["original_filename"] = filename
            data["engine"] = analysis.get("engine", "Unknown")

            # Check duplicate in database or in current batch
            ref_id = data.get("ref_id", "")
            amount = db.sanitize_amount(data.get("amount", 0.0))
            data["amount"] = amount
            date_time = data.get("date_time", "")
            bank = data.get("bank", "")
            receiver = data.get("receiver_name", "")

            dup = db.check_duplicate_transaction(
                ref_id=ref_id,
                amount=amount,
                date_time=date_time,
                bank=bank,
                receiver_name=receiver
            )

            fingerprint = f"{amount:.2f}_{date_time[:10] if date_time else ''}_{bank}"
            is_batch_dup = fingerprint in batch_fingerprints
            batch_fingerprints.add(fingerprint)

            if dup:
                data["is_duplicate"] = True
                data["duplicate_reason"] = f"พบสลิปซ้ำ (เคยบันทึกแล้ว ยอด ฿{dup['amount']:,.2f} วันที่ {dup.get('date_time', '')[:10]})"
            elif is_batch_dup:
                data["is_duplicate"] = True
                data["duplicate_reason"] = "พบสลิปซ้ำ (มีรูปสลิปนี้ซ้ำในชุดที่อัปโหลด)"
            else:
                data["is_duplicate"] = False

            results.append(data)
        except Exception as e:
            print(f"[Upload Error on {getattr(file, 'filename', 'unknown')}]: {e}")
            ignored.append({
                "filename": getattr(file, "filename", "unknown"),
                "reason": f"เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"
            })

    return {
        "status": "success",
        "total_scanned": len(files),
        "slips_found": len(results),
        "ignored_count": len(ignored),
        "items": results,
        "ignored": ignored
    }

@app.post("/api/save-transaction")
def save_transaction(item: Dict[str, Any]):
    tx_id = db.add_transaction(item)
    return {"status": "success", "id": tx_id}

@app.post("/api/save-batch-transactions")
def save_batch_transactions(items: List[Dict[str, Any]]):
    saved_ids = []
    for item in items:
        tx_id = db.add_transaction(item)
        saved_ids.append(tx_id)
    return {"status": "success", "count": len(saved_ids), "ids": saved_ids}

@app.post("/api/transactions/clear")
@app.delete("/api/transactions")
def clear_all_transactions():
    deleted_count = db.clear_all_transactions()
    return {"status": "success", "deleted_count": deleted_count}

@app.get("/api/months")
def get_available_months():
    months = db.get_available_months()
    return {"months": months}

@app.get("/api/transactions")
def list_transactions(
    category: Optional[str] = None,
    bank: Optional[str] = None,
    search: Optional[str] = None,
    month: Optional[str] = None
):
    transactions = db.get_transactions(category=category, bank=bank, search=search, month=month)
    return {"transactions": transactions}

@app.get("/api/transactions/{tx_id}")
def get_transaction(tx_id: int):
    tx = db.get_transaction_by_id(tx_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return tx

@app.put("/api/transactions/{tx_id}")
def update_transaction(tx_id: int, item: Dict[str, Any]):
    success = db.update_transaction(tx_id, item)
    return {"status": "success" if success else "failed"}

@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int):
    success = db.delete_transaction(tx_id)
    return {"status": "success" if success else "failed"}

@app.get("/api/stats")
def get_stats(month: Optional[str] = None):
    return db.get_statistics(month=month)

@app.post("/api/seed-samples")
def seed_samples():
    """Seeds the 4 sample slips provided by the user."""
    sample_records = [
        {
            "bank": "ttb (ทีเอ็มบีธนชาต)",
            "transaction_type": "เติมเงิน",
            "sender_name": "นาย เสฏฐพงศ์ เลิศสกุลบรรลือ",
            "sender_account": "XXX-X-XX189-4",
            "receiver_name": "ทรู มันนี่ [เติมเงิน]",
            "receiver_account": "0863650009",
            "amount": 1000.0,
            "fee": 0.0,
            "date_time": "2026-10-24 17:30:00",
            "ref_id": "261024173043899666",
            "category": "📲 เติมเงิน & วอลเล็ต",
            "note": "เติมเงินเข้า TrueMoney Wallet",
            "image_path": "/static/samples/ttb_truemoney_1000.jpg"
        },
        {
            "bank": "ธนาคารกสิกรไทย (K+)",
            "transaction_type": "จ่ายบิล",
            "sender_name": "นาย เสฏฐพงศ์ เ***",
            "sender_account": "xxx-x-x9088-x",
            "receiver_name": "บัตรวันสยามซิกเนเจอร์",
            "receiver_account": "4417-70xx-xxxx-5693",
            "amount": 6790.0,
            "fee": 0.0,
            "date_time": "2026-10-24 14:05:00",
            "ref_id": "016297140545DPM11409",
            "category": "💳 ชำระบิล/บัตรเครดิต",
            "note": "ชำระค่าบัตรเครดิตวันสยาม ซิกเนเจอร์",
            "image_path": "/static/samples/kbank_onesiam_6790.jpg"
        },
        {
            "bank": "เป๋าตัง (GLO)",
            "transaction_type": "ซื้อสลากดิจิทัล",
            "sender_name": "G Wallet",
            "sender_account": "006 XXXXXXXX 3888",
            "receiver_name": "ร้านค้าสลากฯ",
            "receiver_account": "GLO Digital 4 ใบ",
            "amount": 320.0,
            "fee": 0.0,
            "date_time": "2026-10-25 15:17:33",
            "ref_id": "81872069162531711792916253",
            "category": "🎟️ เสี่ยงโชค & บันเทิง",
            "note": "ซื้อสลากหกหลักดิจิทัล 4 ใบ (งวด 1 พ.ย. 69)",
            "image_path": "/static/samples/glo_lottery_320.jpg"
        },
        {
            "bank": "ธนาคารกรุงไทย (Krungthai NEXT)",
            "transaction_type": "จ่ายบิลร้านค้า",
            "sender_name": "นาย เสฏฐพงศ์ เ***",
            "sender_account": "XXX-X-XX554-4",
            "receiver_name": "JONES SALAD-SAMITIVEJ SRI",
            "receiver_account": "010753600031501",
            "amount": 40.0,
            "fee": 0.0,
            "date_time": "2026-10-25 14:37:00",
            "ref_id": "C20261025629814650554",
            "category": "🍔 อาหารและเครื่องดื่ม",
            "note": "สลัดโจนส์ สมิติเวช ศรีนครินทร์",
            "image_path": "/static/samples/krungthai_jonessalad_40.jpg"
        }
    ]
    for rec in sample_records:
        db.add_transaction(rec)
    return {"status": "success", "message": "โหลด 4 สลิปตัวอย่างเรียบร้อย", "samples": sample_records}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
