from PyQt5.QtWidgets import QPushButton, QGroupBox, QVBoxLayout, QHBoxLayout, QStyle, QPlainTextEdit, QMessageBox, QCheckBox
from PyQt5.QtCore import QThread, pyqtSignal
from ultimatelabeling.models.tracker import SocketTracker, KCFTracker
from ultimatelabeling.models import Detection, FrameMode
from ultimatelabeling.models import KeyboardListener
import signal
import os
from subprocess import Popen

class TrackingThread(QThread):
    err_signal = pyqtSignal(str)

    def __init__(self, state_ref, tracker, **kwargs):
        super().__init__()

        self.state = state_ref
        self.runs = False
        self.tracker = tracker(**kwargs)

        self.selected = False

    def run(self):
        self.runs = True

        init_frame = self.state.current_frame
        if init_frame == self.state.nb_frames:
            return

        class_id = self.state.current_detection.class_id
        track_id = self.state.current_detection.track_id
        init_bbox = self.state.current_detection.bbox

        self.state.frame_mode = FrameMode.CONTROLLED
        self.selected = True

        try:
            self.tracker.init(self.state.file_names[init_frame], init_bbox)
        except Exception as e:
            self.err_signal.emit(str(e))
            return

        frame = init_frame + 1

        while frame < self.state.nb_frames and self.runs:

            image_path = self.state.file_names[frame]
            try:
                bbox, polygon = self.tracker.track(image_path)
            except Exception as e:
                self.err_signal.emit(str(e))
                return

            if bbox is None:
                break

            detection = Detection(class_id=class_id, track_id=track_id, polygon=polygon, bbox=bbox)

            self.state.add_detection(detection, frame)

            if (self.state.frame_mode == FrameMode.CONTROLLED and self.selected) or self.state.current_frame == frame:
                self.state.set_current_frame(frame)
                self.state.current_detection = detection

            frame += 1

        self.tracker.terminate()

    def stop(self):
        self.runs = False


class TrackingButtons(QGroupBox):
    def __init__(self, state, parent, index, name, thread):
        super().__init__(name)
        self.pid = None

        self.state = state
        self.parent = parent
        self.index = index
        self.thread = thread

        self.thread.err_signal.connect(self.display_err_message)
        self.thread.finished.connect(self.on_finished_tracking)

        layout = QVBoxLayout()

        self.start_button = QPushButton("Start")
        self.start_button.setIcon(self.style().standardIcon(QStyle.SP_DialogYesButton))
        self.start_button.clicked.connect(self.on_start_tracking)
        self.start_button.setToolTip("Start tracking")

        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_DialogNoButton))
        self.stop_button.clicked.connect(self.on_stop_tracking)
        self.stop_button.setToolTip("Stop tracking")

        self.enable_button = QPushButton("Enable")
        self.enable_button.clicked.connect(self.on_enabled)
        self.enable_button.setToolTip("Enable this tracker")

        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.enable_button)
        self.setLayout(layout)

        self.stop_button.hide()

    def display_err_message(self, err_message):
        QMessageBox.warning(self, "", "Error: {}".format(err_message))

    def on_start_tracking(self):
        if not self.state.tracking_server_running and self.index >= 1:
            self.start_tracking_server()
            
        if self.state.current_detection is None:
            print("No bounding box selected for tracking.")
        else:
            if self.thread.isRunning():
                print("Thread is running.")
            else:
                self.thread.start()
                self.start_button.hide()
                self.stop_button.show()

    def on_stop_tracking(self):
        if self.thread.isRunning():
            self.thread.stop()
            self.stop_tracking_server()

    def on_finished_tracking(self):
        if self.thread.isRunning():
            self.thread.terminate()
            self.stop_tracking_server()

        self.state.frame_mode = FrameMode.MANUAL
        self.state.notify_listeners("on_frame_mode_change")
        self.stop_button.hide()
        self.start_button.show()

    def on_enabled(self):
        self.parent.on_enabled(self.index)

    def start_tracking_server(self):
        self.pid = Popen(["bash", "-c", "CUDA_VISIBLE_DEVICES=0 python -m tracker -p 8787"]).pid
        self.state.tracking_server_running = True
        print("Tracking server started...")

    def close_tracking_server(self):
        print("closing tracking server")
        os.system("kill {}".format(self.pid))
        self.state.tracking_server_running = False
        print("tracking server closed")

class TrackingManager(QGroupBox, KeyboardListener):
    def __init__(self, state):
        super().__init__("Tracking")
        self.state = state
        self.selected = None

        # define the trackers available
        self.trackers = [
            TrackingButtons(self.state, self, 0, "KCF", TrackingThread(self.state, tracker=KCFTracker, state=self.state)),
            TrackingButtons(self.state, self, 1, "SiamMask", TrackingThread(self.state, tracker=SocketTracker, port=8787))
        ]
        self.trackers[0].setToolTip("Algorithm-based object tracking method")
        self.trackers[1].setToolTip("Neural network-based object tracking method")
        self.on_enabled(1) # enable the SiamMask tracker by default

        vlayout = QVBoxLayout()
        layout = QHBoxLayout()

        for tracker in self.trackers:
            layout.addWidget(tracker)

        self.automatic_tracking_checkbox = QCheckBox("Automatic tracking")
        self.automatic_tracking_checkbox.setToolTip("Automatically run the selected tracking method after creating a new bounding box")
        self.automatic_tracking_checkbox.setChecked(True)

        vlayout.addLayout(layout)
        vlayout.addWidget(self.automatic_tracking_checkbox)

        self.setLayout(vlayout)

    def on_key_track(self, automatic):
        if self.selected is not None:
            tracker = self.trackers[self.selected]
            if (automatic and self.automatic_tracking_checkbox.isChecked()) or not automatic:
                if tracker.thread.isRunning():
                    tracker.on_stop_tracking()
                else:
                    tracker.on_start_tracking()

    def on_enabled(self, index):
        self.selected = index
        for tracker in self.trackers:
            if tracker.index == self.selected:
                tracker.enable_button.setEnabled(False)
                tracker.enable_button.setText("Enabled")
            else:
                tracker.enable_button.setEnabled(True)
                tracker.enable_button.setText("Enable")