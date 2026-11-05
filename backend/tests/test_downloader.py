from pathlib import Path
import shutil
import time
from unittest.mock import patch


def test_download_pipeline_mocked_ytdlp(client, video):
    class FakeYDL:
        def __init__(self, options):
            self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            assert url.startswith('https://www.tiktok.com/')
            self.path = Path(self.options['outtmpl'].replace('%(ext)s', 'mp4'))
            shutil.copyfile(video, self.path)
            for hook in self.options['progress_hooks']:
                hook({'downloaded_bytes': 100, 'total_bytes': 100})
            return {'ext': 'mp4'}
        def prepare_filename(self, info): return str(self.path)

    with patch('modules.downloader.yt_dlp.YoutubeDL', FakeYDL):
        response = client.post('/api/downloads', json={'url': 'https://www.tiktok.com/@demo/video/123', 'seconds': 1})
        assert response.status_code == 202
        task_id = response.json()['id']
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            task = client.get(f'/api/tasks/{task_id}').json()
            if task['status'] in ('succeeded', 'failed'):
                break
            time.sleep(.02)
        assert task['status'] == 'succeeded', (
            f"Download task status={task['status']}, error={task.get('error')!r}; task={task}"
        )
    assets = client.get('/api/assets').json()
    assert len(assets) == 1
    assert .8 <= assets[0]['duration'] <= 1.2
