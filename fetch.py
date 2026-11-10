"""
זמן ימין - פיד טלגרם לסוכן החדשות.

מושך את הפוסטים מהשעות האחרונות מכל הערוצים ברשימה, עם תאריך ושעה מדויקים,
צפיות, שיתופים, תגובות (ריאקציות), טקסט מלא ופרטי מדיה, וכותב אותם לקובץ feed.json.
הסוכן של Claude קורא את הקובץ הזה במקום לפתוח את דפי הטלגרם.

משתני סביבה (מוגדרים כ-Secrets ב-GitHub):
  TG_API_ID, TG_API_HASH  - מ-my.telegram.org
  TG_SESSION              - מחרוזת ההתחברות שנוצרה פעם אחת עם login.py
"""
import asyncio
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
)

# הערוצים שהסוכן עוקב אחריהם. אפשר להוסיף או להוריד שמות כאן.
CHANNELS = [
    "zmanyemin1",
    "amitsegal",
    "abualiexpress",
    "MichaelShemesh",
    "lieldaphna",
    "tzviyeh",
    "realelchangr",
    "elishayered",
    "hakolhayehudi",
    "alexmehacarmel",
    "Israel_Police_100",
]

# תיקיית טלגרם: כל ערוץ שנמצא בתיקייה בשם הזה בחשבון נכנס לפיד אוטומטית.
# כדי להוסיף ערוץ, פשוט מוסיפים אותו לתיקייה באפליקציית טלגרם.
FOLDER_NAME = "זמן ימין"

HOURS_BACK = 24          # כמה שעות אחורה לשמור בפיד
MAX_PER_CHANNEL = 150    # תקרה לכל ערוץ בכל הרצה
TEXT_LIMIT = 4000        # אורך מקסימלי של טקסט פוסט
IL = ZoneInfo("Asia/Jerusalem")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed.json")

# פיד נפרד לניתוחי המשחקים של בית"ר: כל הערוצים בתיקיית הטלגרם "ביתר" נכתבים ל-beitar.json.
BEITAR_FOLDER = "ביתר"
BEITAR_HOURS_BACK = 96      # 4 ימים אחורה, כדי לכסות לפני המשחק, המשחק עצמו והתגובות אחריו
BEITAR_MAX_PER_CHANNEL = 400
BEITAR_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "beitar.json")


def media_info(msg):
    """מחזיר סוג מדיה ואורך וידאו בשניות, אם יש."""
    m = msg.media
    if m is None:
        return "none", None
    if isinstance(m, MessageMediaPhoto):
        return "photo", None
    if isinstance(m, MessageMediaDocument) and m.document:
        for a in m.document.attributes:
            if isinstance(a, DocumentAttributeVideo):
                return "video", int(a.duration or 0)
            if isinstance(a, DocumentAttributeAudio):
                return ("voice" if a.voice else "audio"), int(a.duration or 0)
        mime = getattr(m.document, "mime_type", "") or ""
        if mime.startswith("video"):
            return "video", None
        if mime.startswith("image"):
            return "photo", None
        return "file", None
    if isinstance(m, MessageMediaWebPage):
        return "link", None
    return "other", None


async def folder_entities(client, name):
    """מחזיר את הערוצים שבתיקיית הטלגרם בשם name (רשימה ריקה אם אין תיקייה כזו)."""
    res = await client(GetDialogFiltersRequest())
    filters = getattr(res, "filters", res) or []
    out = []
    for f in filters:
        title = getattr(f, "title", None)
        title = getattr(title, "text", title)  # בגרסאות חדשות הכותרת היא אובייקט
        if not title or title.strip() != name:
            continue
        for peer in list(getattr(f, "pinned_peers", []) or []) + list(getattr(f, "include_peers", []) or []):
            try:
                out.append(await client.get_entity(peer))
            except Exception:
                pass
    return out


def reactions_total(msg):
    r = getattr(msg, "reactions", None)
    if not r or not getattr(r, "results", None):
        return 0
    return sum(x.count for x in r.results)


