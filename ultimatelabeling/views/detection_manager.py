import os
import datetime
from PyQt5.QtWidgets import QGroupBox, QHBoxLayout, QPushButton, QMessageBox, QCheckBox, QComboBox, QFormLayout, QLabel, QVBoxLayout
from PyQt5.QtCore import QThread, pyqtSignal
from ultimatelabeling.models import FrameMode, TrackInfo
from ultimatelabeling.models.detector import SocketDetector
from ultimatelabeling.models.polygon import Bbox
from ultimatelabeling.config import DATA_DIR
from subprocess import Popen

class DetectionManager(QGroupBox):
    def __init__(self, state):
        super().__init__("Detection")

        self.state = state
        self.detector = SocketDetector()
        options_layout = QFormLayout()

        crop_layout = QHBoxLayout()
        self.crop_checkbox = QCheckBox("Use cropping area", self)
        self.crop_checkbox.setChecked(self.state.use_cropping_area)
        self.crop_checkbox.stateChanged.connect(self.checked_cropping_area)
        self.crop_button = QPushButton("Choose cropping area")
        self.crop_button.clicked.connect(self.save_cropping_area)
        crop_layout.addWidget(self.crop_checkbox)
        crop_layout.addWidget(self.crop_button)
        options_layout.addRow(crop_layout)

        self.detector_dropdown = QComboBox()
        self.detector_dropdown.addItems(["YOLO", "OpenPifPaf"])
        options_layout.addRow(QLabel("Detection net:"), self.detector_dropdown)

        self.frame_detection_thread = DetectionThread(self.state, self.detector, self, detect_video=False)
        self.frame_detection_thread.err_signal.connect(self.display_err_message)
        self.frame_detection_thread.finished.connect(self.on_detection_finished)

        self.detection_thread = DetectionThread(self.state, self.detector, self, detect_video=True)
        self.detection_thread.err_signal.connect(self.display_err_message)
        self.detection_thread.finished.connect(self.on_detection_finished)

        run_layout = QHBoxLayout()
        self.frame_detection_button = QPushButton("Run on frame")
        self.frame_detection_button.clicked.connect(self.on_frame_detection_clicked)

        self.detection_button = QPushButton("Run on video")
        self.detection_button.clicked.connect(self.on_detection_clicked)

        self.pid = None
        self.start_detection_server()

        run_layout.addWidget(self.frame_detection_button)
        run_layout.addWidget(self.detection_button)

        layout = QVBoxLayout()
        layout.addLayout(options_layout)
        layout.addLayout(run_layout)
        self.setLayout(layout)

    def start_detection_server(self):
        self.pid = Popen(["bash", "-c", "CUDA_VISIBLE_DEVICES=0 python -m detector"]).pid
        self.state.detection_server_running = True
        print("Detection server started...")

    def close_detection_server(self):
        print("close detection server")
        os.system("kill {}".format(self.pid))
        self.state.detection_server_running = False
        print("detection server closed")

    def display_err_message(self, err_message):
        QMessageBox.warning(self, "", "Error: {}".format(err_message))

    def on_frame_detection_clicked(self):
        if not self.state.detection_server_running:
            self.start_detection_server()

        self.detection_button.setEnabled(False)
        self.frame_detection_button.setEnabled(False)

        self.frame_detection_thread.start()

    def on_detection_clicked(self):
        if not self.state.detection_server_running:
            self.start_detection_server()

        self.detection_button.setEnabled(False)
        self.frame_detection_button.setEnabled(False)

        self.detection_thread.start()

    def on_detection_finished(self):
        self.detection_button.setEnabled(True)
        self.frame_detection_button.setEnabled(True)
        self.close_detection_server()

    def checked_cropping_area(self):
        if self.crop_checkbox.isChecked():
            self.state.use_cropping_area = True

            if self.state.stored_area == (0, 0, 0, 0):
                self.state.stored_area = self.state.visible_area
        else:
            self.state.use_cropping_area = False

        self.state.notify_listeners("on_current_frame_change")

    def save_cropping_area(self):
        self.crop_checkbox.setChecked(True)
        self.state.stored_area = self.state.visible_area
        self.state.notify_listeners("on_current_frame_change")


class DetectionThread(QThread):
    err_signal = pyqtSignal(str)

    def __init__(self, state, detector, parent, detect_video=True):
        super().__init__()
        self.state = state
        self.detector = detector
        self.detect_video = detect_video
        self.parent = parent

    def run(self):
        self.detector.init()

        crop_area = None
        if self.parent.crop_checkbox.isChecked():
            crop_area = Bbox(*self.state.stored_area)

        detector = str(self.parent.detector_dropdown.currentText())

        if self.detect_video:
            seq_path = os.path.join(DATA_DIR, self.state.current_video)
            self.state.frame_mode = FrameMode.CONTROLLED

            try:
                for frame, detections in enumerate(self.detector.detect_sequence(seq_path, self.state.nb_frames, crop_area=crop_area, detector=detector)):
                    self.state.set_detections(detections, frame)

                    if self.state.frame_mode == FrameMode.CONTROLLED or self.state.current_frame == frame:
                        self.state.set_current_frame(frame)
            except Exception as e:
                self.err_signal.emit(str(e))
                self.detector.terminate()

            self.state.frame_mode = FrameMode.MANUAL

        else:
            image_path = self.state.file_names[self.state.current_frame]

            try:
                detections = self.detector.detect(image_path, crop_area=crop_area, detector=detector)
                self.state.set_detections(detections, self.state.current_frame)
                self.state.set_current_frame(self.state.current_frame)

                self.detector.terminate()
            except Exception as e:
                self.err_signal.emit(str(e))
