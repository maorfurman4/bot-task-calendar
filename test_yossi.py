"""בדיקות קצרות: python test_yossi.py"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "api"))
import _shifts as s  # noqa: E402

# שעות לפי הכללים של מאור
assert s.shift_hours("מאבטח 1 אקדח", "בוקר", False) == ("07:00", "15:00")
assert s.shift_hours("הרצליה", "צהריים", False) == ("15:00", "23:00")
assert s.shift_hours('רמ"ש', "לילה", False) == ("22:00", "06:00")
assert s.shift_hours("רמ״ש", "צהריים", False) == ("14:00", "22:00")  # גרשיים עבריים
assert s.shift_hours("צלף", "בוקר", True) == ("07:00", "19:00")
assert s.shift_hours("צלף", "לילה", True) == ("19:00", "07:00")
assert s.shift_hours('רמ"ש', "לילה", True) == ("18:00", "06:00")
assert s.shift_hours("שיפוצים 7:00-17:00", "בוקר", False) == ("07:00", "17:00")

# הסידור לדוגמה (שבוע 25/10/2026): 3 משמרות
found = [
    {"date": "29/10/2026", "shift": "לילה", "position": 'רמ"ש', "twelve": False},
    {"date": "25/10/2026", "shift": "צהריים", "position": "מאבטח 1 אקדח", "twelve": False},
    {"date": "26/10/2026", "shift": "צהריים", "position": "הרצליה", "twelve": False},
    {"date": "26/10/2026", "shift": "משהו", "position": "x", "twelve": False},  # לא חוקי — מסונן
]
events = s.to_events(found)
assert [s.line(e) for e in events] == [
    "🛡️ ראשון 25/10 · צהריים · 15:00–23:00 · מאבטח 1 אקדח",
    "🛡️ שני 26/10 · צהריים · 15:00–23:00 · הרצליה",
    '🛡️ חמישי 29/10 · לילה · 22:00–06:00 · רמ"ש',
]
assert events[2]["end"].date() == date(2026, 10, 30)  # לילה נגמר למחרת
ws = s.week_start(events[0]["start"].date())
assert ws == date(2026, 10, 25) and s.week_start(date(2026, 10, 31)) == ws

# אישור: המשמרות נקראות בחזרה מטקסט ההודעה בדיוק כמו שהוצגו
text = s.preview_text(events, ws, [], []).replace("&quot;", '"')
assert [s._key(e) for e in s.parse_preview(text, ws)] == [s._key(e) for e in events]
assert s.parse_preview("שורה בלי משמרת\n📋 כותרת", ws) == []

# שבוע שחוצה שנה
jan = s.make_event(date(2027, 1, 2), "בוקר", "07:00", "15:00", "צלף")
assert s.parse_preview(s.line(jan), date(2026, 12, 27))[0]["start"].year == 2027

# מה השתנה
assert s.diff([], events) == []
assert s.diff(events, events) == ["✔️ זהה לסידור שכבר ביומן"]
changed = s.diff(events[:2], events[1:])
assert len(changed) == 2 and changed[0].startswith("➕ נוסף: חמישי") and changed[1].startswith("➖ בוטל: ראשון")

# קיצורי טקסט: "ראשון שלישי וחמישי משמרת צהריים"
wed = date(2026, 10, 28)  # יום רביעי → ברירת מחדל: השבוע הבא (1/11)
def short(text, today=wed):
    return [s.line(e) for e in s.parse_shortcut(text, today)]

assert short("יש לי בראשון שלישי וחמישי משמרת צהריים") == [
    "🛡️ ראשון 01/11 · צהריים · 15:00–23:00 · רגילה",
    "🛡️ שלישי 03/11 · צהריים · 15:00–23:00 · רגילה",
    "🛡️ חמישי 05/11 · צהריים · 15:00–23:00 · רגילה",
]
assert short("משמרות: ראשון ושלישי בוקר, חמישי לילה רמש") == [
    "🛡️ ראשון 01/11 · בוקר · 07:00–15:00 · רגילה",
    "🛡️ שלישי 03/11 · בוקר · 07:00–15:00 · רגילה",
    '🛡️ חמישי 05/11 · לילה · 22:00–06:00 · רמ"ש',
]
assert short("משמרת צהריים בשני") == ["🛡️ שני 02/11 · צהריים · 15:00–23:00 · רגילה"]
assert short("השבוע משמרת לילה בשישי") == ["🛡️ שישי 30/10 · לילה · 23:00–07:00 · רגילה"]
assert short("משמרת בוקר 12 בשבת") == ["🛡️ שבת 07/11 · בוקר · 07:00–19:00 · רגילה"]
assert short("משמרת בראשון", date(2026, 11, 1)) == []  # בלי סוג משמרת — לא קיצור
assert short("אימון בבוקר ביום שלישי") == []  # בלי "משמרת" — משימה רגילה

# צילום מסך → אירועים ומשימות: הטקסט של התצוגה המקדימה הוא גם המקור לאישור
import _core as c  # noqa: E402
items = [
    {"type": "event", "title": "רופא שיניים — מרפאת הדר", "date": "02/11/2026", "start_time": "08:00", "end_time": None},
    {"type": "task", "title": "לקנות חלב", "date": "01/11/2026"},
]
preview = "\n".join(c.item_line(i) for i in items)
assert preview.splitlines() == [
    "📅 שני 02/11/2026 · 08:00–09:00 · רופא שיניים — מרפאת הדר",
    "📌 ראשון 01/11/2026 · לקנות חלב",
]
back = c.parse_items("📸 מצאתי בתמונה:\n" + preview + "\nשורה אחרת")
assert back == [
    {"type": "event", "title": "רופא שיניים — מרפאת הדר", "date": "02/11/2026", "start_time": "08:00", "end_time": "09:00"},
    {"type": "task", "title": "לקנות חלב", "date": "01/11/2026", "start_time": None, "end_time": None},
]
assert c.item_line({"type": "event", "title": "x", "date": "bad"}) == ""  # תאריך לא תקין — לא מוצג

# ביטול: כל פעולה מסומנת במזהה קצר שנכנס לכפתור
b = c.new_batch()
assert len(f"undo:{b}".encode()) <= 64 and c.is_marked({"notes": f"נוסף ע\"י יוסי · {c.marker(b)}"}, b)
assert not c.is_marked({"notes": "x"}, b) and not c.is_marked({}, b)

# קובץ תמונה מזוהה גם כשטלגרם שולח mime_type כללי (כך זה הגיע מהמק)
import webhook  # noqa: E402
assert webhook.is_image({"mime_type": "application/binary", "file_name": "5a21.JPG"})
assert webhook.is_image({"mime_type": "image/png", "file_name": "x"})
assert not webhook.is_image({"mime_type": "application/pdf", "file_name": "a.pdf"})

print("ok")
