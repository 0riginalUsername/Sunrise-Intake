"""
classify_3dr.py - Cyclone 3DR automatic point cloud classification

Invoked after the DJI Terra EXE completes. Finds LAZ/LAS files in the
Terra output folder and runs ClassifyLAZ.js on each one via Cyclone 3DR.

Retry logic: if any file fails on the first attempt and the current time
is between 8 AM – 5 PM MST (business hours when 3DR may be in use), the
thread sleeps until 5 PM MST and retries those files once more. Outside
business hours a single attempt is made and failures are reported as-is.
"""

import datetime
import os
import subprocess
import time
from typing import List

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QWidget


# ---------------------------------------------------------------------------
# Paths — update if the installation differs
# ---------------------------------------------------------------------------

CYCLONE_3DR_EXE   = r"C:\Program Files\Leica Geosystems\Cyclone 3DR\3DR.exe"
CLASSIFY_SCRIPT   = r"Z:\Survey\UT\_Scripts\3DR\LAZ-Classify\ClassifyLAZ.js"
GET_MODELS_SCRIPT = r"Z:\Survey\UT\_Scripts\3DR\LAZ-Classify\GetModels.js"

# ---------------------------------------------------------------------------
# Available classification models (as shown in Cyclone 3DR)
# ---------------------------------------------------------------------------

CLASSIFICATION_MODELS: List[str] = [
    "BLK Mobile Filter People 2.0",
    "Heavy Construction UAV 2.0",
    "Indoor 2.2",
    "Indoor Construction Site 1.3",
    "Outdoor TLS 2.1",
    "Plant 2.0",
    "Road 1.0",
]


# ---------------------------------------------------------------------------
# Business-hours helpers (MST = UTC-7, fixed offset)
# ---------------------------------------------------------------------------

def _now_mst() -> datetime.datetime:
    return datetime.datetime.utcnow() - datetime.timedelta(hours=7)


def _is_business_hours_mst() -> bool:
    """True while current MST time is between 08:00 and 17:00 (8 AM – 5 PM)."""
    hour = _now_mst().hour
    return 8 <= hour < 17


def _seconds_until_5pm_mst() -> float:
    """Seconds from now until 17:00 MST today (0 if already past 5 PM)."""
    now = _now_mst()
    end = now.replace(hour=17, minute=0, second=0, microsecond=0)
    return max(0.0, (end - now).total_seconds())


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def find_laz_files(root: str) -> List[str]:
    """Recursively find all .las/.laz files under root."""
    results = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.lower().endswith((".las", ".laz")):
                results.append(os.path.join(dirpath, f))
    return results


# ---------------------------------------------------------------------------
# Terra reconstruction wait helper
# ---------------------------------------------------------------------------

