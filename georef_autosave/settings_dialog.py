"""
Settings dialog for Georeferencer Autosave.
All values are persisted via QgsSettings across QGIS sessions.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QSpinBox,
    QCheckBox, QDialogButtonBox, QLabel, QFrame, QSizePolicy
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsSettings

SETTINGS_PREFIX = "georef_autosave"

DEFAULTS = {
    "debounce_ms":        1500,
    "separate_file":      False,
    "show_status_label":  True,
    "show_message_bar":   False,
}


def get_setting(key, type_=None):
    s = QgsSettings()
    default = DEFAULTS[key]
    t = type_ if type_ is not None else type(default)
    return s.value(f"{SETTINGS_PREFIX}/{key}", default, type=t)


def set_setting(key, value):
    QgsSettings().setValue(f"{SETTINGS_PREFIX}/{key}", value)


class GeorefAutosaveSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Georeferencer Autosave — Settings")
        self.setMinimumWidth(400)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._build_ui()
        self._load()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── Save timing ───────────────────────────────────────────────
        timing_label = QLabel("<b>Save timing</b>")
        root.addWidget(timing_label)

        form = QFormLayout()
        form.setContentsMargins(12, 0, 0, 0)

        self.debounce_spin = QSpinBox()
        self.debounce_spin.setRange(250, 10000)
        self.debounce_spin.setSingleStep(250)
        self.debounce_spin.setSuffix(" ms")
        self.debounce_spin.setToolTip(
            "How long to wait after the last GCP change before writing the file.\n"
            "Lower = more responsive. Higher = fewer disk writes."
        )
        form.addRow("Delay after last change:", self.debounce_spin)
        root.addLayout(form)

        root.addWidget(_divider())

        # ── Save location ─────────────────────────────────────────────
        loc_label = QLabel("<b>Save file</b>")
        root.addWidget(loc_label)

        self.separate_file_cb = QCheckBox(
            "Save to a separate  <raster>_autosave.points  file"
        )
        self.separate_file_cb.setToolTip(
            "When checked, autosaves go to <raster>_autosave.points so they\n"
            "never overwrite a .points file you saved manually.\n\n"
            "When unchecked, autosaves write directly to <raster>.points —\n"
            "the same file QGIS uses, so a crash recovery needs no extra steps."
        )
        layout_cb = QVBoxLayout()
        layout_cb.setContentsMargins(12, 0, 0, 0)
        layout_cb.addWidget(self.separate_file_cb)
        root.addLayout(layout_cb)

        root.addWidget(_divider())

        # ── Notifications ─────────────────────────────────────────────
        notif_label = QLabel("<b>Notifications</b>")
        root.addWidget(notif_label)

        notif_layout = QVBoxLayout()
        notif_layout.setContentsMargins(12, 0, 0, 0)
        notif_layout.setSpacing(6)

        self.status_label_cb = QCheckBox(
            "Show last-save timestamp in the Georeferencer status bar"
        )
        self.message_bar_cb = QCheckBox(
            "Show a notification in the QGIS message bar on each save"
        )
        self.message_bar_cb.setToolTip(
            "Useful while testing. Can get noisy during active digitising — "
            "saves always appear in the QGIS log panel regardless."
        )

        notif_layout.addWidget(self.status_label_cb)
        notif_layout.addWidget(self.message_bar_cb)
        root.addLayout(notif_layout)

        root.addWidget(_divider())

        # ── Buttons ───────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        root.addWidget(buttons)

    # ------------------------------------------------------------------

    def _load(self):
        self.debounce_spin.setValue(get_setting("debounce_ms", int))
        self.separate_file_cb.setChecked(get_setting("separate_file", bool))
        self.status_label_cb.setChecked(get_setting("show_status_label", bool))
        self.message_bar_cb.setChecked(get_setting("show_message_bar", bool))

    def _save_and_accept(self):
        set_setting("debounce_ms",       self.debounce_spin.value())
        set_setting("separate_file",     self.separate_file_cb.isChecked())
        set_setting("show_status_label", self.status_label_cb.isChecked())
        set_setting("show_message_bar",  self.message_bar_cb.isChecked())
        self.accept()

    def _restore_defaults(self):
        self.debounce_spin.setValue(DEFAULTS["debounce_ms"])
        self.separate_file_cb.setChecked(DEFAULTS["separate_file"])
        self.status_label_cb.setChecked(DEFAULTS["show_status_label"])
        self.message_bar_cb.setChecked(DEFAULTS["show_message_bar"])


def _divider():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
