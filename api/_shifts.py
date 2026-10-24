"""
סידור עבודה מתמונה → משמרות של מאור ביומן, אחרי אישור.
ה-AI רק מאתר את התאים עם השם; את השעות מחשב הקוד לפי הכללים.
"""

import re
from datetime import date, datetime, timedelta

from _core import CALENDAR_ID, IL_TZ, ask_ai, calendar, edit, esc, send

DAYS_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]
TAG = {"yossi": "shift"}  # מסמן משמרות שיוסי הכניס, כדי להחליף אותן כששולחים סידור מתוקן

REGULAR = {"בוקר": ("07:00", "15:00"), "צהריים": ("15:00", "23:00"), "לילה": ("23:00", "07:00")}
TWELVE = {"בוקר": ("07:00", "19:00"), "לילה": ("19:00", "07:00")}  # יום עם 12/12 באדום

VISION_PROMPT = """This image was sent by Maor. If it is a weekly work schedule (a table of days × shifts × positions,
with employee names in the cells), find EVERY cell that contains the name "מאור פורמן"
(also written "פורמן מאור", or partly cut off).

For each such cell return:
- date: the date in that cell's day header, as DD/MM/YYYY
- shift: that cell's sub-column header — exactly one of "בוקר", "צהריים", "לילה"
- position: that cell's row label on the right side, copied exactly (e.g. "מאבטח 1 אקדח", "שיפוצים 7:00-17:00")
- twelve: true only if, in the SAME row and SAME day, the "צהריים" cell shows a red "12/12"

Be very careful to map each cell to the correct day and sub-column: the table is right-to-left, and each day has
three sub-columns (בוקר on the right, צהריים in the middle, לילה on the left). Never guess; if the name is not in the
table, return an empty list.

Return ONLY JSON: {"is_schedule": true|false, "shifts": [{"date": "...", "shift": "...", "position": "...", "twelve": false}]}"""

# ── שעות ──────────────────────────────────────────────────────────────────────

def _add_hours(hhmm: str, hours: int) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h + hours) % 24:02d}:{m:02d}"

def shift_hours(position: str, shift: str, twelve: bool) -> tuple:
    hours = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", position)
    if hours:  # שעות כתובות בשורה, למשל "שיפוצים 7:00-17:00"
        h1, m1, h2, m2 = hours.groups()
        return f"{int(h1):02d}:{m1}", f"{int(h2):02d}:{m2}"
    start, end = (TWELVE if twelve and shift in TWELVE else REGULAR)[shift]
    if "רמש" in re.sub(r"[\"״׳']", "", position):  # רמ"ש מתחיל שעה מוקדם יותר
        start, end = _add_hours(start, -1), _add_hours(end, -1)
    return start, end

def make_event(day: date, shift: str, start: str, end: str, position: str) -> dict:
    s = datetime.combine(day, datetime.strptime(start, "%H:%M").time(), IL_TZ)
    e = datetime.combine(day, datetime.strptime(end, "%H:%M").time(), IL_TZ)
    if e <= s:
        e += timedelta(days=1)  # משמרת לילה נגמרת למחרת
    return {"start": s, "end": e, "shift": shift, "position": position}

def week_start(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)  # יום ראשון

# ── תצוגה מקדימה — הטקסט שלה הוא גם המקור לאישור (בלי לשמור מצב בשרת) ─────────

def line(ev: dict) -> str:
    return f"🛡️ {DAYS_HE[ev['start'].weekday()]} {ev['start']:%d/%m} · {ev['shift']} · " \
           f"{ev['start']:%H:%M}–{ev['end']:%H:%M} · {ev['position']}"

LINE_RE = re.compile(r"(\d{2})/(\d{2}) · (.+?) · (\d{2}:\d{2})–(\d{2}:\d{2}) · (.+)$")

def parse_preview(text: str, ws: date) -> list:
    events = []
    for row in text.splitlines():
        m = LINE_RE.search(row)
        if not row.startswith("🛡") or not m:
            continue
        d, mo, shift, start, end, position = m.groups()
        year = ws.year + (1 if int(mo) < ws.month else 0)  # שבוע שחוצה את סוף דצמבר
        events.append(make_event(date(year, int(mo), int(d)), shift, start, end, position.strip()))
    return events

def _key(ev: dict) -> tuple:
    return ev["start"].isoformat(), ev["end"].isoformat(), ev["position"]

def diff(old: list, new: list) -> list:
    if not old:
        return []
    old_keys, new_keys = {_key(e): e for e in old}, {_key(e): e for e in new}
    if old_keys.keys() == new_keys.keys():
        return ["✔️ זהה לסידור שכבר ביומן"]
    return [f"➕ נוסף: {esc(line(e)[3:])}" for k, e in new_keys.items() if k not in old_keys] + \
           [f"➖ בוטל: {esc(line(e)[3:])}" for k, e in old_keys.items() if k not in new_keys]