def _wait_for_terra_reconstruction(
    status_cb=None,
    poll_secs: int = 20,
    timeout_hours: int = 8,
    appear_timeout_secs: int = 600,
) -> None:
    """Block until DJI Terra finishes reconstruction.

    While a reconstruction runs, DJI Terra shows a Text element whose Name starts
    with 'Reconstruction in progress...'; that element changes/disappears when it
    completes. We wait in two phases so the classifier never jumps the gun:

      Phase 1 — confirm reconstruction actually STARTED (the in-progress text
                appears). Without this, the very first poll can fire before Terra
                has rendered the indicator and instantly (wrongly) conclude 'done'.
      Phase 2 — wait until the in-progress text is gone for several CONSECUTIVE
                checks. Transient UIA/connect failures are treated as 'keep
                waiting', not 'finished', so a momentary hiccup can't end it early.

    Matching is done with prefix regexes (window title contains 'DJI Terra', the
    indicator Name starts with 'Reconstruction in progress') so small UI drift —
    a percent suffix, trailing space, version in the title — can't break it.
    """
    try:
        from pywinauto import Desktop
    except ImportError:
        if status_cb:
            status_cb("[3DR] pywinauto not available — skipping reconstruction wait")
        return

    def _find_terra_window():
        """Return the DJI Terra top-level window wrapper, or None.

        Found among Chromium windows (class Chrome_WidgetWin_1) either by the window
        title containing 'DJI Terra' or, failing that, by it containing the
        'DJI Terra' document node — whose Name is stable regardless of the window
        title (the reconstruction text lives under that document).
        """
        cands = Desktop(backend="uia").windows(class_name="Chrome_WidgetWin_1")
        for w in cands:
            try:
                if "DJI Terra" in (w.window_text() or ""):
                    return w
            except Exception:
                continue
        for w in cands:
            try:
                for doc in w.descendants(control_type="Document"):
                    if (doc.window_text() or "") == "DJI Terra":
                        return w
            except Exception:
                continue
        return None

    def _in_progress():
        """True = indicator present, False = confirmed absent, None = Terra unreachable."""
        try:
            w = _find_terra_window()
            if w is None:
                return None
            for txt in w.descendants(control_type="Text"):
                try:
                    if (txt.window_text() or "").startswith("Reconstruction in progress"):
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return None

    # --- Phase 1: wait for reconstruction to START (indicator appears) ---
    appear_deadline = time.time() + appear_timeout_secs
    started = False
    while time.time() < appear_deadline:
        if _in_progress() is True:
            started = True
            break
        if status_cb:
            status_cb("[3DR] Waiting for Terra reconstruction to start...")
        time.sleep(5)

    if not started:
        if status_cb:
            status_cb(
                f"[3DR] Did not see 'Reconstruction in progress' within "
                f"{appear_timeout_secs}s — assuming it already finished and proceeding."
            )
        return

    if status_cb:
        status_cb("[3DR] Reconstruction in progress — waiting for it to finish...")

    # --- Phase 2: wait for reconstruction to COMPLETE (indicator gone) ---
    deadline = time.time() + timeout_hours * 3600
    start = time.time()
    gone_streak = 0          # consecutive checks confirming the indicator is absent
    unreachable_streak = 0   # consecutive checks Terra couldn't be reached at all
    unreachable_limit = max(1, 300 // poll_secs)   # ~5 min of silence ⇒ Terra closed

    while time.time() < deadline:
        state = _in_progress()
        if state is True:
            gone_streak = 0
            unreachable_streak = 0
            elapsed_min = int((time.time() - start) / 60)
            if status_cb:
                status_cb(f"[3DR] Waiting for Terra reconstruction... ({elapsed_min}m elapsed)")
        elif state is False:
            unreachable_streak = 0
            gone_streak += 1
            if gone_streak >= 2:
                if status_cb:
                    status_cb("[3DR] Reconstruction complete.")
                return
        else:  # None — couldn't reach Terra this tick
            gone_streak = 0
            unreachable_streak += 1
            if unreachable_streak >= unreachable_limit:
                if status_cb:
                    status_cb("[3DR] Terra window gone for several minutes — assuming reconstruction ended.")
                return
            if status_cb:
                status_cb("[3DR] Terra UI busy/unreachable — still waiting...")
        time.sleep(poll_secs)

    if status_cb:
        status_cb(f"[3DR] Warning: timed out waiting for reconstruction after {timeout_hours}h — proceeding anyway")


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------


class Classify3DRThread(QThread):
    """Classifies every LAZ/LAS file in terra_folder using Cyclone 3DR scripts.

    First pass: attempt each file once.
    If any fail and current time is 8 AM – 5 PM MST, wait until 5 PM then
    retry those files once. Outside business hours no retry is attempted.
    """

    status_update           = pyqtSignal(str)
    classification_complete = pyqtSignal(int, int)   # (succeeded, total)

    def __init__(self, terra_folder: str, model_name: str, parent=None):
        super().__init__(parent)
        self._terra_folder = terra_folder
        self._model_name   = model_name

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_one(self, path: str) -> bool:
        """Run ClassifyLAZ.js on a single file. Returns True on success."""
        js_path = path.replace("\\", "\\\\")
        param   = f"var inputFile='{js_path}'; var modelName='{self._model_name}';"
        try:
            result = subprocess.run(
                [CYCLONE_3DR_EXE,
                 f"--Script={CLASSIFY_SCRIPT}",
                 "--scriptAutorun",
                 "--silent",
                 f"--scriptParam={param}"],
                timeout=300,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            self.status_update.emit(
                f"[3DR] Timed out: {os.path.basename(path)}"
            )
        except Exception as exc:
            self.status_update.emit(
                f"[3DR] Error on {os.path.basename(path)}: {exc}"
            )
        return False

    # ------------------------------------------------------------------
    # Thread entry point
    # ------------------------------------------------------------------

    def run(self):
        # Wait for DJI Terra to finish reconstruction before scanning for output files
        self.status_update.emit("[3DR] Waiting for DJI Terra reconstruction to complete...")
        _wait_for_terra_reconstruction(status_cb=self.status_update.emit)
        self.status_update.emit("[3DR] Reconstruction complete — scanning for LAZ/LAS files...")

        laz_files = find_laz_files(self._terra_folder)
        total     = len(laz_files)

        if total == 0:
            self.status_update.emit("[3DR] No LAZ/LAS files found in Terra output folder.")
            self.classification_complete.emit(0, 0)
            return

        # --- First pass ---
        succeeded = 0
        failed: List[str] = []

        for i, path in enumerate(laz_files, 1):
            self.status_update.emit(
                f"[3DR] Classifying [{i}/{total}]: {os.path.basename(path)}"
            )
            if self._classify_one(path):
                succeeded += 1
            else:
                self.status_update.emit(
                    f"[3DR] Failed (first attempt): {os.path.basename(path)}"
                )
                failed.append(path)

        # --- Retry after business hours if needed ---
        if failed:
            if _is_business_hours_mst():
                wait_secs = _seconds_until_5pm_mst()
                hrs  = int(wait_secs // 3600)
                mins = int((wait_secs % 3600) // 60)
                self.status_update.emit(
                    f"[3DR] {len(failed)} file(s) failed during business hours "
                    f"(8 AM – 5 PM MST). Waiting {hrs}h {mins}m until 5 PM MST to retry..."
                )
                time.sleep(wait_secs)
                self.status_update.emit(
                    f"[3DR] Retrying {len(failed)} file(s) after business hours..."
                )
                retry_failed: List[str] = []
                for path in failed:
                    self.status_update.emit(
                        f"[3DR] Retry: {os.path.basename(path)}"
                    )
                    if self._classify_one(path):
                        succeeded += 1
                    else:
                        self.status_update.emit(
                            f"[3DR] Still failed after retry: {os.path.basename(path)}"
                        )
                        retry_failed.append(path)
                failed = retry_failed
            else:
                self.status_update.emit(
                    f"[3DR] {len(failed)} file(s) failed. "
                    "Outside business hours — no retry scheduled."
                )

        self.status_update.emit(
            f"[3DR] Classification complete: {succeeded}/{total} files processed."
        )
        self.classification_complete.emit(succeeded, total)


# ---------------------------------------------------------------------------
# UI widget
# ---------------------------------------------------------------------------

class Classify3DRWidget(QWidget):
    """
    Compact row: [checkbox] [label] [model combo]

    Meant to live inside the DJI Terra Parameters box.
    Models are populated from the static CLASSIFICATION_MODELS list — 3DR is
    never launched to build the dropdown.
    Call is_enabled / selected_model to read state before launching the thread.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self.enabled_check = QCheckBox("Auto-classify with Cyclone 3DR after Terra")
        self.enabled_check.setFont(QFont("Segoe UI", 10))
        self.enabled_check.setStyleSheet("color: #113e59; border: none;")
        layout.addWidget(self.enabled_check)

        model_label = QLabel("Model:")
        model_label.setFont(QFont("Segoe UI", 10))
        model_label.setStyleSheet("color: #113e59; background: transparent; border: none;")
        layout.addWidget(model_label)

        self.model_combo = QComboBox()
        self.model_combo.setFont(QFont("Segoe UI", 10))
        self.model_combo.setMinimumWidth(220)
        self.model_combo.setEnabled(False)
        self.model_combo.addItems(CLASSIFICATION_MODELS)
        layout.addWidget(self.model_combo)

        layout.addStretch()

        self.enabled_check.stateChanged.connect(
            lambda state: self.model_combo.setEnabled(bool(state))
        )

    @property
    def is_enabled(self) -> bool:
        return self.enabled_check.isChecked()

    @property
    def selected_model(self) -> str:
        return self.model_combo.currentText() if self.is_enabled else ""
