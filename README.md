# SliceEnglish

> 把最长 30 分钟的英文音频，切成一句一句的听写练习。

上传课堂录音、播客或新闻音频，自动获得句子级时间轴；先试听并修正切片，再自由跳转逐句听写和查看词级纠错。

## 为谁而做

面向想提升英语听力的大学生：不需要安装软件，打开网站、上传音频、开始练习。

一个把英语音频自动切成逐句听写练习的本地 MVP。支持最长 **30 分钟**、最大 **500MB** 的 MP3/WAV/M4A。

## 运行

1. 安装 [FFmpeg](https://ffmpeg.org/) 并确保 `ffmpeg`、`ffprobe` 位于 PATH。
2. 在本目录安装 Python 3.10+ 依赖：`pip install -r requirements.txt`
3. 启动：`python app.py`
4. 打开 `http://127.0.0.1:8000`。

默认使用 `base.en` faster-whisper 模型。可通过 `WHISPER_MODEL=small.en` 提升英文转写质量（首次运行会下载模型）；可通过 `SLICEENGLISH_WORK_DIR` 指定临时文件目录。

## 隐私与限制

- 上传内容仅写入 `.sessions/`，一小时未活动自动清理；关闭页面也会发起删除请求。上传分片会自动重试三次，服务端保留已收到的分片编号供上传器续传。
- 自动断句结合词级时间戳、Whisper VAD、标点和静音；低置信度或异常句会标记为“建议检查”。
- “准确”由预览页的试听、拖动边界、拆分、合并和文字修正来最终保证，不能对嘈杂或多人重叠语音承诺完全自动正确。

## 测试

`python -m unittest discover -s tests -p "test_*.py"`

## 分享部署（Render）

本项目含 FFmpeg 与本地 Whisper 推理，不能部署到 GitHub Pages。仓库已提供 `Dockerfile` 和 `render.yaml`，可按以下方式生成公开 HTTPS 链接：

1. 将 `sliceenglish/` 作为一个 GitHub 仓库推送。
2. 在 Render 新建 **Blueprint**，连接该仓库；它会读取 `render.yaml`。
3. 部署完成后，Render 会提供一个 `https://<服务名>.onrender.com` 链接，可直接分享。
4. 首次真实转写会下载 Whisper 模型，实例需要足够内存；建议先用 `base.en`，并使用带至少 2GB 内存的实例。

生产部署使用 Gunicorn，提供 `/healthz` 健康检查；上传的音频继续存放在容器临时目录，服务重启或会话超时会清除。
