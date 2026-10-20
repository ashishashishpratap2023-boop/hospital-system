"""Public, login-free pages: the hospital card patients open from the WhatsApp link, and the logo image."""
import base64
import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Tenant

router = APIRouter(include_in_schema=False)

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#eef4f3;color:#0f172a;min-height:100vh}
.top{background:linear-gradient(135deg,#0f766e,#14b8a6);padding:36px 20px 70px;text-align:center;color:#fff}
.logo{width:104px;height:104px;border-radius:50%;background:#fff;margin:0 auto 14px;display:flex;align-items:center;justify-content:center;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.18);font-size:44px}
.logo img{width:100%;height:100%;object-fit:contain}
h1{margin:0;font-size:24px}.sub{margin:6px 0 0;opacity:.92;font-size:15px}
.wrap{max-width:440px;margin:-44px auto 30px;padding:0 16px}
.card{background:#fff;border-radius:20px;padding:18px;box-shadow:0 10px 30px rgba(15,23,42,.09)}
.card h2{margin:0 0 4px;font-size:17px}.hint{margin:0 0 14px;color:#64748b;font-size:14px}
a.btn{display:flex;align-items:center;gap:14px;padding:13px 14px;margin-top:10px;border-radius:14px;text-decoration:none;color:#0f172a;background:#f8fafc;border:1px solid #e2e8f0;font-weight:600;transition:transform .12s}
a.btn:active{transform:scale(.98)}a.btn small{display:block;font-weight:400;color:#64748b;font-size:12px}
.ic{width:42px;height:42px;border-radius:12px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:18px;flex:none;background:var(--c)}
a.hot{background:#fffbeb;border-color:#fde68a}
.foot{text-align:center;color:#64748b;font-size:13px;margin-top:16px;line-height:1.5}
@media(prefers-color-scheme:dark){body{background:#0b1220;color:#e5e7eb}.card{background:#111827}a.btn{background:#1f2937;border-color:#374151;color:#f3f4f6}a.hot{background:#2a2410;border-color:#6b5a1a}.hint,.foot,a.btn small{color:#9ca3af}}
"""


def _tenant(db: Session, slug: str) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t or t.status != "active" or not t.settings:
        raise HTTPException(404, "Not found")
    return t


def _safe(url) -> str:
    url = (url or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def build_page(name, branding, base, slug):
    q = lambda v: html.escape(str(v or ""), quote=True)
    b = branding or {}
    soc = b.get("social") or {}
    has_logo = bool(b.get("logo_b64"))
    logo = f"{base}/logo/{q(slug)}"
    phone = "".join(ch for ch in (b.get("phone") or "") if ch.isdigit() or ch == "+")
    items = [  # (link, colour, badge, title, subtitle, hot)
        (_safe(soc.get("google_reviews")), "#f59e0b", "&#9733;", "Rate us on Google", "Share your experience, it helps other patients", True),
        (_safe(soc.get("instagram")), "#e1306c", "IG", "Follow on Instagram", "Health tips and updates", False),
        (_safe(soc.get("facebook")), "#1877f2", "f", "Like us on Facebook", "Stay connected with our page", False),
        (_safe(soc.get("youtube")), "#ff0000", "&#9654;", "Subscribe on YouTube", "Watch our health videos", False),
        (_safe(soc.get("twitter")), "#111827", "X", "Follow on X", "Latest announcements", False),
        (_safe(b.get("website")), "#0f766e", "W", "Visit our website", "Services and doctors", False),
        (_safe(b.get("maps_url")), "#16a34a", "&#9906;", "Get directions", "Open in Google Maps", False),
        (f"tel:{phone}" if phone else "", "#0ea5e9", "&#9742;", "Call the hospital", q(b.get("phone")), False),
    ]
    btns = "".join(
        f'<a class="btn{" hot" if hot else ""}" style="--c:{c}" href="{q(l)}" target="_blank" rel="noopener">'
        f'<span class="ic">{ic}</span><span>{t}<small>{s}</small></span></a>'
        for l, c, ic, t, s, hot in items if l)
    addr = f'<p class="foot">{q(b.get("address"))}</p>' if b.get("address") else ""
    img = f'<img src="{logo}" alt="{q(name)} logo">' if has_logo else "&#10010;"
    og_img = f'<meta property="og:image" content="{logo}">' if has_logo else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{q(name)}</title><meta property="og:title" content="{q(name)}"><meta property="og:type" content="website">
<meta property="og:description" content="Follow us on social media and rate your visit on Google.">{og_img}
<style>{CSS}</style></head><body>
<div class="top"><div class="logo">{img}</div><h1>{q(name)}</h1><p class="sub">Thank you for choosing us. We wish you good health.</p></div>
<div class="wrap"><div class="card"><h2>Stay connected with us</h2><p class="hint">Follow our pages and tell us how we did.</p>{btns or '<p class="hint">Links will appear here soon.</p>'}</div>{addr}</div>
</body></html>"""


@router.get("/h/{slug}", response_class=HTMLResponse)
def hospital_card(slug: str, request: Request, db: Session = Depends(get_db)):
    t = _tenant(db, slug)
    base = str(request.base_url).rstrip("/")
    if "localhost" not in base and "127.0.0.1" not in base:
        base = base.replace("http://", "https://", 1)
    return HTMLResponse(build_page(t.name, t.settings.branding, base, slug),
                        headers={"Cache-Control": "public, max-age=120"})


@router.get("/logo/{slug}")
def logo(slug: str, db: Session = Depends(get_db)):
    b = _tenant(db, slug).settings.branding or {}
    if not b.get("logo_b64"):
        raise HTTPException(404, "No logo")
    return Response(base64.b64decode(b["logo_b64"]), media_type=b.get("logo_mime", "image/png"),
                    headers={"Cache-Control": "public, max-age=300"})
