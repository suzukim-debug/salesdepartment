"""
FastAPI ダッシュボード
- リード一覧・スコアリング
- ホットリード管理（反響あり）
- メール送信・AI生成
- 架電ログ記録
- トラッキングエンドポイント
"""
import json
import io
from datetime import datetime, date
from typing import Optional

from fastapi import FastAPI, Depends, Request, Form, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from database.db import get_db, init_db
from database.models import Lead, OutreachLog, CallLog, LeadStatus, ServiceType
from lead_generator.csv_importer import import_from_csv, get_csv_template
from lead_generator.scorer import score_lead
from outreach.template_generator import generate_email
from outreach.email_sender import send_outreach_email, create_outreach_log
from tracker.response_tracker import record_open, record_click, record_form_submit, record_unsubscribe
from config import settings

app = FastAPI(title="Sales Outreach System")
templates = Jinja2Templates(directory="dashboard/templates")

init_db()


# ─── ヘルパー ────────────────────────────────────────────────

def _get_stats(db: Session) -> dict:
    total = db.query(func.count(Lead.id)).scalar()
    hot = db.query(func.count(Lead.id)).filter(Lead.status == LeadStatus.RESPONDED).scalar()
    sent = db.query(func.count(Lead.id)).filter(Lead.status.in_([
        LeadStatus.EMAIL_SENT, LeadStatus.OPENED, LeadStatus.CLICKED, LeadStatus.RESPONDED
    ])).scalar()
    meetings = db.query(func.count(Lead.id)).filter(Lead.status == LeadStatus.MEETING_SET).scalar()
    return {"total": total, "hot": hot, "sent": sent, "meetings": meetings}


# ─── ダッシュボード TOP ─────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = _get_stats(db)
    hot_leads = (
        db.query(Lead)
        .filter(Lead.status == LeadStatus.RESPONDED)
        .order_by(desc(Lead.lead_score))
        .limit(10)
        .all()
    )
    recent_leads = (
        db.query(Lead)
        .order_by(desc(Lead.created_at))
        .limit(5)
        .all()
    )
    return templates.TemplateResponse("index.html", {
        "request": request, "stats": stats,
        "hot_leads": hot_leads, "recent_leads": recent_leads,
    })


# ─── リード管理 ─────────────────────────────────────────────

