# 路瞳 · 基于 YOLOv11 的交通违法行为检测系统

面向固定机位交通视频的车辆行为监测项目，包含 **Web 前端、FastAPI 后端、Qt 桌面端、YOLOv11 车辆检测、ByteTrack 多目标跟踪**，以及通过 **DeepSeek API** 实现的告警分析、自然语言规则配置与画面问答。

支持超速、违停与逆行候选事件检测。告警、截图、人工复核记录和 AI 报告可保存在本机，重启后查询。

## 从 GitHub 下载并运行

### 1. 准备运行工具

- **Python 3.12（64 位）**：项目提供的启动器会创建独立虚拟环境。
- **Git + Git LFS**：用于取得完整模型和测试视频。
- 首次安装依赖需要联网；CPU 即可运行，不要求 NVIDIA GPU。

> 请使用 `git clone`，不要只下载源码 ZIP。模型和完整测试视频由 Git LFS 保存；缺少 Git LFS 时，拿到的可能只是几十字节的指针文件，不能用于检测或播放。

### 2. 下载完整仓库

```bash
git lfs install
git clone https://github.com/hangkangx-eng/traffic-violation-monitoring.git
cd traffic-violation-monitoring
git lfs pull
```

仓库包含：

| 文件 | 用途 |
| --- | --- |
| `yolo11s.pt` | YOLO11s 车辆检测预训练模型，约 19 MB |
| `samples/traffic.mp4` | 完整交通测试视频，约 473 MB（451 MiB） |

### 3. 启动 Web 版

**Windows：双击项目根目录的 `start_web.cmd`。**

第一次运行会自动查找 Python 3.12、创建 `.venv` 并安装锁定版本的依赖；安装完成后自动打开：

```text
http://127.0.0.1:8765
```

后续运行复用同一环境。也可以先双击 `setup_windows.cmd` 只安装依赖，再启动项目。

跨平台命令行方式：

```bash
python3.12 bootstrap.py web
```

如果 Windows 的 `python` 已指向已安装的 Python，可使用 `python bootstrap.py web`。启动器会检查并选择 Python 3.12。

启动后，在“实时监测”中选择自带的 `traffic.mp4`，点击“启动监测”。也可以上传其他视频或输入本地路径、RTSP 地址。浏览器界面无需安装 Node.js 或执行前端构建。

> 完整下载、首次依赖安装、视频选择和模型加载是运行所需步骤。AI API 密钥不包含在仓库中；不配置密钥也可以使用本地检测、标定、告警与复核功能。

## 哪个文件用于运行

| 入口 | 作用 |
| --- | --- |
| `start_web.cmd` | Windows 一键准备环境并启动 Web 版 |
| `setup_windows.cmd` | Windows 安装运行环境，不启动监测 |
| `start_desktop.cmd` | Windows 启动原 Qt 桌面版 |
| `bootstrap.py` | 环境准备、Web / Qt / 测试统一入口 |
| `launch_web.py` | 已安装依赖后的 Web 直接入口 |
| `app/main.py` | 原 Qt 桌面程序入口，以 `python -m app.main` 执行 |

在 VS Code 中打开整个仓库。先完成环境安装，再按 `Ctrl+Shift+B` 启动 Web 任务。在 PyCharm 中将解释器设为本仓库 `.venv`，运行 `launch_web.py`。不要直接运行 `backend/main.py`。

命令行可指定端口或关闭自动打开浏览器：

```bash
python bootstrap.py web --port 8766 --no-browser
```

终端中的 `Ctrl+C` 用于停止 Web 服务；页面中的“停止”只停止当前检测任务。

## 功能

### 实时视频监测

- YOLO11s 车辆检测与 ByteTrack 多目标跟踪。
- 接收本地视频、上传视频（单文件最多 2 GB）和 RTSP 视频流。
- MJPEG 画面、任务状态、处理帧率、当前轨迹数和任务累计轨迹数。
- 本地视频播放完毕后结束任务，不跨循环复用轨迹。
- 使用视频时间轴计算本地视频的速度与停留时长，减少 CPU 推理耗时对规则判定的影响。

