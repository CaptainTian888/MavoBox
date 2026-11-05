import re
from pathlib import Path
from .base import Client, Fatal
from ..schemas import Word, checked, validate_words


def estimate_words(words, duration):
    if isinstance(words, str): words = re.findall(r'[\u3400-\u9fff]|[^\W_]+|[^\s]', words)
    total = sum(len(w) for w in words)
    output, elapsed = [], 0.
    for word in words:
        end = elapsed + duration * len(word) / max(total, 1)
        output.append(dict(word=word, start=elapsed, end=end))
        elapsed = end
    return output


class ASRClient(Client):
    def transcribe(self, path, duration, language=None):
        data = {'model':self.provider['model'], 'response_format':'verbose_json', 'timestamp_granularities[]':'word'}
        if language: data['language'] = language
        with Path(path).open('rb') as source:
            value = self.json(self.request('POST', self.extra.get('transcriptions_path','/audio/transcriptions'),
                                          data=data, files={'file':(Path(path).name, source)}))
        try:
            return validate_words([checked(Word, w) for w in value['words']], duration)
        except (KeyError, ValueError, TypeError):
            raise Fatal('ASR 未返回有效词级时间戳') from None
