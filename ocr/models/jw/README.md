# 俊瑋 去筆跡模型 handwriting-eraser-v10

- 來源：https://tzutzu-course-system.vercel.app/models/handwriting-eraser-v10.onnx（他的 eraser.html 載入）
- 授權：俊瑋口頭授權（2026-11-03，同公司），可用於訓練（當老師）與題庫系統。此 repo 為私有，不可外流。
- 輸入 `image` [b,3,h,w] RGB 0–1；輸出 `out` [b,2,h,w]，第 0 通道＝乾淨灰階。
- 他網頁的「打印版」＝ clip((g-0.25)/(0.88-0.25))；512 切塊、重疊 48。實作見 `ocr/dehand/bench_vs_jw.py`。
