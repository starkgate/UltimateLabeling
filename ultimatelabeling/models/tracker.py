import os
import torch
from .polygon import Polygon, Bbox
import json
import socket
import pickle
import cv2
import struct
from siamMask.models.custom import Custom
from siamMask.utils.load_helper import load_pretrain
from siamMask.test import siamese_init, siamese_track, get_image_crop
from ultimatelabeling.config import RESOURCES_DIR

class Tracker:
    def __init__(self):
        self.use_cuda = torch.cuda.is_available()
        self.device = torch.device('cuda' if self.use_cuda else 'cpu')
        torch.backends.cudnn.benchmark = True

    def init(self, img, bbox):
        """
        Arguments:
            img (OpenCV image): obtained from cv2.imread(img_file)
            bbox (BBox)
        """
        raise NotImplementedError

    def track(self, img):
        """
        Output:
            bbox (BBox), polygon (Polygon)
        """
        raise NotImplementedError

    def terminate(self):
        pass


class SiamMaskTracker(Tracker):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.cfg = json.load(open(os.path.join(RESOURCES_DIR, "config_vot.json")))
        self.tracker = Custom(anchors=self.cfg['anchors'])
        self.tracker = load_pretrain(self.tracker, os.path.join(RESOURCES_DIR, "SiamMask_VOT.pth"), use_cuda=self.use_cuda)
        self.tracker.eval().to(self.device)

        self.state = None

    def init(self, image_path, bbox):
        img = cv2.imread(image_path)
        self.state = siamese_init(img, bbox.center, bbox.size, self.tracker, self.cfg['hp'], use_cuda=self.use_cuda)

    def track(self, image_path):
        img = cv2.imread(image_path)
        self.state = siamese_track(self.state, img.copy(), mask_enable=True, refine_enable=True, use_cuda=self.use_cuda)
        bbox = Bbox.from_center_size(self.state['target_pos'], self.state['target_sz'])
        polygon = Polygon(self.state['polygon'].flatten())

        success = self.state['score']

        return bbox, polygon

class KCFTracker(Tracker):
    def __init__(self, state, **kwargs):
        super().__init__(**kwargs)
        self.state = state

    def init(self, image_path, bbox):
        img = cv2.imread(image_path)
        self.tracker = cv2.TrackerKCF_create()
        self.tracker.init(img, tuple(bbox.to_json()))
        self.tracker.update(img)

    def track(self, image_path):
        img = cv2.imread(image_path)
        success, bbox = self.tracker.update(img)

        if not success:
            return None, None

        bbox = Bbox(*bbox)
        return bbox, Polygon.from_bbox(bbox)
