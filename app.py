#!/usr/bin/env python3
"""
vidforge — Video Studio
=======================
Desktop front end for the vidforge pipeline: pick a topic, watch it become a
finished narrated video, then review and upload from the library.

The pipeline runs in a worker thread and reports through vidforge.progress, so
the GUI and `main.py` drive exactly the same code.

Run:  python app.py   (needs PySide6 — see requirements.txt)
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vidforge import history, ideation, llm, pipeline, progress
from vidforge.config import (
    CONFIG_PATH,
    MUSIC_DIR,
    Config,
    ensure_user_root,
    optional_key,
    output_root,
)
from vidforge.ffmpeg_utils import FFmpegError, ffmpeg_bin, has_filter
from vidforge.progress import STAGES, Cancelled, Reporter

APP_TITLE = "vidforge"
APP_SUBTITLE = "topic in, finished video out"


# ----------------------------------------------------------------------------
# Style — matches the Backup Control Center app in this lab
# ----------------------------------------------------------------------------
APP_STYLE = """
QWidget { background: #f3f4f7; color: #1f2430; font-size: 13px; }
/* Labels and checkboxes must not paint the window colour over a white card. */
QLabel, QCheckBox { background: transparent; }
#ScrollArea, #ScrollContent { background: #f3f4f7; border: none; }
#AppTitle { font-size: 22px; font-weight: 700; color: #1f2430; }
#AppSubtitle { color: #6b7280; font-size: 12px; }
#Card { background: #ffffff; border-radius: 14px; }
#CardTitle { font-size: 14px; font-weight: 700; color: #1f2430; }
#CardSubtitle { color: #6b7280; font-size: 11px; }
#StageActive { color: #2f6fed; font-weight: 600; }
#StageDone { color: #16a34a; }
#StagePending { color: #9ca3af; }
#Hint { color: #6b7280; font-size: 11px; }
#Warn { color: #b45309; font-size: 11px; }
#ItemTitle { font-weight: 600; font-size: 13px; }
#ItemMeta { color: #6b7280; font-size: 11px; }
QPushButton {
    background: #2f6fed; color: white; border: none;
    border-radius: 8px; padding: 7px 14px; font-weight: 600;
}
QPushButton:hover { background: #2860d6; }
QPushButton:pressed { background: #2050ba; }
QPushButton:disabled { background: #c4cbe0; color: #f0f1f5; }
QPushButton[secondary="true"] { background: #eef0f6; color: #1f2430; }
QPushButton[secondary="true"]:hover { background: #e1e4ee; }
QPushButton[secondary="true"]:disabled { background: #f4f5f9; color: #b9bfd0; }
QPushButton[danger="true"] { background: #ef4444; }
QPushButton[danger="true"]:hover { background: #dc2626; }
QPushButton[danger="true"]:disabled { background: #f0d2d2; color: #fbeaea; }
QTabWidget::pane { border: none; background: #f3f4f7; }
QTabBar::tab {
    background: transparent; color: #6b7280;
    padding: 8px 16px; font-weight: 600; border: none;
}
QTabBar::tab:selected { color: #1f2430; border-bottom: 2px solid #2f6fed; }
QLineEdit, QComboBox, QSpinBox {
    background: #ffffff; color: #1f2430; border: 1px solid #d1d5db;
    border-radius: 6px; padding: 6px 8px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #2f6fed; }
QListWidget {
    background: #fafbfc; border: 1px solid #e5e7eb;
    border-radius: 8px; padding: 4px;
}
QListWidget::item:selected { background: #e6edfd; color: #1f2430; }
/* Text areas are light input fields by default; only #Console reads as a log. */
QPlainTextEdit, QTextBrowser {
    background: #ffffff; color: #1f2430;
    border: 1px solid #d1d5db; border-radius: 6px; padding: 4px 6px;
}
QPlainTextEdit:focus, QTextBrowser:focus { border: 1px solid #2f6fed; }
#Console {
    background: #11151c; color: #d7dce3; border: none; border-radius: 8px;
    padding: 6px 8px; font-family: Menlo, monospace; font-size: 11px;
}
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #b6bccb; border-radius: 4px; background: #ffffff;
}
QCheckBox::indicator:checked {
    background: #2f6fed; border: 1px solid #2f6fed;
    image: url(none);
}
QCheckBox::indicator:hover { border: 1px solid #2f6fed; }
QProgressBar {
    border: none; border-radius: 5px; background: #e5e7eb;
    height: 10px; text-align: center;
}
QProgressBar::chunk { border-radius: 5px; background: #2f6fed; }
QDialog { background: #f3f4f7; }
"""


def shadow() -> QGraphicsDropShadowEffect:
    eff = QGraphicsDropShadowEffect()
    eff.setBlurRadius(18)
    eff.setOffset(0, 3)
    eff.setColor(QColor(0, 0, 0, 30))
    return eff


class Card(QFrame):
    def __init__(self, title: str | None = None, subtitle: str | None = None):
        super().__init__()
        self.setObjectName("Card")
        self.setGraphicsEffect(shadow())
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(18, 16, 18, 18)
        self.vbox.setSpacing(10)
        if title:
            head = QVBoxLayout()
            head.setSpacing(2)
            label = QLabel(title)
            label.setObjectName("CardTitle")
            head.addWidget(label)
            if subtitle:
                sub = QLabel(subtitle)
                sub.setObjectName("CardSubtitle")
                sub.setWordWrap(True)
                head.addWidget(sub)
            self.vbox.addLayout(head)

    def body(self, item) -> None:
        if isinstance(item, QWidget):
            self.vbox.addWidget(item)
        else:
            self.vbox.addLayout(item)


def secondary(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("secondary", True)
    return button


def _left(*widgets: QWidget) -> QHBoxLayout:
    """Keep buttons at their natural width instead of stretching to the card."""
    row = QHBoxLayout()
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch()
    return row


def danger(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("danger", True)
    return button


def reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path.parent)], check=False)


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


# ----------------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------------


class GuiReporter(Reporter):
    """Turns pipeline events into Qt signals (emitted from the worker thread)."""

    def __init__(self, worker: "ProduceWorker"):
        super().__init__()
        self._worker = worker

    def on_stage(self, stage_key: str, detail: str) -> None:
        self._worker.stage.emit(stage_key, detail)
        self._worker.overall.emit(int(progress.overall_fraction(stage_key) * 100))

    def on_progress(self, stage_key: str, fraction: float, detail: str) -> None:
        self._worker.overall.emit(
            int(progress.overall_fraction(stage_key, fraction) * 100)
        )
        if detail:
            self._worker.detail.emit(detail)

    def on_log(self, message: str) -> None:
        self._worker.log.emit(message)


class ProduceWorker(QThread):
    stage = Signal(str, str)
    overall = Signal(int)
    detail = Signal(str)
    log = Signal(str)
    finished_ok = Signal(str)      # slug
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, cfg: Config, topic: str | None, resume: str | None, count: int):
        super().__init__()
        self.cfg = cfg
        self.topic = topic
        self.resume = resume
        self.count = count
        self.reporter = GuiReporter(self)

    def stop(self) -> None:
        self.reporter.cancel()

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            last = None
            for i in range(self.count):
                if self.count > 1:
                    self.log.emit(f"── video {i + 1} of {self.count} ──")
                build = pipeline.produce(
                    self.cfg,
                    topic=self.topic if i == 0 else None,
                    resume_slug=self.resume if i == 0 else None,
                    reporter=self.reporter,
                )
                last = build.slug
            self.finished_ok.emit(last or "")
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.failed.emit(f"{exc}")
            self.log.emit(traceback.format_exc())


class SuggestWorker(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, cfg: Config, count: int):
        super().__init__()
        self.cfg = cfg
        self.count = count

    def run(self) -> None:  # noqa: D102
        try:
            self.done.emit(ideation.suggest(self.cfg, self.count))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class UploadWorker(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, cfg: Config, slug: str, privacy: str):
        super().__init__()
        self.cfg = cfg
        self.slug = slug
        self.privacy = privacy

    def run(self) -> None:  # noqa: D102
        from vidforge import youtube

        try:
            root = output_root() / self.slug
            meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
            thumb = root / "thumbnail.jpg"
            result = youtube.upload(
                self.cfg,
                root / f"{self.slug}.mp4",
                meta,
                privacy=self.privacy,
                thumbnail=thumb if thumb.exists() else None,
            )
            history.record(
                {
                    "slug": self.slug,
                    "published": result["privacy"] == "public",
                    "privacy": result["privacy"],
                    "video_id": result["video_id"],
                    "url": result["url"],
                    "uploaded": history.utcnow(),
                }
            )
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ----------------------------------------------------------------------------
# Produce tab
# ----------------------------------------------------------------------------


class ProduceTab(QWidget):
    produced = Signal()

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window
        self.worker: ProduceWorker | None = None
        self.stage_labels: dict[str, QLabel] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        # ---- what to make -------------------------------------------------
        setup = Card(
            "What should it make?",
            "Leave the topic blank to take the next unused line from the queue.",
        )
        form = QFormLayout()
        form.setSpacing(9)

        self.topic_edit = QLineEdit()
        self.topic_edit.setPlaceholderText("e.g. How undersea cables carry the internet")
        form.addRow("Topic", self.topic_edit)

        self.length = QSpinBox()
        self.length.setRange(30, 3600)
        self.length.setSingleStep(30)
        self.length.setSuffix(" s")
        form.addRow("Target length", self.length)

        self.visuals = QComboBox()
        self.visuals.addItems(["ai", "pexels", "gradient"])
        form.addRow("Visuals", self.visuals)

        self.provider = QComboBox()
        self.provider.addItems(["openai", "anthropic"])
        form.addRow("Script model", self.provider)

        self.count = QSpinBox()
        self.count.setRange(1, 20)
        form.addRow("How many", self.count)

        toggles = QHBoxLayout()
        self.captions_on = QCheckBox("Captions")
        self.music_on = QCheckBox("Music bed")
        self.transitions_on = QCheckBox("Crossfades")
        for box in (self.captions_on, self.music_on, self.transitions_on):
            toggles.addWidget(box)
        toggles.addStretch()
        form.addRow("", toggles)

        setup.body(form)

        self.estimate = QLabel()
        self.estimate.setObjectName("Hint")
        self.estimate.setWordWrap(True)
        setup.body(self.estimate)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Produce video")
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = danger("Stop")
        self.cancel_btn.clicked.connect(self.stop)
        self.cancel_btn.setEnabled(False)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch()
        setup.body(buttons)
        outer.addWidget(setup)

        # ---- progress -----------------------------------------------------
        run_card = Card("Progress")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        run_card.body(self.bar)

        self.detail_label = QLabel("Idle")
        self.detail_label.setObjectName("Hint")
        run_card.body(self.detail_label)

        stages_row = QHBoxLayout()
        stages_row.setSpacing(12)
        for key, label, _ in STAGES:
            widget = QLabel(f"○ {label}")
            widget.setObjectName("StagePending")
            self.stage_labels[key] = widget
            stages_row.addWidget(widget)
        stages_row.addStretch()
        run_card.body(stages_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("Console")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(220)
        run_card.body(self.log_view)
        outer.addWidget(run_card)
        outer.addStretch()

        self.load_from_config()
        for signal in (
            self.length.valueChanged,
            self.count.valueChanged,
        ):
            signal.connect(self.update_estimate)
        self.visuals.currentTextChanged.connect(self.update_estimate)
        self.update_estimate()

    # -- config round-trip -------------------------------------------------
    def load_from_config(self) -> None:
        cfg = self.window.cfg
        self.length.setValue(int(cfg.get("script.target_seconds", 480)))
        self.visuals.setCurrentText(str(cfg.get("visuals.source", "ai")))
        self.provider.setCurrentText(str(cfg.get("script.provider", "openai")))
        self.captions_on.setChecked(bool(cfg.get("captions.enabled", True)))
        self.music_on.setChecked(bool(cfg.get("music.enabled", True)))
        self.transitions_on.setChecked(
            str(cfg.get("video.transition", "xfade")).lower() == "xfade"
        )

    def run_config(self) -> Config:
        cfg = Config.load()
        cfg.apply_overrides(
            {
                "script.target_seconds": self.length.value(),
                "script.provider": self.provider.currentText(),
                "visuals.source": self.visuals.currentText(),
                "captions.enabled": self.captions_on.isChecked(),
                "music.enabled": self.music_on.isChecked(),
                "video.transition": "xfade" if self.transitions_on.isChecked() else "cut",
            }
        )
        return cfg

    def update_estimate(self) -> None:
        seconds = self.length.value()
        scenes = max(4, round(seconds / 14))
        images = (scenes + 1) * 0.04 if self.visuals.currentText() == "ai" else 0.0
        words = seconds / 60 * 155
        cost = 0.04 + images + (words * 6 / 4 / 1e6 * 0.60) + (words / 155 * 0.006)
        minutes = max(2, round(seconds / 60 * 4))
        self.estimate.setText(
            f"About {scenes} scenes · roughly ${cost * self.count.value():.2f} in API "
            f"calls · {minutes}–{minutes * 2} min to render"
        )

    # -- run ---------------------------------------------------------------
    def start(self, _checked: bool = False, resume: str | None = None) -> None:
        ok, message = llm.provider_available(self.window.cfg)
        if not ok:
            QMessageBox.warning(self, "Missing API key", message)
            return

        cfg = self.run_config()
        topic = self.topic_edit.text().strip() or None

        self.log_view.clear()
        self.bar.setValue(0)
        for key, label, _ in STAGES:
            self.stage_labels[key].setText(f"○ {label}")
            self.stage_labels[key].setObjectName("StagePending")
            self.stage_labels[key].setStyleSheet("")
        self.window.apply_style()

        self.worker = ProduceWorker(cfg, topic, resume, self.count.value())
        self.worker.stage.connect(self.on_stage)
        self.worker.overall.connect(self.bar.setValue)
        self.worker.detail.connect(self.detail_label.setText)
        self.worker.log.connect(self.append_log)
        self.worker.finished_ok.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.start()

        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.detail_label.setText("Starting…")

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.detail_label.setText("Stopping after the current step…")
            self.cancel_btn.setEnabled(False)

    def on_stage(self, key: str, detail: str) -> None:
        reached = False
        for stage_key, label, _ in STAGES:
            widget = self.stage_labels[stage_key]
            if stage_key == key:
                widget.setText(f"● {label}")
                widget.setObjectName("StageActive")
                reached = True
            elif not reached:
                widget.setText(f"✓ {label}")
                widget.setObjectName("StageDone")
        self.window.apply_style()
        self.detail_label.setText(
            f"{progress.stage_label(key)} {detail}".strip()
        )

    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)
        self.log_view.moveCursor(QTextCursor.End)

    def _reset_buttons(self) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.worker = None

    def on_done(self, slug: str) -> None:
        for key, label, _ in STAGES:
            self.stage_labels[key].setText(f"✓ {label}")
            self.stage_labels[key].setObjectName("StageDone")
        self.window.apply_style()
        self.bar.setValue(100)
        self.detail_label.setText("Done")
        self._reset_buttons()
        self.produced.emit()
        if slug:
            self.window.show_library(slug)

    def on_failed(self, message: str) -> None:
        self.detail_label.setText("Failed")
        self._reset_buttons()
        QMessageBox.critical(self, "Render failed", message)

    def on_cancelled(self) -> None:
        self.detail_label.setText("Stopped — progress is saved, use Resume in Library")
        self.append_log("stopped by user; finished stages are cached")
        self._reset_buttons()
        self.produced.emit()


# ----------------------------------------------------------------------------
# Library tab
# ----------------------------------------------------------------------------


class UploadDialog(QDialog):
    def __init__(self, parent: QWidget, cfg: Config, slug: str, title: str):
        super().__init__(parent)
        self.setWindowTitle("Upload to YouTube")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        heading = QLabel(title)
        heading.setObjectName("CardTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        form = QFormLayout()
        self.privacy = QComboBox()
        self.privacy.addItems(["private", "unlisted", "public"])
        form.addRow("Visibility", self.privacy)
        layout.addLayout(form)

        self.note = QLabel()
        self.note.setObjectName("Warn")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.cfg = cfg
        self.privacy.currentTextChanged.connect(self.refresh_note)
        self.refresh_note(self.privacy.currentText())

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Upload")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.buttons = buttons

    def refresh_note(self, value: str) -> None:
        if value != "public":
            self.note.setText(
                "The video will be uploaded but not listed publicly. You can change "
                "visibility later in YouTube Studio."
            )
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
            return

        enabled = bool(self.cfg.get("youtube.enabled", False))
        if enabled:
            self.note.setText(
                "This publishes to your channel immediately and anyone can watch it. "
                "Make sure you have read the script and checked the facts."
            )
        else:
            self.note.setText(
                "Publishing publicly is disabled. Turn on 'Allow public uploads' in "
                "Settings first — this second switch exists so an unattended run can "
                "never publish by accident."
            )
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(enabled)


class LibraryTab(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window
        self.entries: list[dict] = []
        self.upload_worker: UploadWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        card = Card("Library", "Everything produced so far.")
        row = QHBoxLayout()
        row.setSpacing(14)

        self.list = QListWidget()
        self.list.setMinimumWidth(330)
        self.list.currentRowChanged.connect(self.show_detail)
        row.addWidget(self.list, 1)

        detail = QVBoxLayout()
        detail.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(320, 180)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background: #e5e7eb; border-radius: 8px;")
        detail.addWidget(self.thumb)

        self.title_label = QLabel("Select a video")
        self.title_label.setObjectName("ItemTitle")
        self.title_label.setWordWrap(True)
        detail.addWidget(self.title_label)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("ItemMeta")
        self.meta_label.setWordWrap(True)
        detail.addWidget(self.meta_label)

        actions = QHBoxLayout()
        self.play_btn = secondary("Play")
        self.play_btn.clicked.connect(self.play)
        self.reveal_btn = secondary("Show files")
        self.reveal_btn.clicked.connect(self.reveal)
        self.resume_btn = secondary("Resume")
        self.resume_btn.clicked.connect(self.resume)
        self.upload_btn = QPushButton("Upload…")
        self.upload_btn.clicked.connect(self.upload)
        for button in (self.play_btn, self.reveal_btn, self.resume_btn, self.upload_btn):
            actions.addWidget(button)
        actions.addStretch()
        detail.addLayout(actions)

        self.desc = QTextBrowser()
        self.desc.setObjectName("Console")
        self.desc.setMinimumHeight(150)
        detail.addWidget(self.desc)
        row.addLayout(detail, 1)

        card.body(row)
        outer.addWidget(card)

        self.refresh()

    def refresh(self) -> None:
        current = self.list.currentRow()
        self.entries = list(reversed(history.load()))
        self.list.clear()
        for entry in self.entries:
            if not entry.get("complete", True):
                meta = "unfinished — select to resume"
            else:
                minutes = entry.get("duration_seconds", 0) / 60
                state = entry.get("privacy") or "local"
                meta = f"{minutes:.1f} min · {state}"
            self.list.addItem(
                QListWidgetItem(f"{entry.get('title', entry['slug'])}\n{meta}")
            )
        if self.entries:
            self.list.setCurrentRow(min(max(current, 0), len(self.entries) - 1))

    def select_slug(self, slug: str) -> None:
        for i, entry in enumerate(self.entries):
            if entry["slug"] == slug:
                self.list.setCurrentRow(i)
                return

    def current(self) -> dict | None:
        row = self.list.currentRow()
        if 0 <= row < len(self.entries):
            return self.entries[row]
        return None

    def show_detail(self, _row: int) -> None:
        entry = self.current()
        if not entry:
            return
        root = output_root() / entry["slug"]

        thumb = root / "thumbnail.jpg"
        if thumb.exists():
            pixmap = QPixmap(str(thumb)).scaled(
                320, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.thumb.setPixmap(pixmap)
        else:
            self.thumb.clear()
            self.thumb.setText("no thumbnail")

        self.title_label.setText(entry.get("title", entry["slug"]))

        bits = [f"{entry.get('duration_seconds', 0) / 60:.1f} min"]
        manifest = root / "manifest.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                cost = data.get("estimated_cost_usd", {}).get("total")
                if cost is not None:
                    bits.append(f"~${cost:.2f}")
                stages = data.get("stages", {})
                if "assemble" not in stages:
                    bits.append("incomplete")
            except json.JSONDecodeError:
                pass
        if entry.get("url"):
            bits.append(entry["url"])
        self.meta_label.setText(" · ".join(bits))

        meta_path = root / "metadata.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.desc.setPlainText(
                f"{meta['title']}\n\n{meta['description']}\n\n"
                f"Tags: {', '.join(meta.get('tags', []))}"
            )
        else:
            self.desc.setPlainText("(no metadata yet — render did not finish)")

        video = root / f"{entry['slug']}.mp4"
        self.play_btn.setEnabled(video.exists())
        self.upload_btn.setEnabled(video.exists() and meta_path.exists())
        self.resume_btn.setEnabled(not video.exists())

    def play(self) -> None:
        entry = self.current()
        if entry:
            open_file(output_root() / entry["slug"] / f"{entry['slug']}.mp4")

    def reveal(self) -> None:
        entry = self.current()
        if entry:
            reveal(output_root() / entry["slug"])

    def resume(self) -> None:
        entry = self.current()
        if not entry:
            return
        self.window.tabs.setCurrentIndex(0)
        self.window.produce_tab.start(resume=entry["slug"])

    def upload(self) -> None:
        entry = self.current()
        if not entry:
            return
        dialog = UploadDialog(
            self, self.window.cfg, entry["slug"], entry.get("title", entry["slug"])
        )
        if dialog.exec() != QDialog.Accepted:
            return

        privacy = dialog.privacy.currentText()
        if privacy == "public":
            confirm = QMessageBox.question(
                self,
                "Publish publicly?",
                f"This will publish “{entry.get('title')}” to your YouTube channel "
                "where anyone can watch it.\n\nPublish now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        self.upload_btn.setEnabled(False)
        self.upload_btn.setText("Uploading…")
        self.upload_worker = UploadWorker(self.window.cfg, entry["slug"], privacy)
        self.upload_worker.done.connect(self.on_uploaded)
        self.upload_worker.failed.connect(self.on_upload_failed)
        self.upload_worker.start()

    def on_uploaded(self, result: dict) -> None:
        self.upload_btn.setText("Upload…")
        self.upload_btn.setEnabled(True)
        self.refresh()
        QMessageBox.information(
            self,
            "Uploaded",
            f"Uploaded as {result['privacy']}.\n\n{result['url']}",
        )

    def on_upload_failed(self, message: str) -> None:
        self.upload_btn.setText("Upload…")
        self.upload_btn.setEnabled(True)
        QMessageBox.critical(self, "Upload failed", message)


# ----------------------------------------------------------------------------
# Topics tab
# ----------------------------------------------------------------------------


class TopicsTab(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window
        self.worker: SuggestWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        card = Card(
            "Topic queue",
            "One per line. Producing a video takes the first line that has not been "
            "used yet. Saved to topics.txt.",
        )
        self.editor = QPlainTextEdit()
        self.editor.setMinimumHeight(320)
        card.body(self.editor)

        row = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        self.suggest_btn = secondary("Suggest 10 more")
        self.suggest_btn.clicked.connect(self.suggest)
        reload_btn = secondary("Reload")
        reload_btn.clicked.connect(self.load)
        row.addWidget(save)
        row.addWidget(self.suggest_btn)
        row.addWidget(reload_btn)
        row.addStretch()
        self.status = QLabel()
        self.status.setObjectName("Hint")
        row.addWidget(self.status)
        card.body(row)

        outer.addWidget(card)
        outer.addStretch()
        self.load()

    def load(self) -> None:
        queue = ideation.read_queue()
        self.editor.setPlainText("\n".join(queue))
        used = sum(1 for topic in queue if not history.is_new(topic))
        self.status.setText(f"{len(queue)} queued · {used} already produced")

    def save(self) -> None:
        lines = [
            line.strip()
            for line in self.editor.toPlainText().splitlines()
            if line.strip()
        ]
        from vidforge.config import TOPICS_PATH

        TOPICS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.load()
        self.status.setText(f"Saved {len(lines)} topics")

    def suggest(self) -> None:
        ok, message = llm.provider_available(self.window.cfg)
        if not ok:
            QMessageBox.warning(self, "Missing API key", message)
            return
        self.suggest_btn.setEnabled(False)
        self.suggest_btn.setText("Thinking…")
        self.worker = SuggestWorker(self.window.cfg, 10)
        self.worker.done.connect(self.on_suggested)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_suggested(self, ideas: list) -> None:
        self.suggest_btn.setEnabled(True)
        self.suggest_btn.setText("Suggest 10 more")
        added = ideation.append_to_queue([idea["topic"] for idea in ideas])
        self.load()
        self.status.setText(f"Added {added} new topics")

    def on_failed(self, message: str) -> None:
        self.suggest_btn.setEnabled(True)
        self.suggest_btn.setText("Suggest 10 more")
        QMessageBox.critical(self, "Could not generate ideas", message)


# ----------------------------------------------------------------------------
# Settings tab
# ----------------------------------------------------------------------------


class SettingsTab(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        channel = Card("Channel", "Steers what the script writer produces.")
        form = QFormLayout()
        form.setSpacing(9)
        self.name = QLineEdit()
        self.niche = QPlainTextEdit()
        self.niche.setMaximumHeight(70)
        self.audience = QLineEdit()
        self.voice = QComboBox()
        self.voice.addItems(
            ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"]
        )
        form.addRow("Name", self.name)
        form.addRow("Niche", self.niche)
        form.addRow("Audience", self.audience)
        form.addRow("Narrator voice", self.voice)
        channel.body(form)
        outer.addWidget(channel)

        publish = Card(
            "Publishing",
            "Uploads are always started by hand from the Library tab. This switch is "
            "the second of two required to publish publicly.",
        )
        self.allow_public = QCheckBox("Allow public uploads")
        publish.body(self.allow_public)
        save = QPushButton("Save settings")
        save.clicked.connect(self.save)
        publish.body(_left(save))
        outer.addWidget(publish)

        keys = Card(
            "API keys",
            "Read from the .env file next to your settings. Edit it there — the app "
            "never stores keys itself.",
        )
        self.keys_label = QLabel()
        self.keys_label.setObjectName("Hint")
        keys.body(self.keys_label)
        open_cfg = secondary("Open config folder")
        open_cfg.clicked.connect(lambda: reveal(CONFIG_PATH))
        keys.body(_left(open_cfg))
        outer.addWidget(keys)

        health = Card("Environment", "Same checks as `python main.py doctor`.")
        self.health = QPlainTextEdit()
        self.health.setObjectName("Console")
        self.health.setReadOnly(True)
        self.health.setMinimumHeight(170)
        health.body(self.health)
        recheck = secondary("Re-check")
        recheck.clicked.connect(self.check)
        health.body(_left(recheck))
        outer.addWidget(health)
        outer.addStretch()

        self.load()
        self.check()

    def load(self) -> None:
        cfg = self.window.cfg
        self.name.setText(str(cfg.get("channel.name", "")))
        self.niche.setPlainText(" ".join(str(cfg.get("channel.niche", "")).split()))
        self.audience.setText(str(cfg.get("channel.audience", "")))
        self.voice.setCurrentText(str(cfg.get("voice.voice", "onyx")))
        self.allow_public.setChecked(bool(cfg.get("youtube.enabled", False)))

    def save(self) -> None:
        import yaml

        from vidforge.config import CONFIG_PATH

        cfg = Config.load()
        cfg.set("channel.name", self.name.text().strip())
        cfg.set("channel.niche", self.niche.toPlainText().strip())
        cfg.set("channel.audience", self.audience.text().strip())
        cfg.set("voice.voice", self.voice.currentText())
        cfg.set("youtube.enabled", self.allow_public.isChecked())

        # Persist the produce-tab knobs too, so the app opens where you left it.
        produce = self.window.produce_tab
        cfg.set("script.target_seconds", produce.length.value())
        cfg.set("script.provider", produce.provider.currentText())
        cfg.set("visuals.source", produce.visuals.currentText())
        cfg.set("captions.enabled", produce.captions_on.isChecked())
        cfg.set("music.enabled", produce.music_on.isChecked())
        cfg.set(
            "video.transition",
            "xfade" if produce.transitions_on.isChecked() else "cut",
        )

        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg.as_dict(), fh, sort_keys=False, allow_unicode=True)

        self.window.cfg = Config.load()
        QMessageBox.information(self, "Saved", "Settings written to config.yaml")

    def check(self) -> None:
        self.keys_label.setText(
            "\n".join(
                f"{'✓' if optional_key(key) else '✗'}  {key} — {why}"
                for key, why in (
                    ("OPENAI_API_KEY", "required: script, narration, images, captions"),
                    ("ANTHROPIC_API_KEY", "optional: Claude script writer"),
                    ("PEXELS_API_KEY", "optional: stock visuals"),
                )
            )
        )

        lines: list[str] = []
        try:
            lines.append(f"ffmpeg      {ffmpeg_bin()}")
            for name in ("zoompan", "xfade", "overlay", "subtitles", "sidechaincompress"):
                lines.append(f"  {name:<20} {'ok' if has_filter(name) else 'missing'}")
            if not has_filter("subtitles"):
                lines.append("  (no libass — captions use the Pillow overlay renderer)")
        except FFmpegError as exc:
            lines.append(f"ffmpeg      MISSING — {exc}")

        lines.append("")
        for key, why in (
            ("OPENAI_API_KEY", "script, narration, images, captions"),
            ("ANTHROPIC_API_KEY", "optional Claude script writer"),
            ("PEXELS_API_KEY", "optional stock visuals"),
        ):
            lines.append(f"{key:<20} {'set' if optional_key(key) else 'not set':<8} {why}")

        tracks = (
            [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav")]
            if MUSIC_DIR.exists()
            else []
        )
        lines.append("")
        lines.append(f"music beds  {len(tracks)} in assets/music")
        lines.append(f"output      {output_root()}")
        self.health.setPlainText("\n".join(lines))


# ----------------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.setWindowTitle("vidforge — Video Studio")
        self.resize(1180, 900)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        header = QVBoxLayout()
        header.setSpacing(2)
        title = QLabel(APP_TITLE)
        title.setObjectName("AppTitle")
        subtitle = QLabel(APP_SUBTITLE)
        subtitle.setObjectName("AppSubtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.produce_tab = ProduceTab(self)
        self.library_tab = LibraryTab(self)
        self.topics_tab = TopicsTab(self)
        self.settings_tab = SettingsTab(self)

        for widget, label in (
            (self.produce_tab, "Produce"),
            (self.library_tab, "Library"),
            (self.topics_tab, "Topics"),
            (self.settings_tab, "Settings"),
        ):
            scroll = QScrollArea()
            scroll.setObjectName("ScrollArea")
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            self.tabs.addTab(scroll, label)

        self.produce_tab.produced.connect(self.library_tab.refresh)
        self.produce_tab.produced.connect(self.topics_tab.load)
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

    def apply_style(self) -> None:
        """Re-apply the sheet so objectName-driven colours refresh."""
        app = QApplication.instance()
        if app:
            app.setStyleSheet(APP_STYLE)

    def show_library(self, slug: str) -> None:
        self.library_tab.refresh()
        self.library_tab.select_slug(slug)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        worker = self.produce_tab.worker
        if worker and worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Still rendering",
                "A video is still being produced. Stop it and quit?\n\n"
                "Finished stages are cached, so you can resume later.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            worker.stop()
            worker.wait(5000)
        event.accept()


def main() -> int:
    ensure_user_root()  # seeds config/topics into Application Support in a .app build
    app = QApplication(sys.argv)
    app.setApplicationName("vidforge")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
