import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
import cv2
import numpy as np

_import_root = tempfile.TemporaryDirectory()
os.environ['TRAFFIC_PROJECT_ROOT'] = _import_root.name

from fastapi.testclient import TestClient
from backend.main import create_app
from backend.ai import RulePatch, apply_rule_patch
from app.schemas import MonitorConfig


class FakeMonitor:
    def __init__(self, root, store):
        self.value = {'running': False, 'camera_id': 'cam_001', 'source': '', 'status_message': 'idle'}
    def status(self): return self.value.copy()
    def stop(self): self.value['running'] = False; return self.status()
    def frame(self): return b'\xff\xd8\xfftest-image'
    def update_config(self, config): pass
    def start(self, source, camera_id, config):
        if self.value['running']: raise ValueError('已有任务')
        self.value.update(running=True, camera_id=camera_id, source=source, status_message='running')
        return self.status()


class MockModel(BaseHTTPRequestHandler):
    payloads = []
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.payloads.append(data)
        content = '{"speed_limit_kmh":40,"parking_min_stay_sec":120}' if data.get('response_format') else '测试模型：基于真实事件的分析。'
        raw = json.dumps({'choices': [{'message': {'content': content}}], 'usage': {'total_tokens': 20}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(raw)
    def log_message(self, *args): pass


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ThreadingHTTPServer(('127.0.0.1', 0), MockModel)
        cls.thread = threading.Thread(target=cls.model.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.model.shutdown(); cls.model.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root, FakeMonitor)
        self.client = TestClient(self.app)
        self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def configure_model(self):
        response = self.client.put('/api/ai/settings', json={
            'base_url': f'http://127.0.0.1:{self.model.server_port}', 'model': 'mock-text',
            'vision_model': 'mock-vision', 'api_key': 'test-only-secret', 'supports_vision': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['key_configured'])
        self.assertNotIn('test-only-secret', response.text)
        if os.name == 'nt':
            self.assertNotIn('test-only-secret', (self.root/'data/web/ai_settings.json').read_text())

    def test_config_validation_and_persistence(self):
        config = self.client.get('/api/configs/cam_001').json()
        config['speed']['speed_limit_kmh'] = 42
        response = self.client.put('/api/configs/cam_001', json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/configs/cam_001').json()['speed']['speed_limit_kmh'], 42)
        config['speed']['speed_limit_kmh'] = -1
        self.assertEqual(self.client.put('/api/configs/cam_001', json=config).status_code, 400)
        config['speed']['line_a'] = [[1], [2]]
        self.assertEqual(self.client.put('/api/configs/cam_001', json=config).status_code, 422)
        self.assertFalse(self.client.get('/api/configs/bad.name').status_code == 200)

    def test_event_review_filter_export_and_restart(self):
        store = self.app.state.store
        for i, kind in enumerate(['overspeed','reverse_driving']):
            store.add_event({'id': str(i), 'event_type':kind, 'track_id': i,
                             'timestamp': '2026-11-23T12:00:00', 'message': '=malicious_formula',
                             'meta': {'speed_kmh':80}, 'snapshot_path': ''}, 's1','cam_001')
        store.add_event({'id':'0','event_type':'overspeed','track_id':0,'timestamp':'2026-11-23T12:00:00','message':'duplicate'},'s1','cam_001')
        self.assertEqual(self.client.get('/api/events').json()['total'], 2)
        response = self.client.patch('/api/events/0/review', json={'status':'confirmed','note':'已复核'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/events?review_status=confirmed').json()['total'], 1)
        self.assertIn("'=malicious_formula", self.client.get('/api/events/export').text)
        self.assertEqual(self.client.get('/api/events/0/snapshot').status_code,404)
        restarted = create_app(self.root, FakeMonitor)
        self.assertEqual(restarted.state.store.events()['total'],2)
        self.assertEqual(restarted.state.store.event('0')['note'],'已复核')

    def test_ai_preview_does_not_apply_and_visual_payload(self):
        self.configure_model()
        self.assertEqual(self.client.post('/api/ai/test').status_code,200)
        before = self.client.get('/api/configs/cam_001').json()
        result = self.client.post('/api/ai/rules/preview',json={'message':'限速40，违停2分钟'}).json()
        self.assertFalse(result['applied'])
        self.assertEqual(result['config']['speed']['speed_limit_kmh'],40)
        self.assertEqual(result['config']['parking']['min_stay_sec'],120)
        self.assertEqual(before,self.client.get('/api/configs/cam_001').json())
        result = self.client.post('/api/ai/chat',json={'message':'描述画面','attach_frame':True})
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(result.json()['frame_attached'])
        payload = MockModel.payloads[-1]
        self.assertEqual(payload['model'],'mock-vision')
        self.assertTrue(payload['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertNotIn('image_url', json.dumps(MockModel.payloads[-2]))
        response=self.client.post('/api/ai/chat',json={'message':'生成报告','mode':'report'})
        self.assertEqual(response.status_code,200,response.text)
        report=response.json()['report']
        self.assertEqual(len(self.client.get('/api/reports').json()),1)
        self.assertIn('测试模型',self.client.get('/api/reports/'+report['id']+'/download').text)

    def test_missing_key_and_cross_site_protection(self):
        result=self.client.post('/api/ai/chat',json={'message':'请分析'})
        self.assertEqual(result.status_code,502)
        self.assertIn('密钥',result.json()['detail'])
        response=self.client.post('/api/monitor/stop',headers={'Origin':'https://other.example'})
        self.assertEqual(response.status_code,403)
        response=self.client.post('/api/sources/upload',files={'file':('bad.exe',b'not a video')})
        self.assertEqual(response.status_code,400)

    def test_rule_patch_rejects_unsupported_or_out_of_range(self):
        with self.assertRaises(ValueError): RulePatch(speed_limit_kmh=-2)
        with self.assertRaises(ValueError): RulePatch(arbitrary_command='delete')
        config, changes=apply_rule_patch(MonitorConfig(), RulePatch(reverse_enabled=False))
        self.assertFalse(config.reverse.enabled)
        self.assertEqual(changes[0]['field'],'reverse_enabled')

    def test_upload_real_decodable_video(self):
        path=self.root/'sample.avi'
        writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(64,48))
        self.assertTrue(writer.isOpened())
        for i in range(5): writer.write(np.full((48,64,3),i*30,dtype=np.uint8))
        writer.release()
        response=self.client.post('/api/sources/upload',files={'file':('sample.avi',path.read_bytes(),'video/x-msvideo')})
        self.assertEqual(response.status_code,200,response.text)
        uploaded=Path(response.json()['id'])
        self.assertTrue(uploaded.is_file())
        self.assertTrue(uploaded.is_relative_to(self.root/'data/web/uploads'))
        preview=self.client.get('/api/preview',params={'source':str(uploaded)})
        self.assertEqual(preview.status_code,200)
        self.assertEqual(preview.headers['content-type'],'image/jpeg')


if __name__ == '__main__': unittest.main(verbosity=2)
