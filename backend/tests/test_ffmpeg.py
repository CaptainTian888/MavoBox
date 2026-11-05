from core.ffmpeg import probe, trim


def test_probe_and_trim(video, tmp_path):
    target = tmp_path / 'trim.mp4'
    trim(video, target, 1)
    result = probe(target)
    assert result['type'] == 'video'
    assert 0.8 <= result['duration'] <= 1.2
    assert result['width'] == 160
