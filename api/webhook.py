"""Webhook של טלגרם עבור יוסי (Vercel Function)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import _core  # noqa: E402
import _shifts  # noqa: E402

HELP = ("👋 אני יוסי.\n"
        "• כתוב לי משימה או אירוע, למשל: \"לקנות חלב, אימון מחר ב-6\"\n"
        "• שלח תמונה של סידור עבודה — אמצא את המשמרות שלך ואכניס ליומן אחרי אישור")

def dispatch(update: dict) -> None:
    if cq := update.get("callback_query"):
        message = cq.get("message", {})
        if str(message.get("chat", {}).get("id")) != _core.CHAT_ID:
            return
        _core.tg("answerCallbackQuery", callback_query_id=cq["id"])
        data = cq.get("data", "")
        if data.startswith("shifts:"):
            _shifts.approve(message, data.split(":", 1)[1])
        elif data == "cancel":
            _core.edit(message["message_id"], "❌ בוטל — לא הוכנס כלום ליומן.")
        return

    message = update.get("message") or {}
    if str(message.get("chat", {}).get("id")) != _core.CHAT_ID:
        return  # רק מאור
    doc = message.get("document") or {}
    _core.logger.info("message keys=%s photo_sizes=%s doc=%s", sorted(message),
                      [(p.get("width"), p.get("height")) for p in message.get("photo", [])],
                      {k: doc.get(k) for k in ("mime_type", "file_name", "file_size")})
    if message.get("photo"):
        _shifts.handle_image(_core.download(message["photo"][-1]["file_id"]))
    elif doc.get("mime_type", "").startswith("image/"):  # תמונה שנשלחה כקובץ — באיכות מלאה
        _shifts.handle_image(_core.download(doc["file_id"]))
    elif text := message.get("text", ""):
        _core.send(HELP) if text.startswith("/") else _core.handle_text(text)

def app(environ, start_response):
    """WSGI — מה ש-Vercel מריץ (pyproject.toml → entrypoint)."""
    def reply(status: str, body: bytes = b""):
        start_response(status, [("Content-Type", "text/plain; charset=utf-8")])
        return [body]

    if environ.get("REQUEST_METHOD") != "POST":
        return reply("200 OK", "Yossi is alive".encode())
    if environ.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN") != _core.webhook_secret():
        return reply("403 Forbidden")
    try:
        body = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
        dispatch(json.loads(body or b"{}"))
    except Exception as ex:
        _core.logger.exception("update failed")
        try:
            _core.send(f"⚠️ יוסי נתקל בשגיאה: {_core.esc(type(ex).__name__)}: {_core.esc(str(ex)[:200])}")
        except Exception:
            pass
    # תמיד 200 — אחרת טלגרם שולח את אותה הודעה שוב ושוב
    return reply("200 OK")