async def collect(client, targets, since, now, limit, errors):
    """מושך פוסטים מרשימת ערוצים עד since, ומחזיר רשימת פוסטים."""
    posts = []
    for entity in targets:
        ch = getattr(entity, "username", None) or f"c/{entity.id}"
        try:
            title = getattr(entity, "title", ch)
            grouped_seen = set()
            async for msg in client.iter_messages(entity, limit=limit):
                if msg.date < since:
                    break
                if msg.action is not None:  # הודעות מערכת (הצמדה וכו')
                    continue
                # אלבום: כמה פריטים עם אותו grouped_id. שומרים רק את הראשון שיש לו טקסט.
                if msg.grouped_id:
                    if msg.grouped_id in grouped_seen:
                        continue
                    if not (msg.message or "").strip():
                        continue
                    grouped_seen.add(msg.grouped_id)
                mtype, dur = media_info(msg)
                age_h = max((now - msg.date).total_seconds() / 3600, 0.25)
                views = msg.views or 0
                posts.append({
                    "channel": ch,
                    "channel_title": title,
                    "post_id": msg.id,
                    "link": f"https://t.me/{ch}/{msg.id}",
                    "date_utc": msg.date.strftime("%Y-%m-%d %H:%M"),
                    "date_israel": msg.date.astimezone(IL).strftime("%Y-%m-%d %H:%M"),
                    "age_hours": round(age_h, 2),
                    "views": views,
                    "views_per_hour": int(views / age_h),
                    "forwards": msg.forwards or 0,
                    "reactions": reactions_total(msg),
                    "replies": (msg.replies.replies if msg.replies else 0),
                    "media": mtype,
                    "video_seconds": dur,
                    "is_album": bool(msg.grouped_id),
                    "edited": msg.edit_date is not None,
                    "text": (msg.message or "")[:TEXT_LIMIT],
                })
        except Exception as e:  # ערוץ שנכשל לא עוצר את השאר
            errors.append({"channel": ch, "error": f"{type(e).__name__}: {e}"})

    return posts


def add_channel_avg(posts):
    # ממוצע צפיות לשעה לכל ערוץ, כדי להשוות כל פוסט לרמה של הערוץ שלו
    by_ch = {}
    for p in posts:
        by_ch.setdefault(p["channel"], []).append(p["views_per_hour"])
    for p in posts:
        vals = by_ch[p["channel"]]
        avg = sum(vals) / len(vals) if vals else 0
        p["vs_channel_avg"] = round(p["views_per_hour"] / avg, 2) if avg else None


async def main():
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = os.environ["TG_SESSION"]

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=HOURS_BACK)
    posts, errors = [], []

    async with TelegramClient(StringSession(session), api_id, api_hash) as client:
        targets, seen_ids = [], set()
        for ch in CHANNELS:
            try:
                e = await client.get_entity(ch)
            except Exception as ex:
                errors.append({"channel": ch, "error": f"{type(ex).__name__}: {ex}"})
                continue
            if e.id not in seen_ids:
                seen_ids.add(e.id); targets.append(e)
        folder_count = 0
        try:
            for e in await folder_entities(client, FOLDER_NAME):
                if e.id not in seen_ids and hasattr(e, "title"):  # רק ערוצים וקבוצות, לא אנשים
                    seen_ids.add(e.id); targets.append(e); folder_count += 1
        except Exception as ex:
            errors.append({"channel": f"folder:{FOLDER_NAME}", "error": f"{type(ex).__name__}: {ex}"})

        posts += await collect(client, targets, since, now, MAX_PER_CHANNEL, errors)

        # ---- פיד בית"ר ----
        b_since = now - timedelta(hours=BEITAR_HOURS_BACK)
        b_posts, b_errors, b_targets = [], [], []
        try:
            b_targets = [e for e in await folder_entities(client, BEITAR_FOLDER) if hasattr(e, "title")]
        except Exception as ex:
            b_errors.append({"channel": f"folder:{BEITAR_FOLDER}", "error": f"{type(ex).__name__}: {ex}"})
        b_posts = await collect(client, b_targets, b_since, now, BEITAR_MAX_PER_CHANNEL, b_errors)

    add_channel_avg(posts)

    posts.sort(key=lambda p: p["date_utc"], reverse=True)
    out = {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M"),
        "generated_israel": now.astimezone(IL).strftime("%Y-%m-%d %H:%M"),
        "hours_back": HOURS_BACK,
        "channels": sorted({p["channel"] for p in posts}),
        "folder": FOLDER_NAME,
        "folder_channels_added": folder_count,
        "post_count": len(posts),
        "errors": errors,
        "posts": posts,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(posts)} posts, {len(errors)} errors")

    add_channel_avg(b_posts)
    b_posts.sort(key=lambda p: p["date_utc"], reverse=True)
    b_out = {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M"),
        "generated_israel": now.astimezone(IL).strftime("%Y-%m-%d %H:%M"),
        "hours_back": BEITAR_HOURS_BACK,
        "folder": BEITAR_FOLDER,
        "channels": sorted({p["channel"] for p in b_posts}),
        "folder_channel_count": len(b_targets),
        "post_count": len(b_posts),
        "errors": b_errors,
        "posts": b_posts,
    }
    with open(BEITAR_OUT, "w", encoding="utf-8") as f:
        json.dump(b_out, f, ensure_ascii=False, indent=1)
    print(f"beitar: wrote {len(b_posts)} posts from {len(b_targets)} channels, {len(b_errors)} errors")
    # שלב ה-Commit ב-workflow מוסיף רק את feed.json, אז מוסיפים כאן גם את beitar.json
    subprocess.run(["git", "add", BEITAR_OUT], check=False)


if __name__ == "__main__":
    asyncio.run(main())
