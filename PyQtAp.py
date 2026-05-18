#!/usr/bin/env python3
"""
FieldSurvey GO – PyQt Professional Edition
-----------------------------------------
A modern desktop app (PySide6 + SQLite) for building simple forms,
collecting field data offline, and exporting CSV/JSON—no terminal needed.

Major features
- Projects & Users (enumerator/admin)
- Form Designer (text, number, select, date, path) with reordering
- Guided data collection with validation, optional GPS & device ID
- Export CSV/JSON; Import JSON (duplicate-safe via response UUID)
- Quick Stats (value counts + ASCII bar)

Modern look & feel
- Fusion theme with custom Light/Dark palettes
- Large comfortable controls, icon buttons, shortcuts

Dependencies (one-time):
    pip install PySide6

Run (double-click or):
    pythonw fieldsurvey_qt.py   (Windows)
    python3 fieldsurvey_qt.py   (macOS/Linux)
"""
from __future__ import annotations
import csv
import datetime as dt
import json
import os
import sqlite3
import sys
import uuid as uuidlib
from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

DB_DEFAULT = "survey1.db"

# ==============================================
# Database layer
# ==============================================

def connect(db_path: str) -> sqlite3.Connection:
    must_init = not os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    if must_init:
        init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            org TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('enumerator','admin'))
        );

        CREATE TABLE IF NOT EXISTS forms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            ftype TEXT NOT NULL CHECK(ftype IN ('text','number','select','date','path')),
            required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
            options_json TEXT,
            ord INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            latitude REAL,
            longitude REAL,
            device_id TEXT,
            uuid TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
            field_id INTEGER NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
            value_text TEXT,
            value_num REAL,
            value_date TEXT,
            value_path TEXT
        );

        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT (datetime('now')),
            checksum TEXT NOT NULL UNIQUE
        );

        CREATE INDEX IF NOT EXISTS idx_fields_form_ord ON fields(form_id, ord);
        CREATE INDEX IF NOT EXISTS idx_answers_response ON answers(response_id);
        CREATE INDEX IF NOT EXISTS idx_responses_form ON responses(form_id);
        """
    )
    conn.commit()


def q(conn, sql, args=()):
    return conn.execute(sql, args)


def add_project(conn, name: str, org: Optional[str]):
    q(conn, "INSERT INTO projects(name, org) VALUES (?,?)", (name, org))
    conn.commit()


def list_projects(conn):
    return q(conn, "SELECT * FROM projects ORDER BY name").fetchall()


def add_user(conn, name: str, role: str):
    q(conn, "INSERT INTO users(name, role) VALUES (?,?)", (name, role))
    conn.commit()


def list_users(conn):
    return q(conn, "SELECT * FROM users ORDER BY name").fetchall()


def add_form(conn, project_id: int, name: str, version: int):
    q(conn, "INSERT INTO forms(project_id, name, version) VALUES (?,?,?)", (project_id, name, version))
    conn.commit()


def list_forms_by_project(conn, project_id: int):
    return q(conn, "SELECT * FROM forms WHERE project_id=? ORDER BY name", (project_id,)).fetchall()


def add_field(conn, form_id: int, label: str, ftype: str, required: bool, options: Optional[List[str]], ord: int):
    options_json = json.dumps(options) if options else None
    q(conn, """
       INSERT INTO fields(form_id, label, ftype, required, options_json, ord)
       VALUES (?,?,?,?,?,?)
       """, (form_id, label, ftype, int(required), options_json, ord))
    conn.commit()


def list_fields(conn, form_id: int):
    return q(conn, "SELECT * FROM fields WHERE form_id=? ORDER BY ord, id", (form_id,)).fetchall()


def update_field_order(conn, field_id: int, new_ord: int):
    q(conn, "UPDATE fields SET ord=? WHERE id=?", (new_ord, field_id))
    conn.commit()


def delete_field(conn, field_id: int):
    q(conn, "DELETE FROM fields WHERE id=?", (field_id,))
    conn.commit()


def delete_form(conn, form_id: int):
    q(conn, "DELETE FROM forms WHERE id=?", (form_id,))
    conn.commit()


def delete_project(conn, project_id: int):
    q(conn, "DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()


def create_response(conn, form_id: int, user_id: int, lat, lon, device_id: str) -> int:
    uid = str(uuidlib.uuid4())
    cur = q(conn, """
            INSERT INTO responses(form_id, user_id, latitude, longitude, device_id, uuid)
            VALUES (?,?,?,?,?,?)
            """, (form_id, user_id, lat, lon, device_id, uid))
    conn.commit()
    return cur.lastrowid


def add_answer(conn, response_id: int, field_id: int, ftype: str, value: str):
    cols = {
        'text': ("value_text", value),
        'number': ("value_num", float(value) if value != '' else None),
        'date': ("value_date", value),
        'path': ("value_path", value),
        'select': ("value_text", value),
    }
    col, val = cols[ftype]
    q(conn, f"INSERT INTO answers(response_id, field_id, {col}) VALUES (?,?,?)", (response_id, field_id, val))


def commit(conn):
    conn.commit()

# ==============================================
# Import / Export / Stats
# ==============================================

def validate_value(ftype: str, required: bool, value: str, options: Optional[List[str]]) -> Tuple[bool, str]:
    if required and (value is None or str(value).strip() == ""):
        return False, "This field is required."
    if not value:
        return True, ""
    try:
        if ftype == 'number':
            float(value)
        elif ftype == 'date':
            dt.date.fromisoformat(value)
        elif ftype == 'select':
            if options and value not in options:
                return False, f"Pick one of: {', '.join(options)}"
    except Exception as e:
        return False, f"Invalid value: {e}"
    return True, ""


def export_form(conn, form_id: int, fmt: str, out_path: str):
    fields = list_fields(conn, form_id)
    field_map = {f["id"]: f for f in fields}
    rows = q(conn, "SELECT * FROM responses WHERE form_id=? ORDER BY id", (form_id,)).fetchall()
    records: List[Dict[str, Any]] = []
    for r in rows:
        ans_rows = q(conn, "SELECT * FROM answers WHERE response_id=?", (r["id"],)).fetchall()
        rec = {
            "response_id": r["id"],
            "user_id": r["user_id"],
            "created_at": r["created_at"],
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "device_id": r["device_id"],
            "uuid": r["uuid"],
        }
        for a in ans_rows:
            f = field_map[a["field_id"]]
            label = f["label"]
            v = a["value_text"] or a["value_num"] or a["value_date"] or a["value_path"]
            rec[label] = v
        records.append(rec)
    if fmt == 'json':
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({"form_id": form_id, "records": records}, f, ensure_ascii=False, indent=2)
    elif fmt == 'csv':
        keys = set()
        for rec in records:
            keys.update(rec.keys())
        keys = list(sorted(keys))
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for rec in records:
                w.writerow(rec)
    else:
        raise ValueError("Unsupported export format; use csv or json")


def file_checksum(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def import_file(conn, path: str):
    csum = file_checksum(path)
    row = q(conn, "SELECT 1 FROM imports WHERE checksum=?", (csum,)).fetchone()
    if row:
        return 0, "Already imported this file."
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict) or "records" not in data:
        raise ValueError("Invalid import file. Expected JSON with 'records'.")
    records = data["records"]
    count_new = 0
    for rec in records:
        existing = q(conn, "SELECT id FROM responses WHERE uuid=?", (rec.get("uuid"),)).fetchone()
        if existing:
            continue
        cur = q(conn, """
                INSERT INTO responses(form_id, user_id, created_at, latitude, longitude, device_id, uuid)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    data.get("form_id") or rec.get("form_id"),
                    rec.get("user_id"),
                    rec.get("created_at") or dt.datetime.utcnow().isoformat(),
                    rec.get("latitude"),
                    rec.get("longitude"),
                    rec.get("device_id"),
                    rec.get("uuid") or str(uuidlib.uuid4()),
                ),
        )
        response_id = cur.lastrowid
        fields = list_fields(conn, data.get("form_id") or rec.get("form_id"))
        label_to_field = {f["label"]: f for f in fields}
        skip_keys = {"response_id","user_id","created_at","latitude","longitude","device_id","uuid","form_id"}
        for k, v in rec.items():
            if k in skip_keys:
                continue
            f = label_to_field.get(k)
            if not f:
                continue
            add_answer(conn, response_id, f["id"], f["ftype"], str(v) if v is not None else "")
        count_new += 1
    q(conn, "INSERT INTO imports(file_name, checksum) VALUES (?,?)", (os.path.basename(path), csum))
    conn.commit()
    return count_new, "Imported successfully."


