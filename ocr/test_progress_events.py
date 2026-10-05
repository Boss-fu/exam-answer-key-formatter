"""輕量驗證：不跑模型，只檢查即時事件與預覽契約。"""
import json
import os
import tempfile
import unittest

import pymupdf

import pyexam


class ProgressEventsTest(unittest.TestCase):
    def test_native_pdf_emits_preview_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "tiny.pdf")
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "A short digital page with enough text for the native text path.")
            doc.save(source)
            old = os.environ.get("OCR_PROGRESS")
            os.environ["OCR_PROGRESS"] = "1"
            try:
                pyexam.process(source, os.path.join(tmp, "out"), dpi=72)
            finally:
                if old is None:
                    os.environ.pop("OCR_PROGRESS", None)
                else:
                    os.environ["OCR_PROGRESS"] = old
            out = os.path.join(tmp, "out")
            with open(os.path.join(out, "progress.jsonl"), encoding="utf-8") as f:
                events = [json.loads(line) for line in f if line.strip()]
            self.assertEqual([e["stage"] for e in events], ["render", "clean", "ocr"])
            for event in events:
                self.assertEqual(set(event), {"t", "stage", "page", "pages", "msg", "img"})
                self.assertEqual((event["page"], event["pages"]), (1, 1))
            self.assertTrue(os.path.isfile(os.path.join(out, "previews", "p1_orig.jpg")))
            self.assertTrue(os.path.isfile(os.path.join(out, "previews", "p1_clean.jpg")))


if __name__ == "__main__":
    unittest.main()
