import torch
import cv2
import transformers
import fastapi
import ultralytics
import easyocr

print("Everything Installed Successfully!")

print("Torch:", torch.__version__)
print("CUDA Available:", torch.cuda.is_available())

if torch.cuda.is_available():
    print("CUDA Version:", torch.version.cuda)
    print("GPU:", torch.cuda.get_device_name(0))
else:
    print("GPU: CPU mode")