def value_counts(conn, form_id: int, field_id: int) -> Dict[str, int]:
    field = q(conn, "SELECT * FROM fields WHERE id=?", (field_id,)).fetchone()
    if not field:
        raise ValueError("Field not found")
    if field["form_id"] != form_id:
        raise ValueError("Field does not belong to this form")
    col = {
        'text': 'value_text',
        'number': 'value_num',
        'date': 'value_date',
        'path': 'value_path',
        'select': 'value_text',
    }[field["ftype"]]
    rows = q(conn, f"""
        SELECT {col} AS v, COUNT(*) AS c
        FROM answers a JOIN responses r ON a.response_id=r.id
        WHERE a.field_id=? AND r.form_id=?
        GROUP BY v
        ORDER BY c DESC
    """, (field_id, form_id)).fetchall()
    out: Dict[str, int] = {}
    for r in rows:
        key = str(r["v"]) if r["v"] is not None else "(blank)"
        out[key] = r["c"]
    return out


def ascii_bar_chart(counts: Dict[str, int], width: int = 40) -> str:
    if not counts:
        return "(no data)"
    max_v = max(counts.values())
    lines = []
    for k, v in counts.items():
        bar_len = int((v / max_v) * width) if max_v > 0 else 0
        lines.append(f"{k[:24]:<24} | " + ("#" * bar_len) + f" {v}")
    return "\n".join(lines)

# ==============================================
# Modern UI helpers (theme, widgets)
# ==============================================

def apply_fusion_theme(app: QtWidgets.QApplication, dark: bool = False):
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    if dark:
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(18, 18, 20))
        pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(232, 232, 236))
        pal.setColor(QtGui.QPalette.Base, QtGui.QColor(28, 28, 32))
        pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(24, 24, 28))
        pal.setColor(QtGui.QPalette.Text, QtGui.QColor(232, 232, 236))
        pal.setColor(QtGui.QPalette.Button, QtGui.QColor(36, 36, 42))
        pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(232, 232, 236))
        pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(66, 133, 244))
        pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
    else:
        pal = app.palette()  # default Fusion light
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(250, 250, 252))
        pal.setColor(QtGui.QPalette.Base, QtGui.QColor(255, 255, 255))
        pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(248, 248, 250))
        pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(59, 130, 246))
        pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
    app.setPalette(pal)


def get_modern_stylesheet(dark: bool = False) -> str:
    """Returns modern, professional stylesheet"""
    if dark:
        return """
        QMainWindow {
            background-color: #1a1a1f;
        }
        QWidget {
            font-family: 'Segoe UI', 'Arial', sans-serif;
            font-size: 10pt;
        }
        QPushButton {
            background-color: #3b82f6;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 10px 20px;
            font-weight: 600;
            min-height: 36px;
        }
        QPushButton:hover {
            background-color: #2563eb;
        }
        QPushButton:pressed {
            background-color: #1d4ed8;
        }
        QPushButton:disabled {
            background-color: #475569;
            color: #94a3b8;
        }
        QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
            background-color: #27272a;
            border: 2px solid #3f3f46;
            border-radius: 6px;
            padding: 8px 12px;
            color: #f4f4f5;
            selection-background-color: #3b82f6;
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 2px solid #3b82f6;
            background-color: #2a2a2f;
        }
        QComboBox::drop-down {
            border: none;
            background-color: #3f3f46;
            border-radius: 4px;
        }
        QComboBox::down-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 6px solid #f4f4f5;
            margin-right: 8px;
        }
        QGroupBox {
            font-weight: 600;
            font-size: 11pt;
            color: #f4f4f5;
            border: 2px solid #3f3f46;
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 16px;
            background-color: #27272a;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 8px;
            background-color: #27272a;
        }
        QTableWidget {
            background-color: #27272a;
            border: 2px solid #3f3f46;
            border-radius: 8px;
            gridline-color: #3f3f46;
            selection-background-color: #3b82f6;
            selection-color: white;
        }
        QTableWidget::item {
            padding: 8px;
        }
        QHeaderView::section {
            background-color: #3f3f46;
            color: #f4f4f5;
            padding: 10px;
            border: none;
            font-weight: 600;
        }
        QTabWidget::pane {
            border: 2px solid #3f3f46;
            border-radius: 8px;
            background-color: #1a1a1f;
        }
        QTabBar::tab {
            background-color: #27272a;
            color: #a1a1aa;
            padding: 12px 24px;
            margin-right: 4px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }
        QTabBar::tab:selected {
            background-color: #3b82f6;
            color: white;
            font-weight: 600;
        }
        QTabBar::tab:hover {
            background-color: #3f3f46;
            color: #f4f4f5;
        }
        QLabel {
            color: #f4f4f5;
        }
        QFormLayout {
            spacing: 12px;
        }
        QToolBar {
            background-color: #27272a;
            border: none;
            spacing: 8px;
            padding: 8px;
        }
        QToolBar QToolButton {
            background-color: #3f3f46;
            color: #f4f4f5;
            border-radius: 6px;
            padding: 8px 16px;
        }
        QToolBar QToolButton:hover {
            background-color: #52525b;
        }
        QMenuBar {
            background-color: #27272a;
            color: #f4f4f5;
            border-bottom: 1px solid #3f3f46;
        }
        QMenuBar::item:selected {
            background-color: #3f3f46;
        }
        QMenu {
            background-color: #27272a;
            color: #f4f4f5;
            border: 1px solid #3f3f46;
        }
        QMenu::item:selected {
            background-color: #3b82f6;
        }
        QStatusBar {
            background-color: #27272a;
            color: #f4f4f5;
            border-top: 1px solid #3f3f46;
        }
        QCheckBox {
            color: #f4f4f5;
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 18px;
            height: 18px;
            border: 2px solid #3f3f46;
            border-radius: 4px;
            background-color: #27272a;
        }
        QCheckBox::indicator:checked {
            background-color: #3b82f6;
            border-color: #3b82f6;
        }
        """
    else:
        return """
        QMainWindow {
            background-color: #f8fafc;
        }
        QWidget {
            font-family: 'Segoe UI', 'Arial', sans-serif;
            font-size: 10pt;
        }
        QPushButton {
            background-color: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 12px 24px;
            font-weight: 600;
            min-height: 40px;
            font-size: 10pt;
        }
        QPushButton:hover {
            background-color: #2563eb;
        }
        QPushButton:pressed {
            background-color: #1d4ed8;
        }
        QPushButton:disabled {
            background-color: #cbd5e1;
            color: #94a3b8;
        }
        QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
            background-color: white;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            padding: 10px 14px;
            color: #1e293b;
            selection-background-color: #3b82f6;
            selection-color: white;
            font-size: 10pt;
        }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
            border: 2px solid #3b82f6;
            background-color: #f8fafc;
        }
        QComboBox::drop-down {
            border: none;
            background-color: #f1f5f9;
            border-radius: 6px;
            width: 30px;
        }
        QComboBox::down-arrow {
            image: none;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 7px solid #475569;
            margin-right: 10px;
        }
        QComboBox QAbstractItemView {
            background-color: white;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            selection-background-color: #3b82f6;
            selection-color: white;
            padding: 4px;
        }
        QGroupBox {
            font-weight: 600;
            font-size: 12pt;
            color: #1e293b;
            border: 2px solid #e2e8f0;
            border-radius: 12px;
            margin-top: 16px;
            padding-top: 20px;
            background-color: white;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 16px;
            padding: 0 10px;
            background-color: white;
            color: #3b82f6;
        }
        QTableWidget {
            background-color: white;
            border: 2px solid #e2e8f0;
            border-radius: 12px;
            gridline-color: #f1f5f9;
            selection-background-color: #dbeafe;
            selection-color: #1e293b;
            alternate-background-color: #f8fafc;
        }
        QTableWidget::item {
            padding: 10px;
            border: none;
        }
        QTableWidget::item:selected {
            background-color: #3b82f6;
            color: white;
        }
        QHeaderView::section {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f1f5f9, stop:1 #e2e8f0);
            color: #1e293b;
            padding: 12px;
            border: none;
            border-bottom: 2px solid #cbd5e1;
            font-weight: 600;
            font-size: 10pt;
        }
        QTabWidget::pane {
            border: 2px solid #e2e8f0;
            border-radius: 12px;
            background-color: #f8fafc;
            top: -1px;
        }
        QTabBar::tab {
            background-color: #f1f5f9;
            color: #64748b;
            padding: 14px 28px;
            margin-right: 4px;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            font-weight: 500;
            font-size: 10pt;
        }
        QTabBar::tab:selected {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 white, stop:1 #f8fafc);
            color: #3b82f6;
            font-weight: 600;
            border-bottom: 3px solid #3b82f6;
        }
        QTabBar::tab:hover {
            background-color: #e2e8f0;
            color: #1e293b;
        }
        QLabel {
            color: #1e293b;
        }
        QFormLayout {
            spacing: 16px;
        }
        QToolBar {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 white, stop:1 #f8fafc);
            border: none;
            border-bottom: 2px solid #e2e8f0;
            spacing: 10px;
            padding: 10px;
        }
        QToolBar QToolButton {
            background-color: white;
            color: #475569;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            padding: 10px 18px;
            font-weight: 500;
        }
        QToolBar QToolButton:hover {
            background-color: #f1f5f9;
            border-color: #3b82f6;
            color: #3b82f6;
        }
        QMenuBar {
            background-color: white;
            color: #1e293b;
            border-bottom: 2px solid #e2e8f0;
            padding: 4px;
        }
        QMenuBar::item:selected {
            background-color: #f1f5f9;
            border-radius: 4px;
        }
        QMenu {
            background-color: white;
            color: #1e293b;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            padding: 6px;
        }
        QMenu::item:selected {
            background-color: #3b82f6;
            color: white;
            border-radius: 4px;
        }
        QStatusBar {
            background-color: white;
            color: #64748b;
            border-top: 2px solid #e2e8f0;
            padding: 6px;
        }
        QCheckBox {
            color: #1e293b;
            spacing: 10px;
        }
        QCheckBox::indicator {
            width: 20px;
            height: 20px;
            border: 2px solid #cbd5e1;
            border-radius: 5px;
            background-color: white;
        }
        QCheckBox::indicator:checked {
            background-color: #3b82f6;
            border-color: #3b82f6;
        }
        QCheckBox::indicator:hover {
            border-color: #3b82f6;
        }
        QScrollBar:vertical {
            background: #f1f5f9;
            width: 14px;
            border-radius: 7px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #cbd5e1;
            border-radius: 7px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover {
            background: #94a3b8;
        }
        QScrollBar:horizontal {
            background: #f1f5f9;
            height: 14px;
            border-radius: 7px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: #cbd5e1;
            border-radius: 7px;
            min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #94a3b8;
        }
        """


