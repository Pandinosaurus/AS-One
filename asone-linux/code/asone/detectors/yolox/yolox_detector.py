
import argparse
import numpy as np
import torch
import onnxruntime
import cv2
import os
import sys
import argparse



from yolox.yolox.utils import fuse_model, postprocess
from yolox.yolox.exp import get_exp
from yolox.yolox_utils import preprocess, COCO_CLASSES, multiclass_nms, demo_postprocess, vis

class YOLOxDetector:
    def __init__(self,
                 model_name=None,
                 exp_file=None,
                 weights=None, 
                 use_onnx=False,
                 use_cuda=False
                ):

        self.use_onnx = use_onnx
        self.device = 'cuda' if use_cuda else 'cpu'
        if weights is None:
            weights = os.path.join("weights", "yolov5n.pt")

        if model_name is None:
            model_name = 'yolox-s'

        if exp_file is None:
            exp_file = os.path.join("exps", "default", "yolox_s.py")
        # Load Model
        if self.use_onnx:
            self.model = self.load_onnx_model(use_cuda, weights)
        else:
            self.model = self.load_torch_model(weights, exp_file, model_name)


    def load_onnx_model(self, use_cuda, weights):
        # Load onnx 
        if use_cuda:
            providers = ['CUDAExecutionProvider','CPUExecutionProvider']
        else:
            providers = ['CPUExecutionProvider']
        model = onnxruntime.InferenceSession(weights, providers=providers)
        return model
     
    def load_torch_model(self, weights,
                         exp_file,model_name, 
                         fp16=True, fuse=False):
        # Device: CUDA and if fp16=True only then half precision floating point works 
        self.fp16 = bool(fp16) & ((not self.use_onnx or self.use_onnx) and self.device != 'cpu')
        exp = get_exp(exp_file, model_name)
        self.classes =  exp.num_classes
        model = exp.get_model()
        if self.device == "cuda":
            model.cuda() 
            if self.fp16:  # to FP16
                model.half()
        model.eval()
        ckpt = torch.load(weights, map_location="cpu")
        # load the model state dict
        model.load_state_dict(ckpt["model"])
        if fuse:
            model = fuse_model(model)
        return model
       
    
    def detect(self, image: list,
               conf_thres: float = 0.25,
               iou_thres: float = 0.45,
               with_p6 = False,
               agnostic_nms: bool = True,
               input_shape=(640, 640),
               max_det: int = 1000) -> list:
        
        original_image = image.copy()
        self.input_shape = input_shape
        # Image Preprocess for onnx models
        if self.use_onnx:
            processed_image, ratio = preprocess(image, self.input_shape)
        else:
            processed_image, ratio = preprocess(image, self.input_shape)
            processed_image = torch.from_numpy(processed_image).unsqueeze(0)
            processed_image = processed_image.float()
            if self.device == "cuda":
                processed_image = processed_image.cuda()
                if self.fp16:
                    processed_image = processed_image.half()
        # Inference
        if self.use_onnx:  # Run ONNX model
        # Model Input and Output
            model_inputs = {self.model.get_inputs()[0].name: processed_image[None, :, :, :]}
            detection = self.model.run(None, model_inputs)[0]
            # Postprrocessing
            detection = demo_postprocess(detection, self.input_shape, p6=with_p6)[0]
            boxes = detection[:, :4]
            scores = detection[:, 4:5] * detection[:, 5:]
            boxes_xyxy = np.ones_like(boxes)
            boxes_xyxy[:, 0] = boxes[:, 0] - boxes[:, 2]/2.
            boxes_xyxy[:, 1] = boxes[:, 1] - boxes[:, 3]/2.
            boxes_xyxy[:, 2] = boxes[:, 0] + boxes[:, 2]/2.
            boxes_xyxy[:, 3] = boxes[:, 1] + boxes[:, 3]/2.
            boxes_xyxy /= ratio
            detection = multiclass_nms(boxes_xyxy, scores, nms_thr=iou_thres, score_thr=conf_thres)

        # Run Pytorch model
        else:
            with torch.no_grad():
                prediction =  self.model(processed_image)
                prediction = postprocess(prediction,
                                    self.classes,
                                    conf_thres,
                                    iou_thres,
                                    class_agnostic=agnostic_nms
                                    )[0]
          
                prediction = prediction.detach().cpu().numpy()
                bboxes = prediction[:, 0:4]
            # Postprocessing
            bboxes /= ratio
            cls = prediction[:, 6]
            scores = prediction[:, 4] * prediction[:, 5]
            detection = []
            for box in range(len(bboxes)):
                pred = np.append(bboxes[box], scores[box])
                pred = np.append(pred, cls[box])
                detection.append(pred)
            detection = np.array(detection)
        
        #Draw Bboxes
        if detection is not None:
            final_boxes, final_scores, final_cls_inds = detection[:, :4], detection[:, 4], detection[:, 5]
            origin_img = vis(original_image, final_boxes, final_scores, final_cls_inds,
                            conf=conf_thres, class_names=COCO_CLASSES)
        
        image_info = {
            'width': image.shape[1],
            'height': image.shape[0],
        }
        return detection, image_info
    
if __name__ == '__main__':
   
    parser = argparse.ArgumentParser()

    parser.add_argument("--name", default=None, type=str, help="Model name")
    parser.add_argument("--expfile", type=str, default=None)
    parser.add_argument("--weights", type=str, default=None, help="Weights path")
    parser.add_argument("--onnx",  action='store_true', default=False, dest='onnx',help="Use onnx model or not")
    
    args = parser.parse_args()
    if args.onnx:
        yolox_detector = YOLOxDetector(weights=args.weights, use_onnx=args.onnx, use_cuda=False)
    else:
        yolox_detector = YOLOxDetector(model_name=args.name,
                                       exp_file=args.expfile,
                                       weights=args.weights,
                                       use_onnx=args.onnx,
                                       use_cuda=False)

    img = cv2.imread('/home/ajmair/benchmarking/asone/asone-linux/test.jpeg')
    # Detect Objects
    result =  yolox_detector.detect(img)
    # print(result)
    # cv2.imwrite("myoutput.jpg", result)
 

