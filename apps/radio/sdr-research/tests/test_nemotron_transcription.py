"""Exercise audio chunk uploads and failure handling without a GPU or live API."""
import ast
import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import yaml

SOURCE = Path(__file__).parents[1] / '10a-voice-decoder-configmap.yaml'


class NemotronTranscriptionTests(unittest.TestCase):
    def setUp(self):
        source = yaml.safe_load(SOURCE.read_text())['data']['transcribe.py']
        tree = ast.parse(source)
        # Load imports/functions only; never start the recording scan loop.
        tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        self.ns = {}
        exec(compile(tree, str(SOURCE), 'exec'), self.ns)
        self.ns.update(MODEL_NAME='nemotron-transcribe', API_URL='http://test/v1/audio/transcriptions',
                       API_KEY='test-token', API_TIMEOUT=3600, CHUNK_SEC=30,
                       _HALLUCINATION_RE=self.ns['re'].compile(r'^\[noise\]$'))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = str(Path(self.tmp.name) / 'radio.wav')
        with wave.open(self.wav, 'wb') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(8000)
            f.writeframes(b'\0\0' * 8000 * 31)

    def test_uploads_valid_wav_chunks_with_pinned_model(self):
        requests = []
        def respond(request, timeout):
            requests.append(request)
            self.assertEqual(timeout, 3600)
            self.assertEqual(request.headers['Authorization'], 'Bearer test-token')
            self.assertIn(b'nemotron-transcribe', request.data)
            self.assertIn(b'RIFF', request.data)
            return io.BytesIO(b'{"segments":[{"text":"Radio check"},{"text":"[noise]"}]}')
        with patch('urllib.request.urlopen', side_effect=respond):
            self.assertEqual(self.ns['transcribe_file'](self.wav, 31), ['Radio check', 'Radio check'])
        self.assertEqual(len(requests), 2)

    def test_failed_later_chunk_discards_partial_result(self):
        with patch('urllib.request.urlopen', side_effect=[io.BytesIO(b'{"segments":[{"text":"partial"}]}'), TimeoutError()]):
            self.assertIsNone(self.ns['transcribe_file'](self.wav, 31))

    def test_malformed_response_is_retryable(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"error":"unavailable"}')):
            self.assertIsNone(self.ns['transcribe_file'](self.wav, 31))

    def test_valid_empty_segments_are_no_speech(self):
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(b'{"segments":[]}')):
            self.assertEqual(self.ns['transcribe_file'](self.wav, 31), [])


if __name__ == '__main__':
    unittest.main()