def preview_text(events: list, ws: date, changes: list, conflicts: list) -> str:
    out = [f"📋 <b>מצאתי {len(events)} משמרות שלך ({ws:%d/%m}–{ws + timedelta(days=6):%d/%m}):</b>",
           *[esc(line(e)) for e in events]]
    if changes:
        out += ["", "🔄 <b>מה השתנה מהסידור שביומן:</b>", *changes]
    if conflicts:
        out += ["", "⚠️ <b>התנגשויות ביומן:</b>", *conflicts]
    return "\n".join(out)

# ── Google Calendar ───────────────────────────────────────────────────────────

def _bounds(ws: date) -> tuple:
    start = datetime.combine(ws, datetime.min.time(), IL_TZ)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()

def existing_shifts(ws: date) -> list:
    t_min, t_max = _bounds(ws)
    items = calendar().events().list(
        calendarId=CALENDAR_ID, timeMin=t_min, timeMax=t_max, singleEvents=True,
        privateExtendedProperty="yossi=shift",
    ).execute().get("items", [])
    out = []
    for it in items:
        ev = {
            "id": it["id"],
            "start": datetime.fromisoformat(it["start"]["dateTime"]).astimezone(IL_TZ),
            "end": datetime.fromisoformat(it["end"]["dateTime"]).astimezone(IL_TZ),
            "shift": it.get("extendedProperties", {}).get("private", {}).get("shift", ""),
            "position": it.get("extendedProperties", {}).get("private", {}).get("position", ""),
        }
        if ev["start"] >= datetime.fromisoformat(t_min):  # רק משמרות שמתחילות בשבוע הזה
            out.append(ev)
    return out

def conflicts(events: list) -> list:
    out = []
    for ev in events:
        items = calendar().events().list(
            calendarId=CALENDAR_ID, timeMin=ev["start"].isoformat(), timeMax=ev["end"].isoformat(), singleEvents=True,
        ).execute().get("items", [])
        for it in items:
            if "dateTime" not in it.get("start", {}) or it.get("extendedProperties", {}).get("private", {}).get("yossi"):
                continue  # אירוע של כל היום או משמרת קודמת שתוחלף
            s = datetime.fromisoformat(it["start"]["dateTime"]).astimezone(IL_TZ)
            e = datetime.fromisoformat(it["end"]["dateTime"]).astimezone(IL_TZ)
            out.append(f"• {DAYS_HE[ev['start'].weekday()]} {ev['start']:%d/%m} ({ev['start']:%H:%M}–{ev['end']:%H:%M}) "
                       f"מתנגש עם: {esc(it.get('summary', '(ללא שם)'))} {s:%H:%M}–{e:%H:%M}")
    return out

def replace_week(ws: date, events: list) -> None:
    service = calendar()
    for old in existing_shifts(ws):
        service.events().delete(calendarId=CALENDAR_ID, eventId=old["id"]).execute()
    for ev in events:
        service.events().insert(calendarId=CALENDAR_ID, body={
            "summary": f"🛡️ משמרת {ev['shift']} — {ev['position']}",
            "start": {"dateTime": ev["start"].isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": ev["end"].isoformat(), "timeZone": "Asia/Jerusalem"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 30}]},
            "extendedProperties": {"private": {**TAG, "shift": ev["shift"], "position": ev["position"]}},
        }).execute()

# ── זרימה ─────────────────────────────────────────────────────────────────────

def to_events(found: list) -> list:
    events = []
    for s in found:
        if s.get("shift") not in REGULAR:
            continue
        day = datetime.strptime(s["date"], "%d/%m/%Y").date()
        start, end = shift_hours(s.get("position", ""), s["shift"], bool(s.get("twelve")))
        events.append(make_event(day, s["shift"], start, end, s.get("position", "").strip()))
    return sorted(events, key=lambda e: e["start"])

def handle_image(image: bytes) -> None:
    send("🔍 קורא את הסידור...")
    result = ask_ai(VISION_PROMPT, image)
    if not result.get("is_schedule"):
        send("🤷 זה לא נראה כמו סידור עבודה. כרגע אני יודע לקרוא מתמונות רק סידורי עבודה.")
        return
    events = to_events(result.get("shifts", []))
    if not events:
        send("🤷 לא מצאתי את השם שלך (מאור פורמן) בסידור.")
        return
    ws = week_start(events[0]["start"].date())
    text = preview_text(events, ws, diff(existing_shifts(ws), events), conflicts(events))
    send(text, [{"text": "✅ אשר והכנס ליומן", "callback_data": f"shifts:{ws.isoformat()}"},
                {"text": "❌ בטל", "callback_data": "cancel"}])

def approve(message: dict, ws_iso: str) -> None:
    ws = date.fromisoformat(ws_iso)
    events = parse_preview(message.get("text", ""), ws)
    if not events:  # בלי זה הלחיצה הייתה מוחקת את משמרות השבוע ולא מכניסה כלום
        send("⚠️ לא הצלחתי לקרוא את המשמרות מההודעה. שלח את התמונה שוב.")
        return
    replace_week(ws, events)
    edit(message["message_id"], "\n".join([*[esc(line(e)) for e in events], "",
                                           f"✅ <b>{len(events)} משמרות נכנסו ליומן</b>"]))
