from datetime import datetime
import pandas as pd
from PySide6.QtCore import Qt, QPoint, QPointF, QDateTime
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QWheelEvent, QPen, QBrush
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSizePolicy
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QScatterSeries, QDateTimeAxis, QValueAxis, QLegend

from chaosgen.gui.theme import Colors, button_toolbar_qss, chart_tooltip_qss


class InteractiveChartView(QChartView):
    """
    Subclass of QChartView to enable mouse wheel zoom, click-and-drag panning,
    and click detection logic for selecting/locking node state tooltips.
    """

    def __init__(self, chart, main_widget, parent=None):
        super().__init__(chart, parent)
        self.main_widget = main_widget
        self.setRubberBand(QChartView.NoRubberBand)  # Disable left-click rectangle stretch zoom
        self.setMouseTracking(True)
        self._is_panning = False
        self._last_mouse_pos = None

    def wheelEvent(self, event: QWheelEvent):
        # Zoom in/out at mouse center based on scroll wheel direction
        factor = 1.15 if event.angleDelta().y() > 0 else 0.85
        self.chart().zoom(factor)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.RightButton:
            self._is_panning = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        elif event.button() == Qt.LeftButton:
            # Set flag to detect if the click landed on an actual scatter point
            self.main_widget.clicked_on_point = False
            super().mousePressEvent(event)
            # If the click wasn't consumed by a scatter point, unlock the tooltip selection
            if not self.main_widget.clicked_on_point:
                self.main_widget.locked_point = None
                self.main_widget.locked_cid = None
                self.main_widget.tooltip.hide()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_panning and self._last_mouse_pos is not None:
            delta = event.position() - self._last_mouse_pos
            self._last_mouse_pos = event.position()
            # Scroll chart axes based on mouse dragging offset
            self.chart().scroll(-delta.x() * 0.5, delta.y() * 0.5)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.RightButton:
            self._is_panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.chart().setGeometry(0, 0, self.width(), self.height())