def hline() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setFrameShadow(QtWidgets.QFrame.Sunken)
    line.setStyleSheet("color: #e2e8f0;")
    return line


def create_card_widget(title: str = "", parent=None, dark: bool = False) -> Tuple[QtWidgets.QWidget, QtWidgets.QVBoxLayout]:
    """Create a modern card-style widget container"""
    card = QtWidgets.QWidget(parent)
    if dark:
        card.setStyleSheet("""
            QWidget {
                background-color: #27272a;
                border: 2px solid #3f3f46;
                border-radius: 12px;
                padding: 16px;
            }
        """)
    else:
        card.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 2px solid #e2e8f0;
                border-radius: 12px;
                padding: 16px;
            }
        """)
    layout = QtWidgets.QVBoxLayout(card)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)
    if title:
        title_label = QtWidgets.QLabel(title)
        if dark:
            title_label.setStyleSheet("""
                font-size: 14pt;
                font-weight: 600;
                color: #3b82f6;
                padding-bottom: 8px;
                border-bottom: 2px solid #3f3f46;
            """)
        else:
            title_label.setStyleSheet("""
                font-size: 14pt;
                font-weight: 600;
                color: #3b82f6;
                padding-bottom: 8px;
                border-bottom: 2px solid #e2e8f0;
            """)
        layout.addWidget(title_label)
    return card, layout


def style_button(btn: QtWidgets.QPushButton, primary: bool = True, icon_text: str = ""):
    """Apply modern styling to a button"""
    if icon_text:
        btn.setText(f"{icon_text} {btn.text()}")
    if primary:
        btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 24px;
                font-weight: 600;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
        """)
    else:
        btn.setStyleSheet("""
            QPushButton {
                background-color: white;
                color: #475569;
                border: 2px solid #e2e8f0;
                border-radius: 8px;
                padding: 10px 20px;
                font-weight: 500;
                min-height: 38px;
            }
            QPushButton:hover {
                background-color: #f1f5f9;
                border-color: #3b82f6;
                color: #3b82f6;
            }
        """)


