"""
Georeferencer Autosave Plugin
Hooks into the QGIS Georeferencer GCP table model and autosaves the .points
file on every change (debounced). Output format matches the native QGIS
.tiff.points format: comma-separated, 8 columns with header, full double
precision, WKT2 CRS header.
"""

import os
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QApplication, QTableView, QDockWidget,
    QLabel, QAction, QToolBar, QStatusBar
)
from qgis.core import QgsMessageLog, QgsApplication, Qgis

from .settings_dialog import GeorefAutosaveSettingsDialog, get_setting


import math

def _fmt_float(v):
    """
    Format a float in fixed decimal notation with 17 significant figures.
    Avoids scientific notation (e.g. 3.6e-12) to match the QGIS .points format
    which always uses decimal strings (e.g. 0.00000000000363798).
    """
    if not isinstance(v, float):
        return str(v)
    if v == 0 or v != v:          # zero or NaN
        return '0'
    magnitude = math.floor(math.log10(abs(v)))
    dp = max(0, 17 - magnitude - 1)
    return f'{v:.{dp}f}'

PLUGIN_NAME = "GeorefAutosave"
POLL_MS = 2000

# Header row written to every .points file
POINTS_HEADER = "mapX,mapY,sourceX,sourceY,enable,dX,dY,residual"


class GeorefAutosavePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.georef_win = None
        self.model = None
        self.proxy = None
        self._connected = False

        self.debounce_timer = QTimer()
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._do_autosave)

        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self._poll_for_georef)

        self.status_label = None
        self.toolbar_action = None
        self.menu_action = None

    # ------------------------------------------------------------------
    # QGIS plugin lifecycle
    # ------------------------------------------------------------------

    def initGui(self):
        self.menu_action = QAction(
            QgsApplication.getThemeIcon("/mActionOptions.svg"),
            "Georeferencer Autosave Settings…",
            self.iface.mainWindow(),
        )
        self.menu_action.triggered.connect(self._open_settings)
        self.iface.pluginMenu().addAction(self.menu_action)
        self.poll_timer.start(POLL_MS)

    def unload(self):
        self.poll_timer.stop()
        self.debounce_timer.stop()
        self._disconnect()
        if self.menu_action:
            self.iface.pluginMenu().removeAction(self.menu_action)
            self.menu_action = None

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _open_settings(self):
        parent = self.georef_win if self.georef_win else self.iface.mainWindow()
        dlg = GeorefAutosaveSettingsDialog(parent)
        if dlg.exec():
            if self.debounce_timer.isActive():
                self.debounce_timer.start(get_setting("debounce_ms", int))
            self._sync_status_label_visibility()

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    def _poll_for_georef(self):
        win = next(
            (w for w in QApplication.topLevelWidgets()
             if w.objectName() == "QgsGeorefPluginGuiBase"),
            None,
        )
        if win and not self._connected:
            self._connect(win)
        elif not win and self._connected:
            self._disconnect()

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def _connect(self, win):
        dock = win.findChild(QDockWidget, "dockWidgetGCPpoints")
        if not dock:
            self._log("Could not find GCP dock widget.", Qgis.MessageLevel.Warning)
            return
        table = dock.findChild(QTableView)
        if not table:
            self._log("Could not find GCP table view.", Qgis.MessageLevel.Warning)
            return
        proxy = table.model()
        source = proxy.sourceModel() if proxy else None
        if not source:
            self._log("Could not get source model.", Qgis.MessageLevel.Warning)
            return

        self.georef_win = win
        self.proxy = proxy
        self.model = source

        self.model.dataChanged.connect(self._on_data_changed)
        self.model.rowsInserted.connect(self._on_rows_changed)
        self.model.rowsRemoved.connect(self._on_rows_changed)

        self._inject_toolbar_button()
        self._inject_status_label()
        self._connected = True

        self._log("Autosave active — watching GCP table.")
        self.iface.messageBar().pushMessage(
            PLUGIN_NAME,
            "GCP autosave is active for this Georeferencer session.",
            level=Qgis.MessageLevel.Info,
            duration=4,
        )

    def _disconnect(self):
        if self.model:
            try:
                self.model.dataChanged.disconnect(self._on_data_changed)
                self.model.rowsInserted.disconnect(self._on_rows_changed)
                self.model.rowsRemoved.disconnect(self._on_rows_changed)
            except Exception:
                pass
        self._remove_toolbar_button()
        self.model = None
        self.proxy = None
        self.georef_win = None
        self.status_label = None
        self._connected = False

    # ------------------------------------------------------------------
    # Model signal handlers
    # ------------------------------------------------------------------

    def _on_data_changed(self, top_left=None, bottom_right=None, roles=None):
        self.debounce_timer.start(get_setting("debounce_ms", int))

    def _on_rows_changed(self, parent=None, first=None, last=None):
        self.debounce_timer.start(get_setting("debounce_ms", int))

    # ------------------------------------------------------------------
    # Autosave — writes format matching <raster>.tiff.points
    #
    # Columns: mapX,mapY,sourceX,sourceY,enable,dX,dY,residual
    # Model:   4     5    2       3       0      6   7   8
    # ------------------------------------------------------------------

    def _do_autosave(self):
        if not self.model:
            return
        row_count = self.model.rowCount()
        if row_count == 0:
            return

        raster_path = self._get_raster_path()
        if not raster_path:
            self._log(
                "Cannot determine raster path — autosave skipped.",
                Qgis.MessageLevel.Warning,
            )
            return

        base = os.path.splitext(raster_path)[0]
        suffix = "_autosave" if get_setting("separate_file", bool) else ""
        points_path = base + suffix + ".points"

        crs_line = self._get_crs_line(points_path)

        lines = []
        if crs_line:
            lines.append(crs_line)
        lines.append(POINTS_HEADER)

        for row in range(row_count):
            dst_x = self._get_value(row, 4)   # mapX
            dst_y = self._get_value(row, 5)   # mapY
            src_x = self._get_value(row, 2)   # sourceX (pixel)
            src_y = self._get_value(row, 3)   # sourceY (pixel)
            enabled = self._get_enabled(row)
            dx  = self._get_value(row, 6) or "0"
            dy  = self._get_value(row, 7) or "0"
            res = self._get_value(row, 8) or "0"

            if None in (dst_x, dst_y, src_x, src_y):
                continue

            lines.append(f"{dst_x},{dst_y},{src_x},{src_y},{enabled},{dx},{dy},{res}")

        try:
            with open(points_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")

            ts = datetime.now().strftime("%H:%M:%S")
            msg = f"Autosaved {row_count} GCP(s) → {os.path.basename(points_path)}"
            self._log(msg)

            if get_setting("show_status_label", bool):
                self._update_status_label(
                    f"GCP autosaved at {ts}  "
                    f"({row_count} point{'s' if row_count != 1 else ''})"
                )
            if get_setting("show_message_bar", bool):
                self.iface.messageBar().pushMessage(
                    PLUGIN_NAME, msg,
                    level=Qgis.MessageLevel.Info, duration=3,
                )
        except OSError as exc:
            self._log(f"Autosave write failed: {exc}", Qgis.MessageLevel.Warning)

    # ------------------------------------------------------------------
    # Data extraction
    # ------------------------------------------------------------------

    def _get_value(self, row, col):
        """
        Return the best string representation of a cell value.
        Tries EditRole first (raw Python float → full double precision).
        Falls back to DisplayRole (pre-formatted string).
        """
        idx = self.model.index(row, col)

        edit_val = self.model.data(idx, Qt.ItemDataRole.EditRole)
        if edit_val is not None:
            if isinstance(edit_val, float):
                # repr() gives the shortest string that round-trips a double
                return _fmt_float(edit_val)
            s = str(edit_val).strip()
            if s:
                return s

        display_val = self.model.data(idx, Qt.ItemDataRole.DisplayRole)
        return str(display_val).strip() if display_val is not None else None

    def _get_enabled(self, row):
        idx = self.model.index(row, 0)
        state = self.model.data(idx, Qt.ItemDataRole.CheckStateRole)
        try:
            return 1 if state == Qt.CheckState.Checked else 0
        except AttributeError:
            return 1 if state == 2 else 0

    # ------------------------------------------------------------------
    # Raster path
    # ------------------------------------------------------------------

    def _get_raster_path(self):
        if not self.georef_win:
            return None
        try:
            from qgis.gui import QgsMapCanvas
            canvas = self.georef_win.findChild(QgsMapCanvas, "georefCanvas")
            if canvas:
                layers = canvas.layers()
                if layers:
                    src = layers[0].source()
                    if src and os.path.exists(src):
                        return src
        except Exception as exc:
            self._log(f"Canvas layer lookup failed: {exc}", Qgis.MessageLevel.Warning)

        # Fallback: parse window title
        title = self.georef_win.windowTitle()
        for sep in (" \u2014 ", " - ", "\u2014"):
            if sep in title:
                candidate = title.split(sep, 1)[-1].strip().lstrip("*").strip()
                if os.path.exists(candidate):
                    return candidate
        return None

    # ------------------------------------------------------------------
    # CRS — prefer WKT2 to match native QGIS .tiff.points format
    # ------------------------------------------------------------------

    def _get_crs_line(self, points_path):
        # 1. Preserve from existing file (keeps WKT2 if already saved)
        if os.path.exists(points_path):
            try:
                with open(points_path, "r", encoding="utf-8") as fh:
                    first = fh.readline().strip()
                    if first.startswith("#CRS:"):
                        return first
            except OSError:
                pass

        # 2. Derive from georef canvas CRS as WKT2
        try:
            from qgis.gui import QgsMapCanvas
            from qgis.core import QgsCoordinateReferenceSystem
            canvas = self.georef_win.findChild(QgsMapCanvas, "georefCanvas")
            if canvas:
                crs = canvas.mapSettings().destinationCrs()
                if crs.isValid():
                    try:
                        wkt = crs.toWkt(
                            QgsCoordinateReferenceSystem.WktVariant.WKT2_2019
                        )
                    except (AttributeError, TypeError):
                        wkt = crs.toWkt()
                    return f"#CRS: {wkt}"
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Toolbar button
    # ------------------------------------------------------------------

    def _inject_toolbar_button(self):
        if not self.georef_win:
            return
        toolbar = self.georef_win.findChild(QToolBar, "toolBarFile")
        if not toolbar:
            return
        self.toolbar_action = QAction(
            QgsApplication.getThemeIcon("/mActionOptions.svg"),
            "Autosave Settings…",
            self.georef_win,
        )
        self.toolbar_action.setToolTip("Georeferencer Autosave Settings")
        self.toolbar_action.triggered.connect(self._open_settings)
        toolbar.addSeparator()
        toolbar.addAction(self.toolbar_action)

    def _remove_toolbar_button(self):
        if self.toolbar_action and self.georef_win:
            try:
                toolbar = self.georef_win.findChild(QToolBar, "toolBarFile")
                if toolbar:
                    toolbar.removeAction(self.toolbar_action)
            except RuntimeError:
                pass
        self.toolbar_action = None

    # ------------------------------------------------------------------
    # Status bar label
    # ------------------------------------------------------------------

    def _inject_status_label(self):
        if not self.georef_win:
            return
        statusbar = self.georef_win.findChild(QStatusBar, "statusbar")
        if statusbar:
            self.status_label = QLabel("  GCP autosave: ready  ")
            statusbar.addPermanentWidget(self.status_label)
            self._sync_status_label_visibility()

    def _sync_status_label_visibility(self):
        if self.status_label:
            try:
                self.status_label.setVisible(get_setting("show_status_label", bool))
            except RuntimeError:
                self.status_label = None

    def _update_status_label(self, text):
        if self.status_label:
            try:
                self.status_label.setText(f"  {text}  ")
            except RuntimeError:
                self.status_label = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg, level=Qgis.MessageLevel.Info):
        QgsMessageLog.logMessage(msg, PLUGIN_NAME, level)