@app.get("/leads", response_class=HTMLResponse)
async def leads_list(
    request: Request,
    status: Optional[str] = None,
    industry: Optional[str] = None,
    min_score: float = 0,
    search: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    q = db.query(Lead)
    if status:
        q = q.filter(Lead.status == status)
    if industry:
        q = q.filter(Lead.industry.contains(industry))
    if min_score > 0:
        q = q.filter(Lead.lead_score >= min_score)
    if search:
        q = q.filter(Lead.company_name.contains(search))

    total_count = q.count()
    per_page = 30
    leads = q.order_by(desc(Lead.lead_score)).offset((page - 1) * per_page).limit(per_page).all()

    return templates.TemplateResponse("leads.html", {
        "request": request, "leads": leads,
        "total_count": total_count, "page": page, "per_page": per_page,
        "current_status": status, "current_industry": industry,
        "min_score": min_score, "search": search,
        "statuses": [s.value for s in LeadStatus],
    })


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail(request: Request, lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "リードが見つかりません")
    return templates.TemplateResponse("lead_detail.html", {
        "request": request, "lead": lead,
        "statuses": [s.value for s in LeadStatus],
    })


@app.post("/leads/{lead_id}/status")
async def update_lead_status(
    lead_id: int,
    status: str = Form(...),
    memo: str = Form(""),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    lead.status = LeadStatus(status)
    if memo:
        lead.memo = memo
    db.commit()
    return RedirectResponse(f"/leads/{lead_id}", status_code=303)


@app.post("/leads/import")
async def import_leads(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    result = import_from_csv(io.BytesIO(content), db)
    db.commit()
    return JSONResponse(result)


@app.get("/leads/export/template")
async def download_template():
    content = get_csv_template()
    return Response(
        content=content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lead_template.csv"},
    )


# ─── メール送信 ─────────────────────────────────────────────

@app.get("/outreach", response_class=HTMLResponse)
async def outreach_page(request: Request, db: Session = Depends(get_db)):
    # メール送信対象: NEW/RESEARCHED でスコア40以上
    targets = (
        db.query(Lead)
        .filter(
            Lead.status.in_([LeadStatus.NEW, LeadStatus.RESEARCHED]),
            Lead.lead_score >= 40,
            Lead.contact_email.isnot(None),
        )
        .order_by(desc(Lead.lead_score))
        .all()
    )
    sent_logs = (
        db.query(OutreachLog)
        .order_by(desc(OutreachLog.sent_at))
        .limit(20)
        .all()
    )
    return templates.TemplateResponse("outreach.html", {
        "request": request, "targets": targets, "sent_logs": sent_logs,
    })


@app.post("/outreach/preview")
async def preview_email(
    lead_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """AI生成メールのプレビューを返す"""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    try:
        result = generate_email(lead)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/outreach/send")
async def send_email(
    lead_id: int = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    body_html: str = Form(...),
    service_type: str = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)

    try:
        stype = ServiceType(service_type)
        send_outreach_email(db, lead, subject, body, body_html, stype)
        db.commit()
        return JSONResponse({"success": True})
    except Exception as e:
        db.rollback()
        raise HTTPException(500, str(e))


@app.post("/outreach/save-draft")
async def save_draft(
    lead_id: int = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    body_html: str = Form(...),
    service_type: str = Form(...),
    db: Session = Depends(get_db),
):
    """送信せずに下書き保存"""
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)
    stype = ServiceType(service_type)
    create_outreach_log(db, lead, subject, body, body_html, stype)
    db.commit()
    return JSONResponse({"success": True})


# ─── ホットリード & 架電管理 ─────────────────────────────────

@app.get("/hot-leads", response_class=HTMLResponse)
async def hot_leads_page(request: Request, db: Session = Depends(get_db)):
    hot = (
        db.query(Lead)
        .filter(Lead.status.in_([
            LeadStatus.RESPONDED, LeadStatus.CLICKED, LeadStatus.OPENED,
            LeadStatus.CALLING, LeadStatus.CONNECTED,
        ]))
        .order_by(desc(Lead.lead_score))
        .all()
    )
    return templates.TemplateResponse("hot_leads.html", {
        "request": request, "hot_leads": hot,
        "statuses": [s.value for s in LeadStatus],
    })


@app.post("/calls/log")
async def log_call(
    lead_id: int = Form(...),
    caller_name: str = Form(...),
    result: str = Form(...),
    next_action: str = Form(""),
    next_action_date: Optional[str] = Form(None),
    notes: str = Form(""),
    duration_seconds: int = Form(0),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404)

    next_dt = None
    if next_action_date:
        try:
            next_dt = datetime.strptime(next_action_date, "%Y-%m-%dT%H:%M")
        except ValueError:
            pass

    call = CallLog(
        lead_id=lead_id,
        caller_name=caller_name,
        result=result,
        next_action=next_action,
        next_action_date=next_dt,
        notes=notes,
        duration_seconds=duration_seconds,
    )
    db.add(call)

    # ステータス更新
    status_map = {
        "アポ獲得": LeadStatus.MEETING_SET,
        "お断り": LeadStatus.REJECTED,
        "繋がった": LeadStatus.CONNECTED,
        "再架電": LeadStatus.CALLING,
        "折り返し": LeadStatus.CALLING,
    }
    new_status = status_map.get(result)
    if new_status:
        lead.status = new_status

    db.commit()
    return JSONResponse({"success": True})


# ─── トラッキングエンドポイント ───────────────────────────────

@app.get("/track/open/{token}")
async def track_open(token: str, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    record_open(db, token, ip, ua)
    # 1x1透過GIF
    gif = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    return Response(content=gif, media_type="image/gif")


@app.get("/track/click/{token}")
async def track_click(
    token: str, request: Request,
    url: str = Query(...),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else ""
    ua = request.headers.get("user-agent", "")
    redirect_url = record_click(db, token, url, ip, ua)
    return RedirectResponse(redirect_url)


@app.get("/form/{token}", response_class=HTMLResponse)
async def inquiry_form(token: str, request: Request):
    return templates.TemplateResponse("inquiry_form.html", {
        "request": request, "token": token,
    })


@app.post("/form/{token}/submit")
async def submit_form(
    token: str, request: Request,
    name: str = Form(...),
    company: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    message: str = Form(""),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else ""
    form_data = {"name": name, "company": company, "email": email, "phone": phone, "message": message}
    record_form_submit(db, token, form_data, ip)
    return templates.TemplateResponse("form_thanks.html", {"request": request})


@app.get("/unsubscribe/{token}")
async def unsubscribe(token: str, db: Session = Depends(get_db)):
    record_unsubscribe(db, token)
    return HTMLResponse("<html><body><p>配信停止しました。ご迷惑をおかけして申し訳ございません。</p></body></html>")


# ─── API (JSON) ─────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats(db: Session = Depends(get_db)):
    return _get_stats(db)


@app.get("/api/leads/hot")
async def api_hot_leads(db: Session = Depends(get_db)):
    hot = (
        db.query(Lead)
        .filter(Lead.status == LeadStatus.RESPONDED)
        .order_by(desc(Lead.lead_score))
        .all()
    )
    return [
        {
            "id": l.id, "company_name": l.company_name, "industry": l.industry,
            "contact_name": l.contact_name, "contact_phone": l.contact_phone or l.company_phone,
            "lead_score": l.lead_score, "status": l.status,
            "recommended_service": l.recommended_service,
        }
        for l in hot
    ]
