from __future__ import annotations

from datetime import date, datetime, timedelta
import csv
import io
import json
import os
import sqlite3
import urllib.parse
import urllib.request

from functools import wraps
from typing import Any, Callable, Iterable

from dotenv import load_dotenv
from flask import Flask, Response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me-in-production")

DB_PATH = "planner.db"
DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
REMINDER_MINUTES = 30


def adapt_sql(sql: str) -> str:
    return sql.replace("?", "%s") if USE_POSTGRES else sql


def get_conn():
    if USE_POSTGRES:
        import psycopg
        return psycopg.connect(DATABASE_URL, sslmode="require")
    return sqlite3.connect(DB_PATH)


def query(sql: str, params: Iterable[Any] = (), fetch: str | None = "all"):
    if USE_POSTGRES:
        import psycopg
        with get_conn() as conn:
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(adapt_sql(sql), list(params))
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return None
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, list(params))
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return None


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(adapt_sql(sql), list(params))
            conn.commit()
        return
    with get_conn() as conn:
        conn.execute(sql, list(params))


def execute_many(sql: str, params_list: Iterable[Iterable[Any]]) -> None:
    if USE_POSTGRES:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for params in params_list:
                    cur.execute(adapt_sql(sql), list(params))
            conn.commit()
        return
    with get_conn() as conn:
        conn.executemany(sql, [list(p) for p in params_list])


def is_unique_violation(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    return getattr(exc, "sqlstate", None) == "23505"


def init_db() -> None:
    if USE_POSTGRES:
        execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                name TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#e7dfd5'
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                subject_id INTEGER,
                title TEXT NOT NULL,
                session_date TEXT NOT NULL,
                session_time TEXT NOT NULL,
                duration_min INTEGER NOT NULL,
                priority TEXT NOT NULL,
                notes TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                reminder_sent INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT NOT NULL,
                color TEXT NOT NULL DEFAULT '#e7dfd5'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject_id INTEGER,
                title TEXT NOT NULL,
                session_date TEXT NOT NULL,
                session_time TEXT NOT NULL,
                duration_min INTEGER NOT NULL,
                priority TEXT NOT NULL,
                notes TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                reminder_sent INTEGER NOT NULL DEFAULT 0
            )
            """
        )


# Initialize DB on startup for WSGI servers (e.g., gunicorn on Render).
init_db()


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def get_user_id() -> int:
    return int(session["user_id"])


@app.get("/register")
def register() -> str:
    return render_template("register.html")


@app.post("/register")
def register_post() -> str:
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    if not email or not password:
        return redirect(url_for("register"))

    password_hash = generate_password_hash(password)
    created_at = datetime.utcnow().isoformat()

    try:
        execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, password_hash, created_at),
        )
        row = query("SELECT id FROM users WHERE email = ?", (email,), fetch="one")
        user_id = row["id"] if row else None
    except Exception as exc:
        if is_unique_violation(exc):
            return redirect(url_for("register"))
        raise

    if not user_id:
        return redirect(url_for("register"))

    session["user_id"] = user_id
    session["user_email"] = email
    return redirect(url_for("dashboard"))


@app.get("/login")
def login() -> str:
    return render_template("login.html")


@app.post("/login")
def login_post() -> str:
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    if not email or not password:
        return redirect(url_for("login"))

    user = query(
        "SELECT id, password_hash FROM users WHERE email = ?",
        (email,),
        fetch="one",
    )
    if not user or not check_password_hash(user["password_hash"], password):
        return redirect(url_for("login"))

    session["user_id"] = user["id"]
    session["user_email"] = email
    return redirect(url_for("dashboard"))


@app.get("/logout")
def logout() -> str:
    session.clear()
    return redirect(url_for("index"))


@app.get("/health/db")
def health_db() -> Response:
    using = "postgres" if USE_POSTGRES else "sqlite"
    host = "local"
    if DATABASE_URL:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(DATABASE_URL)
            host = parsed.hostname or "unknown"
        except Exception:
            host = "unknown"
    return app.response_class(
        json.dumps(
            {
                "db": using,
                "host": host,
                "has_database_url": bool(DATABASE_URL),
            },
            ensure_ascii=False,
        ),
        mimetype="application/json",
    )


@app.get("/")
def index() -> str:
    today = date.today()
    return render_template("index.html", today=today)


@app.get("/dashboard")
@login_required
def dashboard() -> str:
    user_id = get_user_id()
    today = date.today().isoformat()

    sessions = query(
        """
        SELECT sessions.*,
               subjects.name AS subject_name,
               subjects.color AS subject_color
        FROM sessions
        LEFT JOIN subjects ON subjects.id = sessions.subject_id
        WHERE session_date >= ? AND sessions.user_id = ?
        ORDER BY session_date, session_time
        """,
        (today, user_id),
        fetch="all",
    )
    subjects = query(
        "SELECT * FROM subjects WHERE user_id = ? ORDER BY name",
        (user_id,),
        fetch="all",
    )
    stats = query(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS done,
            SUM(duration_min) AS minutes
        FROM sessions
        WHERE session_date >= ? AND user_id = ?
        """,
        (today, user_id),
        fetch="one",
    )

    if not stats:
        stats = {"total": 0, "done": 0, "minutes": 0}

    total = stats["total"] or 0
    done = stats["done"] or 0
    minutes = stats["minutes"] or 0

    tips = generate_tips(sessions)
    send_due_reminders(user_id)

    return render_template(
        "dashboard.html",
        sessions=sessions,
        subjects=subjects,
        total=total,
        done=done,
        minutes=minutes,
        tips=tips,
    )


