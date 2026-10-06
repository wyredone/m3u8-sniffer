import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from src.hls_utils import is_segment_chunk, content_type_is_hls, make_capture_record
from src.stream_tester import test_hls_record
from src.browser_worker import BrowserThread, parse_m3u8_variants
from src.command_utils import win_quote, build_ffmpeg_command
from src.download_engine import download_record, session_headers
from src.export_utils import export_json
from src.gui import MainWindow

class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tokenized_segment(self):
        self.assertTrue(is_segment_chunk('https://example.org/segment.ts?token=abc'))
        self.assertTrue(is_segment_chunk('https://example.org/segment.m4s?token=abc'))
        self.assertFalse(is_segment_chunk('https://example.org/master.m3u8?token=abc'))

    def test_hls_mime(self):
        self.assertFalse(content_type_is_hls('video/mp4'))
        self.assertFalse(content_type_is_hls('application/dash+xml'))
        self.assertTrue(content_type_is_hls('application/vnd.apple.mpegurl'))

    def response(self, data, content_type, url='https://example.org/master.m3u8', status=200):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.url = url
        response.status_code = status
        response.ok = status < 400
        response.headers = {'content-type': content_type}
        response.iter_content.return_value = iter([data])
        return response

    def test_validation_checks_body(self):
        for data, ct, url, expected in [
            (b'#EXTM3U\n#EXTINF:10\na.ts', 'application/vnd.apple.mpegurl', 'https://example.org/master.m3u8', True),
            (b'<html>Login</html>', 'application/vnd.apple.mpegurl', 'https://example.org/master.m3u8', False),
            (b'not video', 'video/mp4', 'https://example.org/video.mp4', False),
            (b'\x00\x00\x00\x18ftypisom', 'video/mp4', 'https://example.org/video.mp4', True),
            (b'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"/>', 'application/dash+xml', 'https://example.org/manifest.mpd', True),
        ]:
            with self.subTest(ct=ct, data=data), patch('src.stream_tester.requests.get', return_value=self.response(data,ct,url)) as get:
                self.assertEqual(test_hls_record({'m3u8_url':url})['ok'], expected)
                self.assertTrue(get.call_args.kwargs['stream'])

    def test_bounded_probe(self):
        response = self.response(b'x'*4096,'video/mp4','https://example.org/video.mp4')
        yielded=[]
        def chunks(**kwargs):
            for i in range(100):
                yielded.append(i)
                yield b'x'*4096
        response.iter_content.side_effect=chunks
        with patch('src.stream_tester.requests.get',return_value=response):
            test_hls_record({'m3u8_url':response.url})
        self.assertEqual(len(yielded),2)

    def test_variant_parser_with_comment(self):
        variants = parse_m3u8_variants('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720\n#comment\nvideo.m3u8\n')
        self.assertEqual(variants[0]['url'],'video.m3u8')
        self.assertEqual(variants[0]['quality_label'],'720p')

    def test_command_quoting(self):
        self.assertEqual(win_quote("a'$(test)"),"'a''$(test)'")
        command=build_ffmpeg_command({'m3u8_url':'https://example.org/master.m3u8','referer':'https://example.org'})
        self.assertIn('-replace "`n", "`r`n"', command)
        self.assertNotIn('\\r\\n',command)

    def test_download_options_and_scoped_cookies(self):
        record={'m3u8_url':'https://example.org/master.m3u8','max_height':720,'request_headers':{'cookie':'session=secret','authorization':'Bearer test','host':'bad','origin':'https://example.org'}}
        with patch('yt_dlp.YoutubeDL') as cls, patch('shutil.which', return_value='/usr/bin/ffmpeg'):
            download_record(record,'video.mp4',lambda msg:None,lambda:False)
            options=cls.call_args.args[0]
            self.assertIn('bestaudio',options['format'])
            self.assertIn('height<=?720',options['format'])
            self.assertNotIn('cookie',options['http_headers'])
            self.assertNotIn('nocheckcertificate',options)
            cookie=cls.return_value.__enter__.return_value.cookiejar.set_cookie.call_args.args[0]
            self.assertEqual(cookie.domain,'example.org')

    def test_export_omits_auth_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'export.json'
            export_json([{'m3u8_url':'https://example.org','request_headers':{'cookie':'secret'},'response_headers':{'set-cookie':'secret'}}],path)
            self.assertNotIn('secret',path.read_text())

    def test_clear_resets_worker(self):
        worker=BrowserThread()
        worker._seen_response_ids['200:url'] = 0
        worker.reset_captures()
        asyncio.run(worker._process_commands())
        self.assertFalse(worker._seen_response_ids)

    def test_sorted_updates_keep_rows_consistent(self):
        window=MainWindow()
        first=make_capture_record(m3u8_url='https://example.org/a.m3u8',event_source='request')
        second=make_capture_record(m3u8_url='https://example.org/b.m3u8',event_source='request')
        first['score']=10; second['score']=20
        window.add_or_update_record(first); window.add_or_update_record(second)
        update=dict(first,score=100,event_source='response',status_code=200)
        window.add_or_update_record(update)
        row=window._find_row_by_id(first['id'])
        self.assertEqual(window.table.item(row,8).text(),first['m3u8_url'])
        window.close()

if __name__ == '__main__':
    unittest.main()

class StreamFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_validation_retained_and_ranks_above_failed(self):
        w = MainWindow()
        first = make_capture_record(m3u8_url='https://example.org/a.m3u8', source_page='https://example.org/video1')
        second = make_capture_record(m3u8_url='https://example.org/master.m3u8', source_page='https://example.org/video2')
        w.add_or_update_record(first); w.add_or_update_record(second)
        w.apply_validation(first, {'ok':True, 'status_code':200, 'audio':'Separate audio'})
        w.apply_validation(second, {'ok':False, 'status_code':403})
        self.assertGreater(w.records[0]['score'], w.records[1]['score'])
        self.assertEqual(w.records[1]['validation_state'], 'Expired / denied')
        w.add_or_update_record(dict(second, score=999))
        self.assertLess(w.records[1]['score'], w.records[0]['score'])
        w.page_filter.setCurrentIndex(w.page_filter.findData(first['source_page']))
        self.assertTrue(w.table.isRowHidden(w._find_row_by_id(second['id'])))
        w.close()

    def test_stale_validation_is_ignored(self):
        w = MainWindow()
        record = make_capture_record(m3u8_url='https://example.org/a.m3u8')
        w.add_or_update_record(record)
        w.records[0]['validation_revision'] = 1
        w.apply_validation(dict(record, validation_revision=0), {'ok':True})
        self.assertNotIn('validation_result', w.records[0])
        w.clear_clicked()
        w.apply_validation(record, {'ok':True}, w.validation_epoch-1)
        self.assertFalse(w.records)
        w.close()

    def test_download_metrics(self):
        w = MainWindow()
        w.show_download_progress({'downloaded_bytes':50,'total_bytes':100,'speed':10,'eta':5})
        self.assertEqual(w.progress_bar.value(),50)
        w.show_download_progress({'downloaded_bytes':10})
        self.assertEqual(w.progress_bar.maximum(),0)
        w.close()

    def test_audio_only_download_options(self):
        with patch('yt_dlp.YoutubeDL') as cls, patch('shutil.which', return_value='/usr/bin/ffmpeg'):
            download_record({'m3u8_url':'https://example.org/a.m3u8','audio_only':True},'audio.mp3',lambda x:None,lambda:False)
            options=cls.call_args.args[0]
            self.assertEqual(options['format'],'bestaudio/best')
            self.assertEqual(options['postprocessors'][0]['preferredcodec'],'mp3')
