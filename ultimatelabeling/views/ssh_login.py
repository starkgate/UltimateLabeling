import os
import paramiko
from scp import SCPClient, SCPException
import time
import json
import socket
import subprocess

from PyQt5.QtWidgets import QGroupBox, QLabel, QLineEdit, QFormLayout, QPushButton, QMessageBox
from PyQt5.QtCore import QThread, pyqtSignal
from ultimatelabeling.models import StateListener, SSHCredentials
from ultimatelabeling.config import OUTPUT_DIR, SERVER_DIR


class SSHLogin(QGroupBox, StateListener):
    def load_credentials(self):
        self.hostname.setText(self.state.ssh_credentials.hostname)
        self.username.setText(self.state.ssh_credentials.username)
        self.password.setText(self.state.ssh_credentials.password)

    def save_credentials(self, hostname, username, password):
        self.state.ssh_credentials = SSHCredentials(hostname, username, password)

    def start_tracking_server(self):
        self.state.tracking_server_running = True;
        print("Tracking server started...")

    def start_tracking_servers(self):
        self.state.tracking_server_running = True;
        print("Tracking server started...")

    def start_detection_server(self):
        self.state.detection_server_running = True;
        print("Detection server started...")

    def start_detached_detection(self, seq_path, crop_area=None, detector="YOLO"):
        stdin, stdout, stderr = self.ssh_client.exec_command("tmux kill-session -t detached")  # Killing possible previous socket server
        args = "-s {} -d {}".format(seq_path, detector)
        if crop_area is not None:
            coords = crop_area.to_json()
            args += " -c {}".format(" ".join([str(int(x)) for x in coords]))
        stdin, stdout, stderr = self.ssh_client.exec_command('cd UltimateLabeling_server && source detection/env/bin/activate && tmux new -d -s detached "CUDA_VISIBLE_DEVICES=0 python -m detector_detached {}"'.format(args))

        print(stdout.read().decode())
        print(stderr.read().decode())

        errors = stderr.read().decode()
        if errors:
            QMessageBox.warning(self, "", errors)
        else:
            self.state.detection_detached_video_name = os.path.basename(seq_path)
            print("Detached detection server started...")

    def fetch_detached_info(self):
        local_info_file = os.path.join(OUTPUT_DIR, "running_info.json")
        server_info_file = os.path.join(SERVER_DIR, local_info_file)

        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.get(server_info_file, local_info_file)
        except SCPException:
            return

        with open(local_info_file, "r") as f:
            data = json.load(f)

        return data

    def load_detached_detections(self, video_name):
        local_detections_folder = os.path.join(OUTPUT_DIR)
        server_detections_folder = os.path.join(SERVER_DIR, "output", video_name)

        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.get(server_detections_folder, local_detections_folder, recursive=True)
        except SCPException:
            QMessageBox.warning(self, "", "Outputs not found on the server.")
            return

    def check_is_running(self):
        tmux_out = subprocess.check_output("tmux ls", shell=True)

        if b"detection" and b"tracking" in tmux_out:
            return True
        return False

    def closeServers(self):
        print("closing servers")
        os.system("bash -c /home/u42/UltimateLabeling_server/stop.sh")
        print("servers closed")
        
    def __init__(self, state):
        super().__init__("SSH login")
        subprocess.check_output("bash -c /home/u42/UltimateLabeling_server/start.sh", shell=True)

        self.state = state
        self.state.add_listener(self)

        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        form_layout = QFormLayout()

        self.hostname = QLineEdit()
        form_layout.addRow(QLabel("Host IP:"), self.hostname)

        self.username = QLineEdit()
        form_layout.addRow(QLabel("Username:"), self.username)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form_layout.addRow(QLabel("Password:"), self.password)

        self.connect_button = QPushButton("Connect")
        form_layout.addRow(self.connect_button)

        self.setLayout(form_layout)
        self.setFixedWidth(250)

        self.load_credentials()
        
        hostname, username, password = self.hostname.text(), self.username.text(), self.password.text()

        self.save_credentials(hostname, username, password)
        self.connect_button.setText("Connected")
        self.connect_button.setEnabled(False)

        self.start_detection_server()
        self.start_tracking_servers()


class SCPThread(QThread):
    countChanged = pyqtSignal(int)
    messageAdded = pyqtSignal(str)

    def __init__(self, ssh_client):
        super().__init__()

        self.ssh_client = ssh_client
        self.total_files = 20
        self.nb_sent = 0

    def progress(self, filename, size, sent):
        if size == sent:
            self.nb_sent += 1
            self.countChanged.emit(self.nb_sent / self.total_files * 100)
            self.messageAdded.emit("Sent {}".format(filename.decode()))

    def run(self):
        with SCPClient(self.ssh_client.get_transport(), progress=self.progress) as scp:
            scp.put('server_files', recursive=True)

        self.countChanged.emit(0)
        self.messageAdded.emit("Installing requirements...")
        stdin, stdout, stderr = self.ssh_client.exec_command('cd server_files && virtualenv venv -p /usr/bin/python3')
        print("out", stdout.read().decode(), "err", stderr.read().decode())
        stdin, stdout, stderr = self.ssh_client.exec_command('source server_files/venv/bin/activate && cd server_files && pip3 install -r requirements.txt')
        print("out", stdout.read().decode(), "err", stderr.read().decode())
        self.countChanged.emit(100)

        # Downloading pretrained model
        self.countChanged.emit(0)
        self.messageAdded.emit("Downloading pretrained weights...")
        stdin, stdout, stderr = self.ssh_client.exec_command('cd server_files/siamMask/pretrained && wget -q http://www.robots.ox.ac.uk/~qwang/SiamMask_VOT.pth')
        print("out", stdout.read().decode(), "err", stderr.read().decode())
        self.countChanged.emit(100)