### 三类候选行为检测

| 类型 | 判定依据 | 必需的场景配置 |
| --- | --- | --- |
| 超速 | A/B 双线时间、已知实际距离与区间位移估计 | 两条水平测速线、真实距离、米/像素、限速 |
| 违停 | 禁停区域内停留时长和车辆位移 | 禁停多边形、停留阈值、最大位移 |
| 逆行 | 车辆轨迹方向与合法行驶方向的余弦比较 | 车道多边形、方向箭头、时长和位移阈值 |

请先到“规则与标定”完成标注。画布采用 **960×540** 坐标：线条点击两个端点，多边形依次点击顶点后选择“完成区域”。支持多个禁停区、多条车道和配置保存。`examples/cam_001.json` 仅供理解配置结构，使用前仍应按当前视频重新标定。

### 告警保存与人工复核

- 保存事件类型、摄像头 ID、轨迹 ID、发生时间、触发参数和证据截图。
- 按日期、类型和复核状态查询历史事件。
- 记录“待复核 / 已确认 / 已排除”状态及人工备注。
- 导出 CSV；关闭网页、程序或重启电脑不会主动清空数据库。
- 原 Qt 历史截图没有对应事件元数据，不会自动导入 Web 数据库。

## DeepSeek AI 应用

进入“模型设置”，填写：

| 设置 | 默认值 |
| --- | --- |
| API 基础地址 | `https://api.deepseek.com` |
| 文本模型 | `deepseek-flash` |
| 视觉模型 | `deepseek-flash` |
| API 密钥 | 使用者自己的 API Key |

点击“保存设置”，再点击“测试连接”。模型名称可以调整，也支持兼容 Chat Completions 的服务。

1. **告警分析与报告**：结合真实统计、最多 30 条近期事件、当前摄像头规则及所选告警，解释触发原因、误报可能性，生成可保存、下载的 Markdown 报告。
2. **自然语言配置**：例如“限速改为 40 公里每小时，违停超过 2 分钟告警”。模型生成参数变更后会先进行字段与范围校验，展示修改预览；只有用户点击应用才保存。AI 不会自行绘制区域、执行命令或直接改写系统。
3. **画面问答**：主动勾选“附上当前画面或所选事件截图”后，将对应图片发给视觉模型。未勾选时仅发送相关事件与规则文本。单帧问答不等于完整视频的时序分析。

Windows 下 API Key 使用当前账户的 DPAPI 加密保存在本机，设置读取接口不会回传密钥；也支持 `DEEPSEEK_API_KEY` 环境变量。环境变量优先于本地文件。仓库不包含 API Key。

模型请求只在用户点击发送、测试、生成或规则预览时发起，可能消耗 API 额度。未配置密钥时会明确提示，不能把模板或测试模拟结果当作真实模型回答。