@app.post("/sessions/create")
@login_required
def create_session() -> str:
    user_id = get_user_id()
    title = request.form.get("title", "").strip()
    subject_id = request.form.get("subject_id", "").strip()
    session_date = request.form.get("session_date", "").strip()
    session_time = request.form.get("session_time", "").strip()
    duration_min = request.form.get("duration_min", "").strip()
    priority = request.form.get("priority", "Средний").strip()
    notes = request.form.get("notes", "").strip()

    if not title or not session_date or not session_time or not duration_min:
        return redirect(url_for("dashboard"))

    try:
        minutes = max(10, int(duration_min))
    except ValueError:
        return redirect(url_for("dashboard"))

    subject_value = None
    if subject_id:
        try:
            subject_value = int(subject_id)
        except ValueError:
            subject_value = None

    execute(
        """
        INSERT INTO sessions
        (user_id, subject_id, title, session_date, session_time, duration_min, priority, notes, reminder_sent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (user_id, subject_value, title, session_date, session_time, minutes, priority, notes),
    )

    send_telegram_message(
        f"Новое занятие: {title}\n"
        f"Когда: {session_date} {session_time}\n"
        f"Длительность: {minutes} мин\n"
        f"Приоритет: {priority}"
    )

    return redirect(url_for("dashboard"))


@app.post("/sessions/<int:session_id>/toggle")
@login_required
def toggle_session(session_id: int) -> str:
    user_id = get_user_id()
    execute(
        """
        UPDATE sessions
        SET completed = CASE WHEN completed = 1 THEN 0 ELSE 1 END
        WHERE id = ? AND user_id = ?
        """,
        (session_id, user_id),
    )
    return redirect(url_for("dashboard"))


@app.post("/sessions/<int:session_id>/delete")
@login_required
def delete_session(session_id: int) -> str:
    user_id = get_user_id()
    execute(
        "DELETE FROM sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    )
    return redirect(url_for("dashboard"))


@app.get("/subjects")
@login_required
def subjects() -> str:
    user_id = get_user_id()
    rows = query(
        """
        SELECT subjects.*,
               COUNT(sessions.id) AS sessions_count
        FROM subjects
        LEFT JOIN sessions ON sessions.subject_id = subjects.id
        WHERE subjects.user_id = ?
        GROUP BY subjects.id
        ORDER BY subjects.name
        """,
        (user_id,),
        fetch="all",
    )
    return render_template("subjects.html", subjects=rows)


@app.post("/subjects/create")
@login_required
def create_subject() -> str:
    user_id = get_user_id()
    name = request.form.get("name", "").strip()
    color = request.form.get("color", "#e7dfd5").strip()
    if not name:
        return redirect(url_for("subjects"))
    execute(
        "INSERT INTO subjects (user_id, name, color) VALUES (?, ?, ?)",
        (user_id, name, color),
    )
    return redirect(url_for("subjects"))


@app.post("/subjects/<int:subject_id>/delete")
@login_required
def delete_subject(subject_id: int) -> str:
    user_id = get_user_id()
    execute(
        "UPDATE sessions SET subject_id = NULL WHERE subject_id = ? AND user_id = ?",
        (subject_id, user_id),
    )
    execute(
        "DELETE FROM subjects WHERE id = ? AND user_id = ?",
        (subject_id, user_id),
    )
    return redirect(url_for("subjects"))


@app.get("/analytics")
@login_required
def analytics() -> str:
    user_id = get_user_id()
    today_date = date.today()
    today = today_date.isoformat()
    start_7 = (today_date - timedelta(days=6)).isoformat()
    start_28 = (today_date - timedelta(days=27)).isoformat()

    by_subject = query(
        """
        SELECT COALESCE(subjects.name, 'Без предмета') AS name,
               COALESCE(subjects.color, '#e7dfd5') AS color,
               COUNT(sessions.id) AS count_sessions,
               SUM(sessions.duration_min) AS minutes
        FROM sessions
        LEFT JOIN subjects ON subjects.id = sessions.subject_id
        WHERE sessions.session_date >= ? AND sessions.user_id = ?
        GROUP BY COALESCE(subjects.name, 'Без предмета')
        ORDER BY minutes DESC
        """,
        (today, user_id),
        fetch="all",
    )
    totals = query(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END) AS done,
            SUM(duration_min) AS minutes
        FROM sessions
        WHERE session_date >= ? AND user_id = ?
        """,
        (today, user_id),
        fetch="one",
    )
    daily = query(
        """
        SELECT session_date AS label,
               COUNT(*) AS count_sessions,
               SUM(duration_min) AS minutes
        FROM sessions
        WHERE session_date >= ? AND user_id = ?
        GROUP BY session_date
        ORDER BY session_date DESC
        """,
        (start_7, user_id),
        fetch="all",
    )
    weekly = query(
        """
        SELECT substr(session_date, 1, 7) AS label,
               COUNT(*) AS count_sessions,
               SUM(duration_min) AS minutes
        FROM sessions
        WHERE user_id = ? AND session_date >= ?
        GROUP BY substr(session_date, 1, 7)
        ORDER BY label DESC
        LIMIT 4
        """,
        (user_id, start_28),
        fetch="all",
    )

    if not totals:
        totals = {"total": 0, "done": 0, "minutes": 0}

    total = totals["total"] or 0
    done = totals["done"] or 0
    minutes = totals["minutes"] or 0
    focus_rate = round((done / total) * 100) if total else 0

    return render_template(
        "analytics.html",
        by_subject=by_subject,
        total=total,
        done=done,
        minutes=minutes,
        focus_rate=focus_rate,
        daily=daily,
        weekly=weekly,
    )


