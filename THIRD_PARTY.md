# 第三方组件与模型

本项目代码采用 MIT 许可证；该许可不替代第三方许可。分发二进制时请同时保留 `licenses/` 和运行时自带的许可文件。

| 组件 | 用途 | 许可与来源 |
| --- | --- | --- |
| Python、Tcl/Tk | 桌面运行环境 | Python/Tcl/Tk 各自许可，见 licenses 和运行时目录 |
| Pillow | 图片读取、套版 | HPND，见 licenses/Pillow-LICENSE |
| NumPy | 像素处理 | BSD 及内含组件许可，见 licenses/numpy-* |
| OpenCV | 图像、人脸检测 | Apache 2.0 及内含第三方许可，见 licenses/opencv-* |
| MediaPipe | 本地人像关键点 | Apache 2.0，见 licenses/mediapipe-* |
| pywin32 | Windows 打印 | 见 licenses/pywin32-* |
| pygrabber、comtypes | Windows 视频设备枚举 | MIT，见 licenses/pygrabber-* 与 comtypes-* |
| PyInstaller | 可执行程序打包 | GPL 附例外条款，见 licenses/pyinstaller-COPYING.txt |

其余运行时依赖的许可也保存在 `licenses/`。PyInstaller 二进制包含的运行时按各组件许可分发。

随附模型仅作本机推理，不做人脸身份识别：

- YuNet `face_detection_yunet_2023mar.onnx`，MIT：[OpenCV Zoo 官方仓库](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)，许可见 `licenses/YuNet-MIT.txt`。
- Face Landmarker float16 v1、Pose Landmarker Lite float16 v1，Google MediaPipe 模型，Apache 2.0，见 `licenses/MediaPipe-Apache-2.0.txt`。[人脸模型文档](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker)，[身体模型文档](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker)。
- 模型文件校验值见 `assets/models/SHA256SUMS.txt`。

Canon EDSDK、Sony Camera Remote SDK、LUMIX SDK 与 DNP 驱动不随项目分发。当前仅实现佳能原生 SDK 与通用 USB视频；索尼、松下原生 SDK 尚未接入。

默认 Logo 的黑色图形由作者提供，经内置图像工具制作透明底并加“logo可替换”；详见 docs/Logo制作说明.md。示例模板、欢迎页和相机图标为本项目生成。工程不含先前现场使用的公司标识和客户照片。
