"""
MIT License

Copyright (c) 2020 Hyeonki Hong <hhk7734@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import cv2
import numpy as np
import os
import shutil
import tensorflow as tf
import time
from typing import Union

from ..core import dataset
from ..core import utils
from ..core import yolov4


class YoloV4:
    def __init__(self):
        self.strides = np.array([8, 16, 32])
        self.anchors = np.array(
            [
                12,
                16,
                19,
                36,
                40,
                28,
                36,
                75,
                76,
                55,
                72,
                146,
                142,
                110,
                192,
                243,
                459,
                401,
            ],
            dtype=np.float32,
        ).reshape(3, 3, 2)
        self.xyscale = np.array([1.2, 1.1, 1.05])
        self.width = self.height = 608

    @property
    def classes(self):
        return self._classes

    @classes.setter
    def classes(self, data: Union[str, dict]):
        """
        Usage:
            yolo.classes = {0: 'person', 1: 'bicycle', 2: 'car', ...}
            yolo.classes = "path/classes"
        """
        if isinstance(data, str):
            self._classes = utils.read_class_names(data)
        elif isinstance(data, dict):
            self._classes = data
        else:
            raise TypeError("YoloV4: Set classes path or dictionary")
        self.num_class = len(self._classes)

    def load_weights(self, path: str, weights_type: str = "yolo"):
        """
        Usage:
            yolo.load_weights("yolov4.weights")
            yolo.load_weights("checkpoints", weights_type="tf")
        """
        if weights_type == "yolo":
            utils.load_weights(self.model, path)
        elif weights_type == "tf":
            self.model.load_weights(path).expect_partial()

        self._has_weights = True

    def inference(self, media_path, is_image=True, cv_waitKey_delay=10):
        if is_image:
            frame = cv2.imread(media_path)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            image = self.predict(frame, self._classes)

            cv2.namedWindow("result", cv2.WINDOW_AUTOSIZE)
            result = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            cv2.imshow("result", result)
            cv2.waitKey(cv_waitKey_delay)
        else:
            vid = cv2.VideoCapture(media_path)
            while True:
                return_value, frame = vid.read()
                if return_value:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                else:
                    raise ValueError("No image! Try with another video format")

                prev_time = time.time()
                image = self.predict(frame, self._classes)
                curr_time = time.time()
                exec_time = curr_time - prev_time

                result = np.asarray(image)
                info = "time: %.2f ms" % (1000 * exec_time)
                print(info)
                cv2.namedWindow("result", cv2.WINDOW_AUTOSIZE)
                result = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
                cv2.imshow("result", result)
                if cv2.waitKey(cv_waitKey_delay) & 0xFF == ord("q"):
                    break

    def train(
        self,
        train_annote_path,
        test_annote_path,
        trained_weights_path="./checkpoints",
        log_dir_path="./log",
        iou_loss_threshold=0.5,
        dataset_type: str = "converted_coco",
        epochs: int = 50,
        save_interval: int = 1,
    ):

        learning_rate_init = 1e-3
        learning_rate_end = 1e-6

        gpus = tf.config.experimental.list_physical_devices("GPU")
        if gpus:
            try:
                tf.config.experimental.set_memory_growth(gpus[0], True)
            except RuntimeError as e:
                print(e)

        trainset = dataset.Dataset(
            annot_path=train_annote_path,
            classes=self._classes,
            anchors=self.anchors,
            input_sizes=self.width,
            dataset_type=dataset_type,
        )
        testset = dataset.Dataset(
            annot_path=test_annote_path,
            classes=self._classes,
            anchors=self.anchors,
            input_sizes=self.width,
            is_training=False,
            dataset_type=dataset_type,
        )

        isfreeze = False

        if self._has_weights:
            first_stage_epochs = int(epochs * 0.3)
        else:
            first_stage_epochs = 0

        steps_per_epoch = len(trainset)
        second_stage_epochs = epochs - first_stage_epochs
        global_steps = tf.Variable(1, trainable=False, dtype=tf.int64)
        warmup_steps = 2 * steps_per_epoch
        total_steps = (
            first_stage_epochs + second_stage_epochs
        ) * steps_per_epoch

        optimizer = tf.keras.optimizers.Adam()
        if os.path.exists(log_dir_path):
            shutil.rmtree(log_dir_path)
        writer = tf.summary.create_file_writer(log_dir_path)

        def train_step(image_data, target):
            with tf.GradientTape() as tape:
                pred_result = self.model(image_data, training=True)
                giou_loss = conf_loss = prob_loss = 0

                # optimizing process
                for i in range(3):
                    conv, pred = pred_result[i * 2], pred_result[i * 2 + 1]
                    loss_items = yolov4.compute_loss(
                        pred,
                        conv,
                        target[i][0],
                        target[i][1],
                        strides=self.strides,
                        num_class=self.num_class,
                        iou_loss_threshold=iou_loss_threshold,
                        i=i,
                    )
                    giou_loss += loss_items[0]
                    conf_loss += loss_items[1]
                    prob_loss += loss_items[2]

                total_loss = giou_loss + conf_loss + prob_loss

                gradients = tape.gradient(
                    total_loss, self.model.trainable_variables
                )
                optimizer.apply_gradients(
                    zip(gradients, self.model.trainable_variables)
                )
                tf.print(
                    "=> STEP %4d   lr: %.6f   giou_loss: %4.2f   conf_loss: %4.2f   "
                    "prob_loss: %4.2f   total_loss: %4.2f"
                    % (
                        global_steps,
                        optimizer.lr.numpy(),
                        giou_loss,
                        conf_loss,
                        prob_loss,
                        total_loss,
                    )
                )
                # update learning rate
                global_steps.assign_add(1)
                if global_steps < warmup_steps:
                    lr = global_steps / warmup_steps * learning_rate_init
                else:
                    lr = learning_rate_end + 0.5 * (
                        learning_rate_init - learning_rate_end
                    ) * (
                        (
                            1
                            + tf.cos(
                                (global_steps - warmup_steps)
                                / (total_steps - warmup_steps)
                                * np.pi
                            )
                        )
                    )
                optimizer.lr.assign(lr.numpy())

                # writing summary data
                writer.as_default()
                tf.summary.scalar("lr", optimizer.lr, step=global_steps)
                tf.summary.scalar(
                    "loss/total_loss", total_loss, step=global_steps
                )
                tf.summary.scalar(
                    "loss/giou_loss", giou_loss, step=global_steps
                )
                tf.summary.scalar(
                    "loss/conf_loss", conf_loss, step=global_steps
                )
                tf.summary.scalar(
                    "loss/prob_loss", prob_loss, step=global_steps
                )
                writer.flush()

        def test_step(image_data, target):
            with tf.GradientTape() as tape:
                pred_result = self.model(image_data, training=True)
                giou_loss = conf_loss = prob_loss = 0

                # optimizing process
                for i in range(3):
                    conv, pred = pred_result[i * 2], pred_result[i * 2 + 1]
                    loss_items = yolov4.compute_loss(
                        pred,
                        conv,
                        target[i][0],
                        target[i][1],
                        strides=self.strides,
                        num_class=self.num_class,
                        iou_loss_threshold=iou_loss_threshold,
                        i=i,
                    )
                    giou_loss += loss_items[0]
                    conf_loss += loss_items[1]
                    prob_loss += loss_items[2]

                total_loss = giou_loss + conf_loss + prob_loss

                tf.print(
                    "=> TEST STEP %4d   giou_loss: %4.2f   conf_loss: %4.2f   "
                    "prob_loss: %4.2f   total_loss: %4.2f"
                    % (
                        global_steps,
                        giou_loss,
                        conf_loss,
                        prob_loss,
                        total_loss,
                    )
                )

        for epoch in range(epochs):
            if epoch < first_stage_epochs:
                if not isfreeze:
                    isfreeze = True
                    for name in ["conv2d_93", "conv2d_101", "conv2d_109"]:
                        freeze = self.model.get_layer(name)
                        utils.freeze_all(freeze)
            elif epoch >= first_stage_epochs:
                if isfreeze:
                    isfreeze = False
                    for name in ["conv2d_93", "conv2d_101", "conv2d_109"]:
                        freeze = self.model.get_layer(name)
                        utils.unfreeze_all(freeze)

            for image_data, target in trainset:
                train_step(image_data, target)
            for image_data, target in testset:
                test_step(image_data, target)

            if epoch % save_interval == 0:
                self.model.save_weights(trained_weights_path)

        self.model.save_weights(trained_weights_path)

    def make_model(self, is_training=False):
        self._has_weights = False
        tf.keras.backend.clear_session()
        input_layer = tf.keras.layers.Input([self.height, self.width, 3])
        feature_maps = yolov4.YOLOv4(input_layer, self.num_class)

        bbox_tensors = []
        for i, fm in enumerate(feature_maps):
            if is_training:
                bbox_tensor = yolov4.decode_train(
                    fm,
                    self.num_class,
                    self.strides,
                    self.anchors,
                    i,
                    self.xyscale,
                )
                bbox_tensors.append(fm)

            else:
                bbox_tensor = yolov4.decode(fm, self.num_class, i)

            bbox_tensors.append(bbox_tensor)

        self.model = tf.keras.Model(input_layer, bbox_tensors)

    def predict(self, frame, classes):
        frame_size = frame.shape[:2]

        image_data = utils.image_preprocess(
            np.copy(frame), [self.height, self.width]
        )
        image_data = image_data[np.newaxis, ...].astype(np.float32)

        pred_bbox = self.model.predict(image_data)

        pred_bbox = utils.postprocess_bbbox(
            pred_bbox, self.anchors, self.strides, self.xyscale
        )
        bboxes = utils.postprocess_boxes(
            pred_bbox, frame_size, self.width, 0.25
        )
        bboxes = utils.nms(bboxes, 0.213, method="nms")

        return utils.draw_bbox(frame, bboxes, classes)