@app.get("/calendar")
@login_required
def calendar() -> str:
    user_id = get_user_id()
    selected = request.args.get("date")
    if selected:
        try:
            selected_date = datetime.strptime(selected, "%Y-%m-%d").date()
        except ValueError:
            selected_date = date.today()
    else:
        selected_date = date.today()

    start = selected_date - timedelta(days=3)
    end = selected_date + timedelta(days=3)
    rows = query(
        """
        SELECT sessions.*,
               subjects.name AS subject_name,
               subjects.color AS subject_color
        FROM sessions
        LEFT JOIN subjects ON subjects.id = sessions.subject_id
        WHERE session_date BETWEEN ? AND ? AND sessions.user_id = ?
        ORDER BY session_date, session_time
        """,
        (start.isoformat(), end.isoformat(), user_id),
        fetch="all",
    )

    days = []
    for i in range(7):
        day = start + timedelta(days=i)
        day_sessions = [r for r in rows if r["session_date"] == day.isoformat()]
        days.append({"date": day, "sessions": day_sessions})

    return render_template(
        "calendar.html",
        days=days,
        selected_date=selected_date,
    )


@app.get("/export/json")
@login_required
def export_json() -> Response:
    user_id = get_user_id()
    rows = query(
        """
        SELECT sessions.*, subjects.name AS subject_name
        FROM sessions
        LEFT JOIN subjects ON subjects.id = sessions.subject_id
        WHERE sessions.user_id = ?
        ORDER BY session_date, session_time
        """,
        (user_id,),
        fetch="all",
    )
    payload = [
        {
            "title": r["title"],
            "date": r["session_date"],
            "time": r["session_time"],
            "duration_min": r["duration_min"],
            "priority": r["priority"],
            "notes": r["notes"],
            "completed": r["completed"],
            "subject": r["subject_name"],
        }
        for r in rows
    ]
    return app.response_class(
        json.dumps(payload, ensure_ascii=False, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=study_sessions.json"},
    )


@app.get("/export/csv")
@login_required
def export_csv() -> Response:
    user_id = get_user_id()
    rows = query(
        """
        SELECT sessions.*, subjects.name AS subject_name
        FROM sessions
        LEFT JOIN subjects ON subjects.id = sessions.subject_id
        WHERE sessions.user_id = ?
        ORDER BY session_date, session_time
        """,
        (user_id,),
        fetch="all",
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["title", "date", "time", "duration_min", "priority", "notes", "completed", "subject"]
    )
    for r in rows:
        writer.writerow(
            [
                r["title"],
                r["session_date"],
                r["session_time"],
                r["duration_min"],
                r["priority"],
                r["notes"] or "",
                r["completed"],
                r["subject_name"] or "",
            ]
        )
    csv_data = output.getvalue()
    return app.response_class(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=study_sessions.csv"},
    )


@app.post("/import/json")
@login_required
def import_json() -> str:
    user_id = get_user_id()
    file = request.files.get("file")
    if not file:
        return redirect(url_for("dashboard"))
    try:
        data = json.loads(file.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return redirect(url_for("dashboard"))

    for item in data:
        title = str(item.get("title", "")).strip()
        session_date = str(item.get("date", "")).strip()
        session_time = str(item.get("time", "")).strip()
        duration_min = int(item.get("duration_min", 30))
        priority = str(item.get("priority", "Средний")).strip()
        notes = str(item.get("notes", "")).strip()
        completed = int(bool(item.get("completed", 0)))
        subject_name = str(item.get("subject", "")).strip()

        subject_id = None
        if subject_name:
            row = query(
                "SELECT id FROM subjects WHERE user_id = ? AND name = ?",
                (user_id, subject_name),
                fetch="one",
            )
            if row:
                subject_id = row["id"]
            else:
                execute(
                    "INSERT INTO subjects (user_id, name, color) VALUES (?, ?, ?)",
                    (user_id, subject_name, "#e7dfd5"),
                )
                subject_id = query(
                    "SELECT id FROM subjects WHERE user_id = ? AND name = ?",
                    (user_id, subject_name),
                    fetch="one",
                )["id"]

        execute(
            """
            INSERT INTO sessions
            (user_id, subject_id, title, session_date, session_time, duration_min, priority, notes, completed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                subject_id,
                title,
                session_date,
                session_time,
                duration_min,
                priority,
                notes,
                completed,
            ),
        )

    return redirect(url_for("dashboard"))


@app.post("/import/csv")
@login_required
def import_csv() -> str:
    user_id = get_user_id()
    file = request.files.get("file")
    if not file:
        return redirect(url_for("dashboard"))
    try:
        text = file.read().decode("utf-8")
    except UnicodeDecodeError:
        return redirect(url_for("dashboard"))

    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        title = (row.get("title") or "").strip()
        session_date = (row.get("date") or "").strip()
        session_time = (row.get("time") or "").strip()
        duration_min = int(row.get("duration_min") or 30)
        priority = (row.get("priority") or "Средний").strip()
        notes = (row.get("notes") or "").strip()
        completed = int(row.get("completed") or 0)
        subject_name = (row.get("subject") or "").strip()

        if not title or not session_date or not session_time:
            continue

        subject_id = None
        if subject_name:
            subject_row = query(
                "SELECT id FROM subjects WHERE user_id = ? AND name = ?",
                (user_id, subject_name),
                fetch="one",
            )
            if subject_row:
                subject_id = subject_row["id"]
            else:
                execute(
                    "INSERT INTO subjects (user_id, name, color) VALUES (?, ?, ?)",
                    (user_id, subject_name, "#e7dfd5"),
                )
                subject_id = query(
                    "SELECT id FROM subjects WHERE user_id = ? AND name = ?",
                    (user_id, subject_name),
                    fetch="one",
                )["id"]

        execute(
            """
            INSERT INTO sessions
            (user_id, subject_id, title, session_date, session_time, duration_min, priority, notes, completed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                subject_id,
                title,
                session_date,
                session_time,
                duration_min,
                priority,
                notes,
                completed,
            ),
        )

    return redirect(url_for("dashboard"))


def generate_tips(sessions: list[dict]) -> list[str]:
    tips: list[str] = []
    if not sessions:
        return [
            "Добавь 2–3 занятия на ближайшие дни и выбери приоритет.",
            "Начни с коротких сессий по 30–45 минут.",
        ]

    completed = sum(1 for s in sessions if s["completed"])
    total = len(sessions)
    minutes = sum(int(s["duration_min"]) for s in sessions)
    focus_rate = round((completed / total) * 100) if total else 0

    if focus_rate < 50:
        tips.append("Фокус ниже 50% — попробуй планировать меньше, но регулярнее.")
    if minutes > 240:
        tips.append("Суммарно больше 4 часов — добавь перерывы между сессиями.")

    priorities = [s["priority"] for s in sessions if s["priority"]]
    if priorities.count("Высокий") >= 3:
        tips.append("Много высоких приоритетов — выдели 1–2 главные задачи.")

    if not tips:
        tips.append("Хороший баланс! Можешь добавить цель недели для мотивации.")

    return tips


def send_telegram_message(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
        }
    ).encode("utf-8")
    try:
        urllib.request.urlopen(url, data=data, timeout=5)
    except Exception:
        pass


def send_due_reminders(user_id: int) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    now = datetime.now()
    window_end = now + timedelta(minutes=REMINDER_MINUTES)
    today = now.date().isoformat()

    rows = query(
        """
        SELECT id, title, session_date, session_time, duration_min
        FROM sessions
        WHERE user_id = ?
          AND session_date = ?
          AND completed = 0
          AND reminder_sent = 0
        """,
        (user_id, today),
        fetch="all",
    )

    to_mark = []
    for row in rows:
        try:
            session_dt = datetime.strptime(
                f"{row['session_date']} {row['session_time']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            continue

        if now <= session_dt <= window_end:
            send_telegram_message(
                f"Напоминание: скоро занятие\n"
                f"{row['title']} в {row['session_time']} "
                f"({row['duration_min']} мин)"
            )
            to_mark.append(row["id"])

    if to_mark:
        execute_many(
            "UPDATE sessions SET reminder_sent = 1 WHERE id = ?",
            [(sid,) for sid in to_mark],
        )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