class LabeledCombo(QtWidgets.QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        self.lbl = QtWidgets.QLabel(label)
        self.cbo = QtWidgets.QComboBox()
        lay.addWidget(self.lbl)
        lay.addWidget(self.cbo)

# ==============================================
# Main Window
# ==============================================

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, db_path: str = DB_DEFAULT):
        super().__init__()
        self.setWindowTitle("FieldSurvey GO – Professional Edition")
        self.resize(1400, 900)
        self.conn = connect(db_path)
        self.dark_mode = False

        self.current_project_id: Optional[int] = None
        self.current_form_id: Optional[int] = None
        self.current_user_id: Optional[int] = None

        self._build_menu()
        self._build_toolbar()
        self._build_tabs()
        self._apply_styles()
        self._refresh_all_models()

    def _apply_styles(self):
        """Apply modern stylesheet to the application"""
        self.setStyleSheet(get_modern_stylesheet(self.dark_mode))

    # --------- menus & toolbar ---------
    def _build_menu(self):
        m = self.menuBar()
        file = m.addMenu("File")
        act_export_csv = file.addAction("Export CSV…")
        act_export_json = file.addAction("Export JSON…")
        file.addSeparator()
        act_import = file.addAction("Import JSON…")
        file.addSeparator()
        act_quit = file.addAction("Quit")

        view = m.addMenu("View")
        self.act_dark = view.addAction("Dark Mode")
        self.act_dark.setCheckable(True)

        data = m.addMenu("Data")
        act_demo = data.addAction("Add Sample Demo Data")
        act_reset = data.addAction("Reset/Empty Database")

        helpm = m.addMenu("Help")
        act_quick = helpm.addAction("Quick Help")

        act_export_csv.triggered.connect(lambda: self.gui_export("csv"))
        act_export_json.triggered.connect(lambda: self.gui_export("json"))
        act_import.triggered.connect(self.gui_import)
        act_quit.triggered.connect(self.close)
        self.act_dark.toggled.connect(self.toggle_dark)
        act_demo.triggered.connect(self.add_demo_data)
        act_reset.triggered.connect(self.reset_database)
        act_quick.triggered.connect(self.show_help)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QtCore.QSize(24, 24))
        
        # Add icon text to toolbar buttons
        btn_projects = QtGui.QAction("📁 Projects", self)
        btn_forms = QtGui.QAction("📝 Forms", self)
        btn_collect = QtGui.QAction("📊 Collect", self)
        btn_stats = QtGui.QAction("📈 Stats", self)
        
        tb.addAction(btn_projects)
        tb.addAction(btn_forms)
        tb.addAction(btn_collect)
        tb.addAction(btn_stats)
        
        btn_projects.triggered.connect(lambda: self.tabs.setCurrentIndex(1))
        btn_forms.triggered.connect(lambda: self.tabs.setCurrentIndex(2))
        btn_collect.triggered.connect(lambda: self.tabs.setCurrentIndex(3))
        btn_stats.triggered.connect(lambda: self.tabs.setCurrentIndex(4))

    # --------- tabs ---------
    def _build_tabs(self):
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.page_start = QtWidgets.QWidget()
        self.page_projects = QtWidgets.QWidget()
        self.page_forms = QtWidgets.QWidget()
        self.page_collect = QtWidgets.QWidget()
        self.page_stats = QtWidgets.QWidget()

        self.tabs.addTab(self.page_start, "🏠 Start Here")
        self.tabs.addTab(self.page_projects, "📁 Projects & Users")
        self.tabs.addTab(self.page_forms, "📝 Form Designer")
        self.tabs.addTab(self.page_collect, "📊 Collect Data")
        self.tabs.addTab(self.page_stats, "📈 Statistics")

        self._build_start()
        self._build_projects()
        self._build_forms()
        self._build_collect()
        self._build_stats()

    # ----- Start Page -----
    def _build_start(self):
        lay = QtWidgets.QVBoxLayout(self.page_start)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(30)
        
        # Welcome header
        header_widget = QtWidgets.QWidget()
        header_layout = QtWidgets.QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title = QtWidgets.QLabel("Welcome to FieldSurvey GO")
        title.setStyleSheet("""
            font-weight: 700;
            font-size: 32pt;
            color: #1e293b;
            padding: 20px 0;
        """)
        header_layout.addWidget(title)
        
        subtitle = QtWidgets.QLabel("Professional Field Data Collection & Management")
        subtitle.setStyleSheet("""
            font-weight: 400;
            font-size: 14pt;
            color: #64748b;
            padding-bottom: 30px;
        """)
        header_layout.addWidget(subtitle)
        
        lay.addWidget(header_widget)
        
        # Steps card
        steps_card, steps_layout = create_card_widget("🚀 Getting Started - 3 Simple Steps", self.page_start, self.dark_mode)
        steps_layout.setSpacing(20)
        
        step1 = QtWidgets.QLabel("1️⃣  Create a Project and at least one User (enumerator)")
        step2 = QtWidgets.QLabel("2️⃣  Create a Form and add Fields (text/number/select/date/path)")
        step3 = QtWidgets.QLabel("3️⃣  Go to Collect Data to fill responses and export")
        
        for step in [step1, step2, step3]:
            step.setStyleSheet("""
                font-size: 12pt;
                color: #475569;
                padding: 12px;
                background-color: #f8fafc;
                border-radius: 8px;
                border-left: 4px solid #3b82f6;
            """)
            steps_layout.addWidget(step)
        
        lay.addWidget(steps_card)
        
        # Action buttons
        btn_container = QtWidgets.QWidget()
        btn_layout = QtWidgets.QHBoxLayout(btn_container)
        btn_layout.setSpacing(20)
        
        btn_demo = QtWidgets.QPushButton("✨ Add Sample Demo Data")
        style_button(btn_demo, primary=True, icon_text="")
        btn_demo.clicked.connect(self.add_demo_data)
        btn_layout.addWidget(btn_demo)
        
        btn_quick_start = QtWidgets.QPushButton("📖 Quick Help")
        style_button(btn_quick_start, primary=False)
        btn_quick_start.clicked.connect(self.show_help)
        btn_layout.addWidget(btn_quick_start)
        
        btn_layout.addStretch()
        lay.addWidget(btn_container)
        
        # Tips section
        tip_card = QtWidgets.QWidget()
        tip_card.setStyleSheet("""
            QWidget {
                background-color: #eff6ff;
                border: 2px solid #bfdbfe;
                border-radius: 12px;
                padding: 20px;
            }
        """)
        tip_layout = QtWidgets.QHBoxLayout(tip_card)
        tip_layout.setContentsMargins(20, 20, 20, 20)
        
        tip_icon = QtWidgets.QLabel("💡")
        tip_icon.setStyleSheet("font-size: 24pt;")
        tip_layout.addWidget(tip_icon)
        
        tip_text = QtWidgets.QLabel("Tip: You can export CSV/JSON from the File menu, and import data to merge responses.")
        tip_text.setStyleSheet("""
            font-size: 11pt;
            color: #1e40af;
            font-weight: 500;
        """)
        tip_text.setWordWrap(True)
        tip_layout.addWidget(tip_text, 1)
        
        lay.addWidget(tip_card)
        lay.addStretch(1)

    # ----- Projects & Users -----
    def _build_projects(self):
        root = QtWidgets.QHBoxLayout(self.page_projects)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(30)

        # Left side - Create new items
        left_container = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_container)
        left.setSpacing(20)
        
        # New Project card
        box_proj = QtWidgets.QGroupBox("➕ New Project")
        lp = QtWidgets.QFormLayout(box_proj)
        lp.setSpacing(16)
        lp.setContentsMargins(20, 30, 20, 20)
        self.ent_proj_name = QtWidgets.QLineEdit()
        self.ent_proj_name.setPlaceholderText("Enter project name...")
        self.ent_proj_org = QtWidgets.QLineEdit()
        self.ent_proj_org.setPlaceholderText("Organization (optional)")
        btn_proj_create = QtWidgets.QPushButton("✨ Create Project")
        style_button(btn_proj_create, primary=True)
        lp.addRow("📋 Name:", self.ent_proj_name)
        lp.addRow("🏢 Organisation:", self.ent_proj_org)
        lp.addRow("", btn_proj_create)
        btn_proj_create.clicked.connect(self.gui_add_project)

        # New User card
        box_user = QtWidgets.QGroupBox("👤 New User")
        lu = QtWidgets.QFormLayout(box_user)
        lu.setSpacing(16)
        lu.setContentsMargins(20, 30, 20, 20)
        self.ent_user_name = QtWidgets.QLineEdit()
        self.ent_user_name.setPlaceholderText("Enter user name...")
        self.cbo_user_role_new = QtWidgets.QComboBox()
        self.cbo_user_role_new.addItems(["enumerator", "admin"])
        btn_user_create = QtWidgets.QPushButton("✨ Create User")
        style_button(btn_user_create, primary=True)
        lu.addRow("👤 Name:", self.ent_user_name)
        lu.addRow("🔑 Role:", self.cbo_user_role_new)
        lu.addRow("", btn_user_create)
        btn_user_create.clicked.connect(self.gui_add_user)

        left.addWidget(box_proj)
        left.addWidget(box_user)
        left.addStretch(1)

        # Right side - Manage existing items
        right_container = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_container)
        right.setSpacing(20)
        
        grp_proj = QtWidgets.QGroupBox("📁 Projects")
        v1 = QtWidgets.QVBoxLayout(grp_proj)
        v1.setContentsMargins(20, 30, 20, 20)
        v1.setSpacing(12)
        self.cbo_project = QtWidgets.QComboBox()
        self.cbo_project.setMinimumHeight(40)
        v1.addWidget(QtWidgets.QLabel("Select Project:"))
        v1.addWidget(self.cbo_project)
        btn_del_proj = QtWidgets.QPushButton("🗑️ Delete Project")
        style_button(btn_del_proj, primary=False)
        v1.addWidget(btn_del_proj)
        btn_del_proj.clicked.connect(self.gui_delete_project)

        grp_users = QtWidgets.QGroupBox("👥 Users")
        v2 = QtWidgets.QVBoxLayout(grp_users)
        v2.setContentsMargins(20, 30, 20, 20)
        v2.setSpacing(12)
        self.cbo_user = QtWidgets.QComboBox()
        self.cbo_user.setMinimumHeight(40)
        v2.addWidget(QtWidgets.QLabel("Select User:"))
        v2.addWidget(self.cbo_user)
        btn_set_user = QtWidgets.QPushButton("✅ Set as Current Enumerator")
        style_button(btn_set_user, primary=True)
        v2.addWidget(btn_set_user)
        btn_set_user.clicked.connect(self.set_current_user)

        right.addWidget(grp_proj)
        right.addWidget(grp_users)
        right.addStretch(1)

        root.addWidget(left_container, 1)
        root.addWidget(right_container, 1)

        self.cbo_project.currentIndexChanged.connect(self.on_project_change)

    def gui_add_project(self):
        name = self.ent_proj_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Error", "Project name is required")
            return
        org = self.ent_proj_org.text().strip() or None
        try:
            add_project(self.conn, name, org)
        except sqlite3.IntegrityError:
            QtWidgets.QMessageBox.critical(self, "Error", "Project name already exists")
            return
        self.ent_proj_name.clear(); self.ent_proj_org.clear()
        self._refresh_all_models()
        self.statusBar().showMessage("Project created.", 3000)

    def gui_delete_project(self):
        if not self.current_project_id:
            QtWidgets.QMessageBox.information(self, "Info", "Select a project first")
            return
        if QtWidgets.QMessageBox.question(self, "Confirm", "Delete this project and ALL its data?") == QtWidgets.QMessageBox.Yes:
            delete_project(self.conn, self.current_project_id)
            self.current_project_id = None
            self._refresh_all_models()
            self.statusBar().showMessage("Project deleted.", 3000)

    def gui_add_user(self):
        name = self.ent_user_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Error", "User name is required")
            return
        role = self.cbo_user_role_new.currentText()
        add_user(self.conn, name, role)
        self.ent_user_name.clear()
        self._refresh_all_models()
        self.statusBar().showMessage("User added.", 3000)

    def set_current_user(self):
        name = self.cbo_user.currentText()
        if not name:
            QtWidgets.QMessageBox.information(self, "Info", "Pick a user")
            return
        self.current_user_id = self._name_to_id(self.users, name)
        self.statusBar().showMessage(f"Current user: {name}", 3000)

    # ----- Forms -----
    def _build_forms(self):
        root = QtWidgets.QVBoxLayout(self.page_forms)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(20)
        
        # Top toolbar
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(15)
        top.addWidget(QtWidgets.QLabel("📁 Project:"))
        self.cbo_project_fd = QtWidgets.QComboBox()
        self.cbo_project_fd.setMinimumHeight(40)
        self.cbo_project_fd.setMinimumWidth(250)
        top.addWidget(self.cbo_project_fd)
        top.addStretch(1)
        btn_new_form = QtWidgets.QPushButton("➕ New Form")
        style_button(btn_new_form, primary=True)
        btn_del_form = QtWidgets.QPushButton("🗑️ Delete Form")
        style_button(btn_del_form, primary=False)
        top.addWidget(btn_new_form)
        top.addWidget(btn_del_form)
        root.addLayout(top)

        mid = QtWidgets.QHBoxLayout()
        mid.setSpacing(20)
        
        # Forms list
        self.cbo_forms = QtWidgets.QComboBox()
        self.cbo_forms.setMinimumHeight(40)
        box_forms = QtWidgets.QGroupBox("📝 Forms")
        v = QtWidgets.QVBoxLayout(box_forms)
        v.setContentsMargins(20, 30, 20, 20)
        v.setSpacing(12)
        v.addWidget(QtWidgets.QLabel("Select Form:"))
        v.addWidget(self.cbo_forms)
        mid.addWidget(box_forms, 1)

        # Fields table & buttons
        box_fields = QtWidgets.QGroupBox("📋 Fields")
        v2 = QtWidgets.QVBoxLayout(box_fields)
        v2.setContentsMargins(20, 30, 20, 20)
        v2.setSpacing(15)
        self.tbl_fields = QtWidgets.QTableWidget(0, 5)
        self.tbl_fields.setHorizontalHeaderLabels(["Label", "Type", "Required", "Options", "Order"])
        self.tbl_fields.horizontalHeader().setStretchLastSection(True)
        self.tbl_fields.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tbl_fields.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tbl_fields.setAlternatingRowColors(True)
        self.tbl_fields.setMinimumHeight(300)
        v2.addWidget(self.tbl_fields)
        
        rowbtn = QtWidgets.QHBoxLayout()
        rowbtn.setSpacing(10)
        btn_add = QtWidgets.QPushButton("➕ Add Field")
        btn_edit = QtWidgets.QPushButton("✏️ Edit")
        btn_del = QtWidgets.QPushButton("🗑️ Delete")
        btn_up = QtWidgets.QPushButton("⬆️ Up")
        btn_down = QtWidgets.QPushButton("⬇️ Down")
        for b in (btn_add, btn_edit, btn_del, btn_up, btn_down):
            style_button(b, primary=False)
            rowbtn.addWidget(b)
        v2.addLayout(rowbtn)
        mid.addWidget(box_fields, 2)
        root.addLayout(mid, 1)

        # hooks
        self.cbo_project_fd.currentIndexChanged.connect(self._refresh_forms_for_project)
        self.cbo_forms.currentIndexChanged.connect(self.refresh_fields_table)
        btn_new_form.clicked.connect(self.gui_new_form)
        btn_del_form.clicked.connect(self.gui_delete_form)
        btn_add.clicked.connect(self.gui_add_field)
        btn_edit.clicked.connect(self.gui_edit_field)
        btn_del.clicked.connect(self.gui_delete_field)
        btn_up.clicked.connect(lambda: self.move_field(-1))
        btn_down.clicked.connect(lambda: self.move_field(1))

    def gui_new_form(self):
        if not self.current_project_id:
            QtWidgets.QMessageBox.information(self, "Info", "Select a project first")
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Create Form", "Name:")
        if not ok or not name.strip():
            return
        add_form(self.conn, self.current_project_id, name.strip(), 1)
        self._refresh_forms_for_project()
        self.statusBar().showMessage("Form created.", 3000)

    def gui_delete_form(self):
        if not self.current_form_id:
            QtWidgets.QMessageBox.information(self, "Info", "Pick a form first")
            return
        if QtWidgets.QMessageBox.question(self, "Confirm", "Delete this form and its fields?") == QtWidgets.QMessageBox.Yes:
            delete_form(self.conn, self.current_form_id)
            self.current_form_id = None
            self._refresh_forms_for_project()
            self.refresh_fields_table()

    def gui_add_field(self):
        if not self.current_form_id:
            QtWidgets.QMessageBox.information(self, "Info", "Pick a form first")
            return
        dlg = FieldDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            data = dlg.data()
            add_field(self.conn, self.current_form_id, data['label'], data['ftype'], data['required'], data['options'], data['ord'])
            self.refresh_fields_table()

    def gui_edit_field(self):
        if not self.current_form_id:
            return
        row = self.tbl_fields.currentRow()
        if row < 0:
            QtWidgets.QMessageBox.information(self, "Info", "Select a field in the table")
            return
        label = self.tbl_fields.item(row, 0).text()
        ftype = self.tbl_fields.item(row, 1).text()
        req = self.tbl_fields.item(row, 2).text() == "Yes"
        opts = self.tbl_fields.item(row, 3).text()
        ord_v = int(self.tbl_fields.item(row, 4).text())
        dlg = FieldDialog(self, init={
            'label': label, 'ftype': ftype, 'required': req,
            'options': [s for s in opts.split(',') if s], 'ord': ord_v
        })
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            data = dlg.data()
            # simple replace: find matching field id by (label, ord)
            for f in list_fields(self.conn, self.current_form_id):
                if f['label']==label and f['ord']==ord_v:
                    delete_field(self.conn, f['id'])
                    add_field(self.conn, self.current_form_id, data['label'], data['ftype'], data['required'], data['options'], data['ord'])
                    break
            self.refresh_fields_table()

    def gui_delete_field(self):
        if not self.current_form_id:
            return
        row = self.tbl_fields.currentRow()
        if row < 0:
            return
        label = self.tbl_fields.item(row, 0).text()
        ord_v = int(self.tbl_fields.item(row, 4).text())
        if QtWidgets.QMessageBox.question(self, "Confirm", f"Delete field '{label}'?") == QtWidgets.QMessageBox.Yes:
            for f in list_fields(self.conn, self.current_form_id):
                if f['label']==label and f['ord']==ord_v:
                    delete_field(self.conn, f['id'])
                    break
            self.refresh_fields_table()

    def move_field(self, delta: int):
        row = self.tbl_fields.currentRow()
        if row < 0 or not self.current_form_id:
            return
        ord_v = int(self.tbl_fields.item(row, 4).text())
        label = self.tbl_fields.item(row, 0).text()
        fields = list_fields(self.conn, self.current_form_id)
        target = None
        for f in fields:
            if f['label']==label and f['ord']==ord_v:
                target = f
                break
        if not target:
            return
        new_ord = max(0, ord_v + delta)
        update_field_order(self.conn, target['id'], new_ord)
        self.refresh_fields_table()

    def refresh_fields_table(self):
        self.tbl_fields.setRowCount(0)
        if not self.current_form_id:
            return
        self.tbl_fields.setAlternatingRowColors(True)
        for f in list_fields(self.conn, self.current_form_id):
            r = self.tbl_fields.rowCount()
            self.tbl_fields.insertRow(r)
            opts = ",".join(json.loads(f['options_json'])) if f['options_json'] else ""
            for c, val in enumerate([f['label'], f['ftype'], 'Yes' if f['required'] else 'No', opts, str(f['ord'])]):
                item = QtWidgets.QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.tbl_fields.setItem(r, c, item)
        self.tbl_fields.resizeColumnsToContents()

    # ----- Collect -----
    def _build_collect(self):
        root = QtWidgets.QVBoxLayout(self.page_collect)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(20)
        
        # Selection row
        selection_card = QtWidgets.QGroupBox("🔍 Select Project, Form & User")
        selection_layout = QtWidgets.QGridLayout(selection_card)
        selection_layout.setContentsMargins(20, 30, 20, 20)
        selection_layout.setSpacing(15)
        
        self.cbo_project_cd = QtWidgets.QComboBox()
        self.cbo_form_cd = QtWidgets.QComboBox()
        self.cbo_user_cd = QtWidgets.QComboBox()
        
        for combo in [self.cbo_project_cd, self.cbo_form_cd, self.cbo_user_cd]:
            combo.setMinimumHeight(40)
        
        selection_layout.addWidget(QtWidgets.QLabel("📁 Project:"), 0, 0)
        selection_layout.addWidget(self.cbo_project_cd, 0, 1)
        selection_layout.addWidget(QtWidgets.QLabel("📝 Form:"), 0, 2)
        selection_layout.addWidget(self.cbo_form_cd, 0, 3)
        selection_layout.addWidget(QtWidgets.QLabel("👤 User:"), 0, 4)
        selection_layout.addWidget(self.cbo_user_cd, 0, 5)
        
        root.addWidget(selection_card)

        # Metadata row
        metadata_card = QtWidgets.QGroupBox("📱 Metadata (Optional)")
        metadata_layout = QtWidgets.QGridLayout(metadata_card)
        metadata_layout.setContentsMargins(20, 30, 20, 20)
        metadata_layout.setSpacing(15)
        
        self.ent_device = QtWidgets.QLineEdit()
        self.ent_device.setText("DEVICE-001")
        self.ent_device.setPlaceholderText("Device identifier")
        self.ent_lat = QtWidgets.QLineEdit()
        self.ent_lat.setPlaceholderText("Latitude (optional)")
        self.ent_lon = QtWidgets.QLineEdit()
        self.ent_lon.setPlaceholderText("Longitude (optional)")
        
        for edit in [self.ent_device, self.ent_lat, self.ent_lon]:
            edit.setMinimumHeight(40)
        
        metadata_layout.addWidget(QtWidgets.QLabel("🖥️ Device ID:"), 0, 0)
        metadata_layout.addWidget(self.ent_device, 0, 1)
        metadata_layout.addWidget(QtWidgets.QLabel("📍 Latitude:"), 0, 2)
        metadata_layout.addWidget(self.ent_lat, 0, 3)
        metadata_layout.addWidget(QtWidgets.QLabel("📍 Longitude:"), 0, 4)
        metadata_layout.addWidget(self.ent_lon, 0, 5)
        
        self.btn_load_form = QtWidgets.QPushButton("🔄 Load Form")
        style_button(self.btn_load_form, primary=True)
        metadata_layout.addWidget(self.btn_load_form, 0, 6)
        
        root.addWidget(metadata_card)

        # Response form
        self.group_resp = QtWidgets.QGroupBox("📋 Response Form")
        self.form_grid = QtWidgets.QGridLayout(self.group_resp)
        self.form_grid.setContentsMargins(30, 30, 30, 30)
        self.form_grid.setSpacing(20)
        self.form_grid.setColumnStretch(1, 2)
        root.addWidget(self.group_resp, 1)

        # Save button
        btn_container = QtWidgets.QWidget()
        btn_layout = QtWidgets.QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 10, 0, 0)
        btn_layout.addStretch()
        self.btn_save = QtWidgets.QPushButton("💾 Save Response")
        style_button(self.btn_save, primary=True)
        self.btn_save.setMinimumWidth(200)
        self.btn_save.setMinimumHeight(50)
        btn_layout.addWidget(self.btn_save)
        root.addWidget(btn_container)

        self.btn_load_form.clicked.connect(self.build_collect_form)
        self.btn_save.clicked.connect(self.save_collect_response)
        self.cbo_project_cd.currentIndexChanged.connect(self.on_collect_project_change)
        self.cbo_form_cd.currentIndexChanged.connect(self.on_collect_form_change)

        self.collect_widgets: List[Tuple[sqlite3.Row, QtWidgets.QWidget]] = []

    def build_collect_form(self):
        # clear
        while self.form_grid.count():
            item = self.form_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.collect_widgets.clear()
        if not self.current_form_id or not self.current_user_id:
            QtWidgets.QMessageBox.information(self, "Info", "Select Form and User first")
            return
        fields = list_fields(self.conn, self.current_form_id)
        if not fields:
            QtWidgets.QMessageBox.information(self, "Info", "This form has no fields yet")
            return
        for i, f in enumerate(fields):
            label = QtWidgets.QLabel(f['label'])
            label.setStyleSheet("""
                font-weight: 600;
                font-size: 11pt;
                color: #1e293b;
                padding: 8px;
            """)
            self.form_grid.addWidget(label, i, 0)
            
            opts = json.loads(f['options_json']) if f['options_json'] else None
            if f['ftype'] == 'select' and opts:
                w = QtWidgets.QComboBox()
                w.addItems(opts)
                w.setMinimumHeight(40)
            else:
                w = QtWidgets.QLineEdit()
                w.setMinimumHeight(40)
                if f['ftype'] == 'date':
                    w.setText(dt.date.today().isoformat())
                    w.setPlaceholderText("YYYY-MM-DD")
                elif f['ftype'] == 'number':
                    w.setPlaceholderText("Enter a number...")
                elif f['ftype'] == 'text':
                    w.setPlaceholderText("Enter text...")
                elif f['ftype'] == 'path':
                    w.setPlaceholderText("Enter file path...")
            self.form_grid.addWidget(w, i, 1)
            if f['required']:
                star = QtWidgets.QLabel("⚠️")
                star.setStyleSheet("color: #dc2626; font-weight: 700; font-size: 12pt;")
                star.setToolTip("Required field")
                self.form_grid.addWidget(star, i, 2)
            self.collect_widgets.append((f, w))

    def save_collect_response(self):
        if not self.current_form_id or not self.current_user_id:
            QtWidgets.QMessageBox.information(self, "Info", "Select Form and User first")
            return
        device_id = self.ent_device.text().strip() or "DEVICE"
        lat = float(self.ent_lat.text()) if self.ent_lat.text().strip() else None
        lon = float(self.ent_lon.text()) if self.ent_lon.text().strip() else None
        resp_id = create_response(self.conn, self.current_form_id, self.current_user_id, lat, lon, device_id)
        for f, w in self.collect_widgets:
            if isinstance(w, QtWidgets.QComboBox):
                val = w.currentText()
            else:
                val = w.text()
            options = json.loads(f['options_json']) if f['options_json'] else None
            ok, msg = validate_value(f['ftype'], bool(f['required']), val, options)
            if not ok:
                QtWidgets.QMessageBox.critical(self, "Please check", f"{f['label']}: {msg}")
                q(self.conn, "DELETE FROM responses WHERE id=?", (resp_id,))
                self.conn.commit()
                return
            add_answer(self.conn, resp_id, f['id'], f['ftype'], val)
        commit(self.conn)
        QtWidgets.QMessageBox.information(self, "Saved", f"Saved response #{resp_id}")
        # clear inputs except selects
        for _, w in self.collect_widgets:
            if isinstance(w, QtWidgets.QLineEdit):
                w.clear()

    # ----- Stats -----
    def _build_stats(self):
        root = QtWidgets.QVBoxLayout(self.page_stats)
        root.setContentsMargins(30, 30, 30, 30)
        root.setSpacing(20)
        
        # Selection row
        selection_card = QtWidgets.QGroupBox("🔍 Select Project & Form")
        selection_layout = QtWidgets.QHBoxLayout(selection_card)
        selection_layout.setContentsMargins(20, 30, 20, 20)
        selection_layout.setSpacing(15)
        
        self.cbo_project_st = QtWidgets.QComboBox()
        self.cbo_form_st = QtWidgets.QComboBox()
        
        for combo in [self.cbo_project_st, self.cbo_form_st]:
            combo.setMinimumHeight(40)
        
        selection_layout.addWidget(QtWidgets.QLabel("📁 Project:"))
        selection_layout.addWidget(self.cbo_project_st)
        selection_layout.addWidget(QtWidgets.QLabel("📝 Form:"))
        selection_layout.addWidget(self.cbo_form_st)
        selection_layout.addStretch(1)
        
        btn_compute = QtWidgets.QPushButton("📊 Analyze Field")
        style_button(btn_compute, primary=True)
        selection_layout.addWidget(btn_compute)
        
        root.addWidget(selection_card)

        # Statistics table
        stats_card = QtWidgets.QGroupBox("📈 Value Counts")
        stats_layout = QtWidgets.QVBoxLayout(stats_card)
        stats_layout.setContentsMargins(20, 30, 20, 20)
        
        self.tbl_counts = QtWidgets.QTableWidget(0, 2)
        self.tbl_counts.setHorizontalHeaderLabels(["Value", "Count"])
        self.tbl_counts.horizontalHeader().setStretchLastSection(True)
        self.tbl_counts.setAlternatingRowColors(True)
        self.tbl_counts.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        stats_layout.addWidget(self.tbl_counts)
        root.addWidget(stats_card, 1)

        # Chart display
        chart_card = QtWidgets.QGroupBox("📊 Visual Chart")
        chart_layout = QtWidgets.QVBoxLayout(chart_card)
        chart_layout.setContentsMargins(20, 30, 20, 20)
        
        self.txt_chart = QtWidgets.QPlainTextEdit()
        self.txt_chart.setReadOnly(True)
        self.txt_chart.setFixedHeight(200)
        self.txt_chart.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 10pt;
                background-color: #1e293b;
                color: #e2e8f0;
                border: 2px solid #475569;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        chart_layout.addWidget(self.txt_chart)
        root.addWidget(chart_card)

        self.cbo_project_st.currentIndexChanged.connect(self.on_stats_project_change)
        btn_compute.clicked.connect(self.gui_stats_pick_field)

    def gui_stats_pick_field(self):
        if not self.current_form_id:
            QtWidgets.QMessageBox.information(self, "Info", "Select a form first")
            return
        fields = list_fields(self.conn, self.current_form_id)
        if not fields:
            QtWidgets.QMessageBox.information(self, "Info", "Form has no fields")
            return
        items = [f"{f['label']} ({f['ftype']})" for f in fields]
        item, ok = QtWidgets.QInputDialog.getItem(self, "Choose Field", "Select field to analyze:", items, 0, False)
        if not ok:
            return
        # Find field by matching label
        selected_label = item.split(' (')[0]
        field_id = None
        for f in fields:
            if f['label'] == selected_label:
                field_id = f['id']
                break
        if not field_id:
            return
        counts = value_counts(self.conn, self.current_form_id, field_id)
        self.tbl_counts.setRowCount(0)
        self.tbl_counts.setAlternatingRowColors(True)
        for k, v in counts.items():
            r = self.tbl_counts.rowCount()
            self.tbl_counts.insertRow(r)
            item1 = QtWidgets.QTableWidgetItem(str(k))
            item1.setFlags(item1.flags() & ~QtCore.Qt.ItemIsEditable)
            item2 = QtWidgets.QTableWidgetItem(str(v))
            item2.setFlags(item2.flags() & ~QtCore.Qt.ItemIsEditable)
            self.tbl_counts.setItem(r, 0, item1)
            self.tbl_counts.setItem(r, 1, item2)
        self.tbl_counts.resizeColumnsToContents()
        self.txt_chart.setPlainText(ascii_bar_chart(counts))

    # --------- export/import & model sync ---------
    def gui_export(self, fmt: str):
        if not self.current_form_id:
            QtWidgets.QMessageBox.information(self, "Info", "Select a form first (Form Designer or Collect tab)")
            return
        ext = "CSV Files (*.csv)" if fmt == "csv" else "JSON Files (*.json)"
        default_name = f"export_{self.current_form_id}_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, f"Export {fmt.upper()}", default_name, ext
        )
        if not path:
            return
        try:
            export_form(self.conn, self.current_form_id, fmt, path)
            self.statusBar().showMessage(f"✅ Successfully exported {fmt.upper()} to {path}", 5000)
            QtWidgets.QMessageBox.information(
                self, "Export Successful",
                f"Data exported successfully to:\n{path}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Export Error", f"Failed to export data:\n{str(e)}")

    def gui_import(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import JSON", "", "JSON Files (*.json);;All Files (*)"
        )
        if not path:
            return
        try:
            n, msg = import_file(self.conn, path)
            self.statusBar().showMessage(f"✅ Imported {n} responses from {os.path.basename(path)}", 5000)
            QtWidgets.QMessageBox.information(
                self, "Import Successful",
                f"{msg}\n\nAdded {n} new response(s).\n\nFile: {os.path.basename(path)}"
            )
            self._refresh_all_models()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Import Error", f"Failed to import data:\n{str(e)}")

    def _refresh_all_models(self):
        self.projects = list_projects(self.conn)
        self.users = list_users(self.conn)
        self._fill_combo(self.cbo_project, [p['name'] for p in self.projects])
        self._fill_combo(self.cbo_user, [u['name'] for u in self.users])
        self._fill_combo(self.cbo_project_fd, [p['name'] for p in self.projects])
        self._fill_combo(self.cbo_project_cd, [p['name'] for p in self.projects])
        self._fill_combo(self.cbo_project_st, [p['name'] for p in self.projects])
        if self.projects and not self.current_project_id:
            self.current_project_id = self.projects[0]['id']
            for cbo in (self.cbo_project, self.cbo_project_fd, self.cbo_project_cd, self.cbo_project_st):
                cbo.setCurrentIndex(0)
        if self.users:
            if not self.current_user_id:
                self.current_user_id = self.users[0]['id']
            self.cbo_user.setCurrentIndex(0)
        self._fill_combo(self.cbo_user_cd, [u['name'] for u in self.users])
        if self.users:
            self.cbo_user_cd.setCurrentIndex(0)
        self.on_project_change()

    def on_project_change(self):
        name = self.cbo_project.currentText()
        if name:
            self.current_project_id = self._name_to_id(self.projects, name)
        self._refresh_forms_for_project()

    def on_collect_project_change(self):
        name = self.cbo_project_cd.currentText()
        if name:
            self.current_project_id = self._name_to_id(self.projects, name)
        self._refresh_forms_for_project()

    def on_collect_form_change(self):
        name = self.cbo_form_cd.currentText()
        if name:
            self.current_form_id = self._name_to_id(self.forms, name)

    def on_stats_project_change(self):
        name = self.cbo_project_st.currentText()
        if name:
            self.current_project_id = self._name_to_id(self.projects, name)
        self._refresh_forms_for_project()

    def _refresh_forms_for_project(self):
        if not self.current_project_id:
            self.forms = []
        else:
            self.forms = list_forms_by_project(self.conn, self.current_project_id)
        names = [f['name'] for f in self.forms]
        for cbo in (self.cbo_forms, self.cbo_form_cd, self.cbo_form_st):
            self._fill_combo(cbo, names)
            if names:
                cbo.setCurrentIndex(0)
        if self.forms:
            self.current_form_id = self.forms[0]['id']
        self.refresh_fields_table()

    def _fill_combo(self, combo: QtWidgets.QComboBox, values: List[str]):
        combo.clear(); combo.addItems(values)

    def _name_to_id(self, rows, name):
        for r in rows:
            if r['name'] == name:
                return r['id']
        return None

    # --------- dark mode & help ---------
    def toggle_dark(self, checked: bool):
        self.dark_mode = checked
        apply_fusion_theme(QtWidgets.QApplication.instance(), dark=checked)
        self._apply_styles()

    def add_demo_data(self):
        if not list_projects(self.conn):
            add_project(self.conn, "Household Demo", "Sample Org")
        if not list_users(self.conn):
            add_user(self.conn, "Asha", "enumerator")
        proj = list_projects(self.conn)[0]
        forms = list_forms_by_project(self.conn, proj['id'])
        if not forms:
            add_form(self.conn, proj['id'], "Household Baseline", 1)
            f = list_forms_by_project(self.conn, proj['id'])[0]
            add_field(self.conn, f['id'], "Age", "number", True, None, 0)
            add_field(self.conn, f['id'], "Gender", "select", True, ["Male","Female","Other"], 1)
            add_field(self.conn, f['id'], "Household Size", "number", False, None, 2)
            add_field(self.conn, f['id'], "Village", "text", True, None, 3)
            add_field(self.conn, f['id'], "Visit Date", "date", True, None, 4)
        self._refresh_all_models()
        QtWidgets.QMessageBox.information(self, "Demo Ready", "Loaded demo project, user, and form.")

    def reset_database(self):
        if QtWidgets.QMessageBox.question(self, "Confirm", "This will ERASE all data. Continue?") == QtWidgets.QMessageBox.Yes:
            self.conn.close()
            if os.path.exists(DB_DEFAULT):
                os.remove(DB_DEFAULT)
            self.conn = connect(DB_DEFAULT)
            self.current_project_id = self.current_form_id = self.current_user_id = None
            self._refresh_all_models()
            QtWidgets.QMessageBox.information(self, "Reset", "Database reset. Start fresh!")

    def show_help(self):
        QtWidgets.QMessageBox.information(
            self, "Quick Help",
            "Step 1: Projects & Users → create a Project and a User (enumerator).\n"
            "Step 2: Form Designer → create a Form and add Fields.\n"
            "Step 3: Collect Data → choose Project, Form, User, fill and Save.\n"
            "Export/Import from File menu."
        )

