# 第三方组件说明

`app/ultralytics/` 是原项目已包含的 Ultralytics 源码副本，保留其原始文件头和作者归属。源码声明为 AGPL-3.0；详情见 [Ultralytics 项目](https://github.com/ultralytics/ultralytics) 与 [许可说明](https://www.ultralytics.com/license)。该目录的代码不是本项目全部自行开发的代码。

运行环境通过依赖清单安装 Ultralytics、PyTorch、OpenCV、PySide6、FastAPI 等第三方组件。各组件的权利、许可及使用条件以对应上游项目声明为准。

`yolo11s.pt` 为原项目使用的 YOLO11s 预训练权重。`samples/traffic.mp4` 为项目提供者选定的测试视频；仓库中的视频用于复现该项目的测试流程。
