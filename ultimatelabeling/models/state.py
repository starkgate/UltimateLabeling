import pickle
import os
import glob
import re
import numpy as np
from PyQt5.QtCore import QThread, QMutex
from ultimatelabeling.styles import Theme
from .ssh_credentials import SSHCredentials
from .track_info import TrackInfo
from ultimatelabeling import utils
from ultimatelabeling.config import DATA_DIR, STATE_PATH


class FrameMode:
    MANUAL = "manual"  # for manually choosing the current frame
    CONTROLLED = "controlled"  # when the current frame is being controlled by some thread (Player, Tracker, ...)
    SLIDER = "slider"


class RightClickOption:
    DELETE_CURRENT = 0
    DELETE_PREVIOUS = 1
    DELETE_FOLLOWING = 2


class State:
    def __init__(self):
        self.video_list = []
        self.current_video = None
        self.current_frame = 0
        self.nb_frames = 0
        self.file_names = []
        self.theme = Theme.DARK
        self.ssh_credentials = SSHCredentials()
        self.track_info = TrackInfo()
        self.current_detection = None
        self.frame_mode = FrameMode.MANUAL

        self.loading_frame = None
        self.loaded_frame = None

        self.ssh_connected = False
        self.tracking_server_running = False
        self.detection_server_running = False

        self.detection_detached_video_name = None

        self.right_click_option = RightClickOption.DELETE_CURRENT

        self.speed_player = +1

        self.keypoints_show_bbox = False
        self.keypoints_instance_color = False
        self.bbox_class_color = False
        self.copy_annotations_option = False

        self.use_cropping_area = False
        self.visible_area = (0, 0, 0, 0)
        self.stored_area = (0, 0, 0, 0)
        self.image_size = (0, 0)
        self.drawing = False

        self.img_viewer = None

        self.listeners = set()

    def get_file_name(self, frame=None):
        if frame is None:
            frame = self.current_frame

        file_path = self.file_names[frame]
        base = os.path.basename(file_path)
        return os.path.splitext(base)[0]

    def remove_current_frame(self):
        current_file = self.file_names[self.current_frame]
        base_path, file_name = os.path.split(current_file)
        file_name, file_ext = os.path.splitext(file_name)
        dataset_name = os.path.split(base_path)[1]

        print("Removing current frame: ", current_file)
        try:
            # rename current file
            os.rename(current_file, current_file + '.not')
            # rename current file's labels
            current_file_label = os.path.join('output', dataset_name, file_name) + '.txt'
            current_file_yolo_label = os.path.join('output', dataset_name, 'yolo', file_name) + '.txt'
            os.rename(current_file_label, current_file_label + '.not')
            os.rename(current_file_yolo_label, current_file_yolo_label + '.not')
        except FileNotFoundError as e:
            print(e)

        del self.file_names[self.current_frame]
        self.nb_frames -= 1
        self.increase_current_frame(frame_mode=FrameMode.MANUAL, speed=+1)
        self.increase_current_frame(frame_mode=FrameMode.MANUAL, speed=-1)

    def get_file_names(self):
        for frame in range(self.nb_frames):
            yield self.get_file_name(frame)

    def find_videos(self):
        return next(os.walk(DATA_DIR))[1]

    def check_raw_videos(self):
        files = glob.glob(os.path.join(DATA_DIR, "*.mp4"))
        files.extend(glob.glob(os.path.join(DATA_DIR, "*.mov")))

        for file in files:
            base = os.path.basename(file)
            filename = os.path.splitext(base)[0]

            if filename not in self.video_list:
                print("Extracting video {}...".format(base))
                utils.convert_video_to_frames(file, os.path.join(DATA_DIR, filename))
        self.video_list = self.find_videos()

    def update_file_names(self):
        if self.current_video:
            file_names = glob.glob(os.path.join(DATA_DIR, self.current_video, '*.jpg'))
            file_names.extend(glob.glob(os.path.join(DATA_DIR, self.current_video, '*.png')))
            self.file_names = sorted(file_names, key = utils.natural_sort_key)
            self.nb_frames = len(self.file_names)

    def save_state(self):
        with open(STATE_PATH, 'wb') as f:
            state_dict = {k: v for k, v in self.__dict__.items() if k not in ["listeners", "track_info", "drawing",
                                                                              "img_viewer", "speed_player"]}
            pickle.dump(state_dict, f)

    def load_state(self):
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, 'rb') as f:
                self.__dict__.update(pickle.load(f))

        # Reinitialize SSH connection info
        self.ssh_connected = False
        self.tracking_server_running = False
        self.detection_server_running = False

        self.copy_annotations_option = False

        self.video_list = self.find_videos()
        self.check_raw_videos()

        if self.current_video not in self.video_list:
            self.current_video = self.video_list[0] if len(self.video_list) > 0 else None
            self.current_frame = 0

        self.update_file_names()
        self.track_info = TrackInfo(self.current_video)
        self.track_info.load_detections(self.get_file_name())
        self.frame_mode = FrameMode.MANUAL

    def set_current_frame(self, current_frame, frame_mode=None):
        self.track_info.save_to_disk()
        self.track_info.img_size = self.image_size

        self.current_frame = current_frame

        if frame_mode is not None:
            self.frame_mode = frame_mode

        self.track_info.load_detections(self.get_file_name())

        self.notify_listeners("on_current_frame_change")

    def increase_current_frame(self, frame_mode=None, speed=None):
        if speed is None:
            speed = self.speed_player

        new_frame = max(min(self.current_frame + speed, self.nb_frames - 1), 0)
        self.set_current_frame(new_frame, frame_mode=frame_mode)

    def set_current_video(self, video_name):
        if video_name != self.current_video:
            self.track_info.save_to_disk()

            self.current_video = video_name
            self.update_file_names()
            self.current_frame = 0
            self.track_info = TrackInfo(self.current_video)
            self.track_info.load_detections(self.get_file_name())
            self.current_detection = None
            self.frame_mode = FrameMode.MANUAL

            self.notify_listeners("on_video_change")

    def set_theme(self, theme):
        if self.theme != theme:
            self.theme = theme
            self.notify_listeners("on_theme_change")

    def add_detection(self, detection, frame):
        self.track_info.add_detection(detection, self.get_file_name(frame))

        if frame == self.current_frame:
            self.current_detection = detection

    def set_detections(self, detections, frame):
        self.track_info.write_detections(self.get_file_name(frame), detections)

        if frame == self.current_frame:
            self.notify_listeners("on_detection_change")

    def remove_detection(self, detection_index=None, detection=None):
        if detection_index is not None:
            self.track_info.detections.pop(detection_index)
        elif detection is not None:
            self.track_info.detections.remove(detection)

        # For UI responsiveness, it's preferable to keep the previous bbox visible rather than having a delay
        # self.notify_listeners("on_detection_change")
    
    def remove_detection_and_future(self, detection_index=None, detection=None):
        if detection_index is not None:
            detection = self.track_info.detections[detection_index]

        track_id = detection.track_id

        if self.right_click_option == RightClickOption.DELETE_CURRENT:
            self.track_info.remove_detection(track_id, self.get_file_name())

        elif self.right_click_option == RightClickOption.DELETE_FOLLOWING:
            for i in range(self.current_frame, self.nb_frames):
                if not self.track_info.remove_detection(track_id, self.get_file_name(i)):
                    break

        elif self.right_click_option == RightClickOption.DELETE_PREVIOUS:
            for i in range(self.current_frame, -1, -1):
                if not self.track_info.remove_detection(track_id, self.get_file_name(i)):
                    break

        self.notify_listeners("on_detection_change")

    def modify_class_id_and_future(self, detection, class_id):
        track_id = detection.track_id

        for i in range(self.current_frame, self.nb_frames):
            if not self.track_info.modify_class_id(track_id, class_id, self.get_file_name(i)):
                break

        self.notify_listeners("on_detection_change")

    def check_frames_equal(self, frame1, frame2):
        frame1 = os.path.split(self.file_names[frame1])[1]
        frame1 = os.path.splitext(frame1)[0]
        frame2 = os.path.split(self.file_names[frame2])[1]
        frame2 = os.path.splitext(frame2)[0]

        return np.array_equal(self.track_info.get_track_ids(frame1), self.track_info.get_track_ids(frame2))
    
    def set_current_detection(self, detection):
        self.current_detection = detection
        self.track_info.add_detection(self.current_detection)

        self.notify_listeners("on_detection_change")

    def set_keypoints_show_bbox(self, value):
        self.keypoints_show_bbox = value
        self.notify_listeners("on_detection_change")
    def set_keypoints_instance_color(self, value):
        self.keypoints_instance_color = value
        self.notify_listeners("on_detection_change")
    def set_bbox_class_color(self, value):
        self.bbox_class_color = value
        self.notify_listeners("on_detection_change")
    def set_copy_annotations_option(self, value):
        self.copy_annotations_option = value

    def add_listener(self, listener):
        self.listeners.add(listener)

    def notify_listeners(self, method_name):
        for listener in self.listeners:
            func = getattr(listener, method_name)
            func()


class StateListener:
    def on_current_frame_change(self):
        pass

    def on_video_change(self):
        pass

    def on_theme_change(self):
        pass

    def on_detection_change(self):
        pass

    def on_frame_mode_change(self):
        pass
