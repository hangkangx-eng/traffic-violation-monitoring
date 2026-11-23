from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import csv
import io
import json
import os
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

import cv2
from fastapi import FastAPI, HTTPException, Request, UploadFile, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.schemas import MonitorConfig
from backend.ai import AIError, AISettings, AISettingsBody, Assistant, ChatBody
from backend.config import Configs, WebConfig, camera_id, validate_config
from backend.monitor import Monitor
from backend.store import Store


class StartBody(BaseModel):
    source: str = Field(min_length=1, max_length=2000)
    camera_id: str = 'cam_001'


class ReviewBody(BaseModel):
    status: str
    note: str = Field(default='', max_length=2000)


class RuleBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    camera_id: str = 'cam_001'


def create_app(root: Path | None = None, monitor_factory=Monitor):
    root = root or Path(os.environ.get('TRAFFIC_PROJECT_ROOT', Path(__file__).resolve().parent.parent))
    web_data = root / 'data' / 'web'
    uploads = web_data / 'uploads'
    uploads.mkdir(parents=True, exist_ok=True)
    store = Store(web_data / 'traffic.db')
    configs = Configs(root)
    monitor = monitor_factory(root, store)
    settings = AISettings(web_data / 'ai_settings.json')
    assistant = Assistant(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(monitor.stop)

    app = FastAPI(title='路瞳 · 交通违法行为监测 API', version='1.0.0', lifespan=lifespan)
    app.state.store, app.state.monitor, app.state.assistant = store, monitor, assistant

    @app.middleware('http')
    async def local_origin(request, call_next):
        # The default server binds loopback. Reject browser cross-origin writes.
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '不允许跨站修改本机监测服务'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(AIError)
    async def ai_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=502)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def runtime_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'service': 'traffic-monitor-web', 'version': '1.0.0'}

    @app.get('/api/status')
    def status():
        return monitor.status()

    @app.get('/api/stats')
    def stats():
        return store.stats()

    @app.get('/api/sessions')
    def sessions():
        return store.sessions()

    @app.post('/api/monitor/start')
    def start(body: StartBody):
        camera_id(body.camera_id)
        config = validate_config(configs.load(body.camera_id))
        return monitor.start(body.source, body.camera_id, config)

    @app.post('/api/monitor/stop')
    def stop():
        return monitor.stop()

    @app.get('/api/configs')
    def config_names():
        return configs.names()

    @app.get('/api/configs/{name}')
    def get_config(name: str):
        return configs.load(name)

    @app.put('/api/configs/{name}')
    def put_config(name: str, body: WebConfig):
        if name != body.camera_id:
            raise ValueError('路径中的摄像头 ID 与配置不一致')
        configs.save(body)
        monitor.update_config(body)
        return {'saved': True, 'config': body}

    @app.get('/api/sources')
    def sources():
        candidates = [root / 'samples' / 'traffic.mp4', *uploads.glob('*')]
        items = [{'id': str(path), 'name': path.name, 'size': path.stat().st_size,
                  'uploaded': path.parent == uploads} for path in candidates if path.is_file()]
        return items

    @app.post('/api/sources/upload')
    async def upload(file: UploadFile):
        name = (file.filename or 'video.mp4').replace('\\', '/').split('/')[-1]
        extension = Path(name).suffix.lower()
        if extension not in {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v'}:
            raise ValueError('请选择 mp4、avi、mov、mkv、webm 或 m4v 视频')
        safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', Path(name).stem)[:80]
        target = uploads / f'{safe_name}_{uuid.uuid4().hex[:8]}{extension}'
        size = 0
        try:
            with target.open('wb') as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 2 * 1024**3:
                        raise ValueError('单个视频最大为 2 GB')
                    output.write(chunk)
            cap = await asyncio.to_thread(cv2.VideoCapture, str(target))
            try:
                ok, frame = await asyncio.to_thread(cap.read)
                if not cap.isOpened() or not ok:
                    raise ValueError('该文件无法解码为视频')
            finally:
                cap.release()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return {'id': str(target), 'name': target.name, 'size': size, 'uploaded': True}

    @app.get('/api/preview')
    def preview(source: str = Query(max_length=2000)):
        if not Path(source).is_file():
            raise ValueError('预览仅支持已存在的本地视频文件')
        cap = cv2.VideoCapture(source)
        try:
            ok, frame = cap.read()
            if not ok:
                raise ValueError('视频预览失败，请检查格式')
            frame = cv2.resize(frame, (960, 540))
            ok, jpeg = cv2.imencode('.jpg', frame)
            return Response(jpeg.tobytes(), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})
        finally:
            cap.release()

    @app.get('/api/frame')
    def frame():
        data = monitor.frame()
        if data is None:
            raise HTTPException(404, '尚无检测画面')
        return Response(data, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.get('/api/stream')
    async def stream(request: Request):
        async def generate():
            while not await request.is_disconnected():
                data = monitor.frame()
                if data:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data + b'\r\n'
                await asyncio.sleep(.1)
        return StreamingResponse(generate(), media_type='multipart/x-mixed-replace; boundary=frame',
                                 headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @app.get('/api/events')
    def events(event_type: str = '', review_status: str = '', session_id: str = '', camera_id: str = '',
               since: str = '', until: str = '', limit: int = Query(default=30, ge=1, le=200), offset: int = Query(default=0, ge=0)):
        return store.events(event_type, review_status, session_id, camera_id, since, until, limit, offset)

    @app.get('/api/events/export')
    def export(event_type: str = '', review_status: str = '', since: str = '', until: str = ''):
        rows = store.events(event_type=event_type, review_status=review_status, since=since, until=until, limit=100000)['items']
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['事件ID', '摄像头', '类型', '轨迹ID', '时间', '描述', '复核状态', '备注'])
        for row in rows:
            values = [row[key] for key in ['id', 'camera_id', 'event_type', 'track_id', 'timestamp', 'message', 'review_status', 'note']]
            writer.writerow([("'" + v) if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values])
        return Response(buffer.getvalue().encode('utf-8-sig'), media_type='text/csv',
                        headers={'Content-Disposition': 'attachment; filename="traffic_events.csv"'})

    @app.get('/api/events/{event_id}/snapshot')
    def snapshot(event_id: str):
        row = store.event(event_id)
        if not row or not row['snapshot_path']:
            raise HTTPException(404, '该事件没有截图')
        path = Path(row['snapshot_path']).resolve()
        if not path.is_relative_to((web_data / 'snapshots').resolve()) or not path.is_file():
            raise HTTPException(404, '截图不存在')
        return FileResponse(path, media_type='image/jpeg')

    @app.patch('/api/events/{event_id}/review')
    def review(event_id: str, body: ReviewBody):
        if body.status not in {'pending', 'confirmed', 'dismissed'}:
            raise ValueError('无效的复核状态')
        if not store.review(event_id, body.status, body.note):
            raise HTTPException(404, '事件不存在')
        return {'saved': True}

    @app.get('/api/ai/settings')
    def ai_settings():
        return settings.public()

    @app.put('/api/ai/settings')
    def save_ai(body: AISettingsBody):
        return settings.save(body)

    @app.post('/api/ai/test')
    def test_ai():
        result = assistant.complete([{'role': 'user', 'content': '请只回复：连接成功'}])
        return {'connected': True, **result}

    def context_for(body):
        current = monitor.status()
        camera = current.get('camera_id') or 'cam_001'
        context = {'status': current, 'stats': store.stats(), 'config': configs.load(camera).model_dump(),
                   'recent_events': store.events(limit=30)['items'], 'scope': '统计为 Web 后端全部已保存事件；近期事件最多 30 条'}
        source = current.get('source', '')
        context['status'] = dict(current, source=Path(source).name if '://' not in source else 'RTSP 视频流')
        if body.event_id:
            row = store.event(body.event_id)
            if not row:
                raise ValueError('选中的事件不存在')
            row['meta'] = json.loads(row['meta'])
            row.pop('snapshot_path', None)
            context['selected_event'] = row
        return context

    @app.post('/api/ai/chat')
    def chat(body: ChatBody):
        frame_data = None
        if body.attach_frame:
            if body.event_id:
                row = store.event(body.event_id)
                path = Path(row['snapshot_path']) if row and row['snapshot_path'] else None
                if path and path.resolve().is_relative_to((web_data / 'snapshots').resolve()) and path.is_file():
                    frame_data = path.read_bytes()
            else:
                frame_data = monitor.frame()
            if not frame_data:
                raise ValueError('暂无可附加的画面，请先启动监测或选择含截图的告警')
        context = context_for(body)
        result = assistant.chat(body, context, frame_data)
        result['frame_attached'] = bool(frame_data)
        if body.mode == 'report':
            report = {'id': uuid.uuid4().hex, 'created_at': Store.now(), 'title': '交通监测分析报告',
                      'content': result['content'], 'model': result['model']}
            store.add_report(report)
            result['report'] = report
        return result

    @app.post('/api/ai/rules/preview')
    def rules_preview(body: RuleBody):
        return assistant.rules(body.message, configs.load(body.camera_id))

    @app.get('/api/reports')
    def reports():
        return store.reports()

    @app.get('/api/reports/{report_id}/download')
    def report_download(report_id: str):
        report = store.report(report_id)
        if report is None:
            raise HTTPException(404, '报告不存在')
        return Response(report['content'], media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="traffic-report-{report_id[:8]}.md"'})

    frontend = Path(__file__).resolve().parent.parent / 'frontend'
    if frontend.is_dir():
        app.mount('/', StaticFiles(directory=frontend, html=True), name='frontend')
    return app


app = create_app()