官方参考：[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)、[视觉输入](https://api-docs.deepseek.com/guides/vision/)。模型能力以服务提供者当前说明为准。

## 项目结构

```text
app/                      原 Qt 界面与视觉检测、跟踪、规则引擎
  main.py                 Qt 入口
  qt_window.py            桌面交互与场景标注
  detector.py             车辆检测
  ultralytics_tracker.py  YOLO / ByteTrack 适配
  rules.py                超速、违停、逆行规则
  video_processor.py      原桌面视频处理
  ultralytics/            原项目附带的第三方源码副本
backend/
  main.py                 FastAPI 路由与前端文件服务
  monitor.py              Web 检测任务与视频时间轴适配
  store.py                SQLite 持久化
  config.py               摄像头配置与参数校验
  ai.py                   模型调用、密钥保护与规则预览
frontend/
  index.html              页面结构
  styles.css              响应式样式
  app.js                  标注、监测、告警与 AI 页面交互
samples/traffic.mp4        自带完整测试视频（Git LFS）
examples/cam_001.json      配置结构示例
tests/test_web.py         自动测试
yolo11s.pt                检测模型（Git LFS）
bootstrap.py              环境安装与启动
launch_web.py             Web 服务入口
run_web_tests.py          测试入口
requirements.txt          视觉检测与桌面依赖
requirements-web.txt      Web 依赖
README.md                 本说明
THIRD_PARTY_NOTICES.md     第三方归属说明
```

原项目附带的 `app/ultralytics/` 保留。常规启动使用依赖清单安装的顶层 `ultralytics` 包。

## 数据保存与备份

首次运行会在仓库目录下创建：

| 路径 | 内容 |
| --- | --- |
| `data/web/traffic.db` | Web 告警、复核、任务和 AI 报告 |
| `data/web/snapshots/` | Web 告警截图 |
| `data/web/uploads/` | 用户上传的视频 |
| `data/web/ai_settings.json` | 本机模型设置与密钥 |
| `data/configs/` | Qt / Web 摄像头配置 |

这些运行数据不提交到 GitHub。停止服务后备份整个 `data/` 目录，可同时保留事件、图片、上传文件和配置。Windows 加密密钥绑定本机账户，迁移电脑后请重新填写 API Key。

本地视频事件时间为“本次任务启动时间 + 视频播放进度”；RTSP 采用收到帧时的本机时间。重复监测同一视频会产生新的任务和事件记录。

## API 文档与主要接口

服务启动后访问 `http://127.0.0.1:8765/docs`。

| 接口 | 用途 |
| --- | --- |
| `GET /api/health`、`/api/status`、`/api/stats` | 健康检查、任务状态和统计 |
| `POST /api/monitor/start`、`/api/monitor/stop` | 启停检测任务 |
| `GET /api/frame`、`/api/stream`、`/api/preview` | 单帧、实时视频和本地预览 |
| `GET /api/sources`、`POST /api/sources/upload` | 视频列表与上传 |
| `GET /api/configs`、`GET/PUT /api/configs/{id}` | 摄像头配置 |
| `GET /api/events`、`/api/events/export` | 历史筛选与 CSV 导出 |
| `GET /api/events/{id}/snapshot` | 证据截图 |
| `PATCH /api/events/{id}/review` | 人工复核记录 |
| `GET/PUT /api/ai/settings`、`POST /api/ai/test` | 模型设置与连接测试 |
| `POST /api/ai/chat`、`/api/ai/rules/preview` | AI 对话、报告和规则预览 |
| `GET /api/reports`、`/api/reports/{id}/download` | 报告归档与下载 |

## 自动测试

准备环境并执行：

```bash
python bootstrap.py test
```

或在已安装依赖的虚拟环境中执行：

```bash
python run_web_tests.py
```

测试覆盖配置校验、告警去重与持久化、复核与导出、视频上传与解码、密钥保护、AI 规则预览不自动应用、视觉请求格式和报告保存。AI 协议测试使用本机模拟服务，不会调用真实 DeepSeek 或消耗额度；真实连通性需使用者填写自己的密钥后测试。

## 常见问题

- **模型或视频只有几十字节**：这是 Git LFS 指针。安装 Git LFS，在仓库内执行 `git lfs pull`。
- **提示没有 Python 3.12**：安装 Python 3.12 64 位后重新运行启动器；无需复制别人的 `.venv`。
- **首次启动较慢**：首次下载 PyTorch、Qt 等依赖较大；完成后会复用本地环境。
- **端口被占用**：先停止重复启动的服务，或使用 `python bootstrap.py web --port 8766`。
- **没有违法候选告警**：先确认画面有车辆检测框，再检查区域、方向、测速线与规则开关；检测框不代表必然触发违法规则。
- **AI 连接失败**：检查模型名、API 基础地址、密钥、余额与网络。密钥不能直接从其他电脑的 DPAPI 文件恢复。

## 适用范围

当前版本面向单机研究与演示，默认仅监听 `127.0.0.1`，一次运行一个检测任务，未提供多用户登录或公网部署配置。车辆累计数按唯一轨迹 ID 计数，跟踪丢失后重新分配 ID 可能重复计数。测速依赖准确标定，单一米/像素比例无法完全消除透视误差。

检测输出为候选事件，需结合原视频和标定进行人工复核；AI 输出用于辅助解释。第三方代码、模型与测试视频的归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
