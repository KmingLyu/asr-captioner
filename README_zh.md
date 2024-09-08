# asr-captioner

- 語音辨識模型：whisper
- 翻譯模型：Helsinki-NLP/opus-mt-zh-en
- 後端：python flask
- 排程：celery

主要功能包括語音辨識、翻譯、合併字幕與影片都在 task.py

## 安裝方式
### 使用 docker-compose 安裝
1. Server 須先安裝好 docker, docker-compose, 以及 gpu driver 等設定

2. 進入 asr-captioner-docker 資料夾:
```
cd asr-captioner-docker
```

3. 執行以下指令:
```
docker-compose up -d
```
  會建立 3 個 container
  - asr-captioner-web-server: 主要連線
  - asr-captioner-worker: 執行任務，包含youtube影片下載、語音辨識、翻譯、合併字幕與影片
  - asr-captioner-redis: 作為 broker 分配任務

3. 在瀏覽器輸入 "http://你的server.ip:7001"
4. 結束程式
```
docker-compose down
```

### 其他指令

查看程式執行狀況
```
docker logs asr-captioner-web-server
docker logs asr-captioner-worker
```

查看使用者上傳的檔案(預設處理完會自動刪除)
  
使用者上傳的檔案存放在 `asr-captioner-web-server` 與 `asr-captioner-worker` 裡面的 `/app/uploads`

可使用以下指令查看：
```
docker exec -it asr-captioner-web-server bash
cd uploads
```