class InteractiveTimelineWidget(QWidget):
    """
    Hardware-accelerated interactive anomaly timeline chart using QtCharts.
    Supports right-click pan, wheel scroll zoom, lock-on-click anomaly tooltips,
    and detailed service/metric summaries.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline_df = None
        self.summaries = []
        self.locked_point = None
        self.locked_cid = None
        self.clicked_on_point = False
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Toolbar layout for zoom/pan controls
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(4, 4, 4, 4)
        toolbar_layout.setSpacing(6)

        # Controls styled via theme tokens (phase a)
        btn_style = button_toolbar_qss()

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_out = QPushButton("-")
        self.btn_reset = QPushButton("⟳")

        for btn in (self.btn_zoom_in, self.btn_zoom_out, self.btn_reset):
            btn.setStyleSheet(btn_style)

        self.btn_zoom_in.clicked.connect(self._on_zoom_in)
        self.btn_zoom_out.clicked.connect(self._on_zoom_out)
        self.btn_reset.clicked.connect(self._on_reset)

        toolbar_layout.addWidget(self.btn_zoom_in)
        toolbar_layout.addWidget(self.btn_zoom_out)
        toolbar_layout.addWidget(self.btn_reset)
        toolbar_layout.addStretch()

        layout.addLayout(toolbar_layout)

        # Chart initialization
        self.chart = QChart()
        # MODIFIED: theme Colors instead of raw hex
        self.chart.setBackgroundRoundness(8)
        self.chart.setBackgroundBrush(QBrush(QColor(Colors.BG_PRIMARY)))
        self.chart.setPlotAreaBackgroundBrush(QBrush(QColor(Colors.BG_INPUT)))
        self.chart.setPlotAreaBackgroundVisible(True)

        # Configure Legend at the bottom
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(Qt.AlignBottom)
        self.chart.legend().setLabelColor(QColor(Colors.TEXT_PRIMARY))
        self.chart.legend().setMarkerShape(QLegend.MarkerShapeCircle)

        self.chart_view = InteractiveChartView(self.chart, self, self)
        self.chart_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.chart_view)

        # Create overlay tooltip label as child of chart_view
        self.tooltip = QLabel(self.chart_view)
        self.tooltip.setWordWrap(True)
        self.tooltip.hide()

        # Connect plot area changed signal to dynamically re-anchor locked tooltips during panning/zooming
        self.chart.plotAreaChanged.connect(self._update_tooltip_position)

    def _on_zoom_in(self):
        self.chart.zoom(1.15)

    def _on_zoom_out(self):
        self.chart.zoom(0.85)

    def _on_reset(self):
        self.chart.zoomReset()

    def update_plot(
        self,
        timeline_df: pd.DataFrame | None,
        summaries: list,
        clusters: list,
    ):
        self.timeline_df = timeline_df
        self.summaries = summaries
        self.locked_point = None
        self.locked_cid = None
        self.tooltip.hide()

        self.chart.removeAllSeries()
        # Clear existing axes
        for axis in self.chart.axes():
            self.chart.removeAxis(axis)

        if timeline_df is None or timeline_df.empty:
            return

        # 1. Create continuous Outlier Score Line Trace
        trace_series = QLineSeries()
        trace_series.setName("Outlier Score")
        # MODIFIED: theme tokens
        pen = QPen(QColor(Colors.TEXT_MUTED))
        pen.setWidthF(1.5)
        trace_series.setPen(pen)

        # 2. Normal points scatter series (gray)
        normal_series = QScatterSeries()
        normal_series.setName("Normal State")
        normal_series.setMarkerShape(QScatterSeries.MarkerShapeCircle)
        normal_series.setMarkerSize(8)
        normal_series.setColor(QColor(Colors.CHART_BASELINE))
        normal_series.setPen(QPen(Qt.NoPen))

        # Add data to trace and normal series
        for ts, row in timeline_df.iterrows():
            msecs = int(ts.timestamp() * 1000)
            score = float(row["score"])
            trace_series.append(msecs, score)
            if row["anomaly_cluster"] == -1:
                normal_series.append(msecs, score)

        self.chart.addSeries(trace_series)
        self.chart.addSeries(normal_series)

        # Hide trace series from Legend to keep layout clean
        trace_markers = self.chart.legend().markers(trace_series)
        if trace_markers:
            trace_markers[0].setVisible(False)

        # 3. Anomaly cluster scatter series
        cluster_labels = timeline_df.loc[
            timeline_df["anomaly_cluster"] != -1, "anomaly_cluster"
        ].values
        unique_clusters = sorted(list(set(cluster_labels))) if len(cluster_labels) > 0 else []

        colors = Colors.CHART_SERIES
        cluster_info_map = {c.cluster_id: c for c in clusters} if clusters else {}

        for cid in unique_clusters:
            cid_mask = timeline_df["anomaly_cluster"] == cid
            cluster_data = timeline_df[cid_mask]
            cluster_color = colors[cid % len(colors)]

            info = cluster_info_map.get(cid)
            if info:
                label_text = f"Cluster {cid} ({info.sample_count} windows)"
            else:
                label_text = f"Cluster {cid}"

            scatter = QScatterSeries()
            scatter.setName(label_text)
            scatter.setMarkerShape(QScatterSeries.MarkerShapeCircle)
            scatter.setMarkerSize(12)
            scatter.setColor(QColor(cluster_color))
            scatter.setPen(QPen(QColor(Colors.BG_PRIMARY), 1))

            for ts, row in cluster_data.iterrows():
                msecs = int(ts.timestamp() * 1000)
                scatter.append(msecs, float(row["score"]))

            # Wire hover and left-click select inspectors
            scatter.hovered.connect(
                lambda point, state, cid=cid: self._on_point_hovered(point, state, cid)
            )
            scatter.clicked.connect(
                lambda point, cid=cid: self._on_point_clicked(point, cid)
            )
            self.chart.addSeries(scatter)

        # 4. Axes Setup
        x_axis = QDateTimeAxis()
        x_axis.setFormat("MM-dd HH:mm")
        x_axis.setTitleText("Timestamp (UTC)")
        x_axis.setTitleBrush(QBrush(QColor(Colors.TEXT_PRIMARY)))
        x_axis.setLabelsColor(QColor(Colors.TEXT_SECONDARY))
        x_axis.setGridLineColor(QColor(Colors.BORDER))
        x_axis.setGridLineVisible(True)

        min_time = QDateTime.fromMSecsSinceEpoch(int(timeline_df.index.min().timestamp() * 1000))
        max_time = QDateTime.fromMSecsSinceEpoch(int(timeline_df.index.max().timestamp() * 1000))
        x_axis.setRange(min_time, max_time)

        y_axis = QValueAxis()
        y_axis.setRange(0.0, 1.05)
        y_axis.setTitleText("Outlier Severity Score")
        y_axis.setTitleBrush(QBrush(QColor(Colors.TEXT_PRIMARY)))
        y_axis.setLabelsColor(QColor(Colors.TEXT_SECONDARY))
        y_axis.setGridLineColor(QColor(Colors.BORDER))
        y_axis.setGridLineVisible(True)

        self.chart.addAxis(x_axis, Qt.AlignBottom)
        self.chart.addAxis(y_axis, Qt.AlignLeft)

        # Connect axis range changes to update tooltip position during panning and zooming
        x_axis.rangeChanged.connect(self._update_tooltip_position)
        y_axis.rangeChanged.connect(self._update_tooltip_position)

        # Attach axes to all series
        for series in self.chart.series():
            series.attachAxis(x_axis)
            series.attachAxis(y_axis)

    def clear(self):
        self.update_plot(None, [], [])

    def _on_point_hovered(self, point: QPointF, state: bool, cid: int):
        # Ignore hover updates if a specific point's tooltip has been locked/selected by left-click
        if self.locked_point is not None:
            return

        if state:
            self._show_point_tooltip(point, cid, persistent=False)
        else:
            self.tooltip.hide()

    def _on_point_clicked(self, point: QPointF, cid: int):
        self.clicked_on_point = True

        # Toggle unlock if clicking on the currently locked point again
        if self.locked_point is not None and abs(self.locked_point.x() - point.x()) < 1.0:
            self.locked_point = None
            self.locked_cid = None
            self.tooltip.hide()
        else:
            self.locked_point = point
            self.locked_cid = cid
            self._show_point_tooltip(point, cid, persistent=True)

    def _show_point_tooltip(self, point: QPointF, cid: int, persistent: bool):
        if self.timeline_df is None:
            return

        # Convert index to tz-naive for robust subtraction
        if hasattr(self.timeline_df.index, "tz") and self.timeline_df.index.tz is not None:
            idx_naive = self.timeline_df.index.tz_localize(None)
        else:
            idx_naive = self.timeline_df.index

        t = pd.to_datetime(point.x(), unit="ms")
        time_diffs = abs(idx_naive - t)
        min_idx = time_diffs.argmin()
        idx = self.timeline_df.index[min_idx]

        if time_diffs[min_idx].total_seconds() <= 300:
            row = self.timeline_df.loc[idx]
            score = float(row["score"])
            t_str = idx.strftime("%Y-%m-%d %H:%M")

            tooltip_text = (
                f"🕒 Window: {t_str}\n"
                f"🔴 Cluster ID: {cid}\n"
                f"🔥 Severity Score: {score:.2f}\n"
            )

            # Match LLM-generated incident summaries for this cluster
            matching_summaries = [s for s in self.summaries if s.source_cluster_id == cid]
            if matching_summaries:
                tooltip_text += "\nAffected Services & Deviating Signals:\n"
                for s in matching_summaries[:3]:
                    feats = ", ".join(f"{name.split('__')[-1]} (z={z:.1f})" for name, z in s.top_features)
                    tooltip_text += f"• {s.service_name}: {feats}\n"
                    if s.error_pattern:
                        tooltip_text += f"  (Logs: {s.error_pattern})\n"

                if len(matching_summaries) > 3:
                    tooltip_text += f"• ...and {len(matching_summaries) - 3} more services\n"

            self.tooltip.setText(tooltip_text.strip())

            # MODIFIED: theme helper (locked → ACCENT border, else BORDER)
            self.tooltip.setStyleSheet(chart_tooltip_qss(locked=persistent))

            # Position next to point inside the chart view coordinates
            pixel_pos = self.chart.mapToPosition(point)
            self.tooltip.move(int(pixel_pos.x() + 15), int(pixel_pos.y() - 15))
            self.tooltip.show()
            self.tooltip.adjustSize()

    def _update_tooltip_position(self, *args):
        """Dynamic slot that updates the locked tooltip screen anchor during pans/zooms/resizes."""
        if self.locked_point is not None:
            visible = False
            for axis in self.chart.axes(Qt.Horizontal):
                if isinstance(axis, QDateTimeAxis):
                    axis_min = axis.min().toMSecsSinceEpoch()
                    axis_max = axis.max().toMSecsSinceEpoch()
                    if axis_min <= self.locked_point.x() <= axis_max:
                        visible = True
                else:
                    if axis.min() <= self.locked_point.x() <= axis.max():
                        visible = True
            for axis in self.chart.axes(Qt.Vertical):
                if not (axis.min() <= self.locked_point.y() <= axis.max()):
                    visible = False

            if visible:
                pixel_pos = self.chart.mapToPosition(self.locked_point)
                self.tooltip.move(int(pixel_pos.x() + 15), int(pixel_pos.y() - 15))
                self.tooltip.show()
            else:
                self.tooltip.hide()