# ==============================================
# Field dialog
# ==============================================
class FieldDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, init: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.setWindowTitle("📋 Field Editor")
        self.setMinimumWidth(500)
        self.resize(550, 400)
        init = init or {'label':'','ftype':'text','required':False,'options':[],'ord':0}
        
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(30, 30, 30, 30)
        
        form = QtWidgets.QFormLayout()
        form.setSpacing(16)
        form.setContentsMargins(0, 0, 0, 0)
        
        self.ent_label = QtWidgets.QLineEdit(init['label'])
        self.ent_label.setPlaceholderText("Enter field label...")
        self.ent_label.setMinimumHeight(40)
        
        self.cbo_type = QtWidgets.QComboBox()
        self.cbo_type.addItems(["text", "number", "select", "date", "path"])
        self.cbo_type.setCurrentText(init['ftype'])
        self.cbo_type.setMinimumHeight(40)
        
        self.chk_req = QtWidgets.QCheckBox()
        self.chk_req.setChecked(bool(init['required']))
        
        self.ent_opts = QtWidgets.QLineEdit(",".join(init['options']))
        self.ent_opts.setPlaceholderText("Option1, Option2, Option3 (for select type)")
        self.ent_opts.setMinimumHeight(40)
        
        self.ent_ord = QtWidgets.QSpinBox()
        self.ent_ord.setRange(0, 999)
        self.ent_ord.setValue(int(init['ord']))
        self.ent_ord.setMinimumHeight(40)
        
        form.addRow("📝 Label:", self.ent_label)
        form.addRow("🔧 Type:", self.cbo_type)
        form.addRow("⚠️ Required:", self.chk_req)
        form.addRow("📋 Options (comma-separated):", self.ent_opts)
        form.addRow("🔢 Order:", self.ent_ord)
        
        main_layout.addLayout(form)
        main_layout.addStretch()
        
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("✅ Save")
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("❌ Cancel")
        style_button(btns.button(QtWidgets.QDialogButtonBox.Ok), primary=True)
        style_button(btns.button(QtWidgets.QDialogButtonBox.Cancel), primary=False)
        main_layout.addWidget(btns)
        
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

    def data(self) -> Dict[str, Any]:
        opts = [s.strip() for s in self.ent_opts.text().split(',') if s.strip()]
        return {
            'label': self.ent_label.text().strip(),
            'ftype': self.cbo_type.currentText(),
            'required': self.chk_req.isChecked(),
            'options': opts or None,
            'ord': int(self.ent_ord.value()),
        }

# ==============================================
# Entrypoint
# ==============================================

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("FieldSurvey GO")
    app.setOrganizationName("FieldSurvey")
    apply_fusion_theme(app, dark=False)
    w = MainWindow(DB_DEFAULT)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
