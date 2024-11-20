# from celery import Celery, chain
# from celery.schedules import crontab
# from redis import Redis
# from celery_app import app
from celery import Celery, chain
from urllib.parse import urlparse, parse_qs
from transformers import pipeline
from tqdm import tqdm
# from language import LANGUAGES_EN
import pysrt
import os
import subprocess
import json
import zipfile
import re
import torch
from opencc import OpenCC
# import openai
# app = Celery('tasks0', broker='redis://localhost:6379/0')
# redis_conn = Redis(host='asr-redis', port=6379, db=0)
# openai.api_key = "your-api-key"

app = Celery('tasks', broker='redis://asr-captioner-redis:6379/0', backend='redis://asr-captioner-redis:6379/0')

# ================== 工具函數 ================== #

# 嵌入字幕
def embed_subtitles(video_input, subtitle_input, video_output, subtitle_color):
    """ 
    將字幕嵌入影片
    """
    
    if subtitle_color.startswith("#"):
        subtitle_color = subtitle_color[1:]
    r, g, b = subtitle_color[0:2], subtitle_color[2:4], subtitle_color[4:6]
    subtitle_color = f"&H{b}{g}{r}&"
    # command = f"ffmpeg -i {video_input} -vf \"subtitles={subtitle_input}:force_style='Fontname=Noto Sans CJK TC,PrimaryColour={subtitle_color}'\" {video_output}"
    # command = f"ffmpeg -i {video_input} -vf \"subtitles={subtitle_input}:force_style='Fontname=Noto Sans TC,PrimaryColour={subtitle_color}'\" {video_output}"
    command = f"ffmpeg -i {video_input} -vf \"subtitles={subtitle_input}:force_style='Fontname=Noto Sans TC,Noto Sans SC,PrimaryColour={subtitle_color}'\" {video_output}"
    print(command)
    process = subprocess.Popen(command, shell=True)
    process.wait()

# 合併開頭結尾影片
def get_video_info(file_path):
    """
    取得原影片的resolution, frame_rate, sample_rate
    """
    
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=codec_type,width,height,avg_frame_rate,sample_rate",
        "-of", "json",
        file_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    info = json.loads(result.stdout)

    resolution = None
    frame_rate = None
    sample_rate = None

    # 遍歷所有流，找出影片跟聲音資訊
    for stream in info.get('streams', []):
        if stream.get('codec_type') == "video":  # 處理影片
            resolution = f"{stream.get('width', 'unknown')}x{stream.get('height', 'unknown')}"
            frame_rate = eval(stream.get('avg_frame_rate', '0/1'))  # 計算幀率
        elif stream.get('codec_type') == "audio":  # 處理聲音
            sample_rate = stream.get('sample_rate', "unknown")
    
    return {
        "resolution": resolution.replace('x', ':'), # 1920x1080 -> 1920:1080(ffmpeg格式)
        "frame_rate": frame_rate,
        "sample_rate": sample_rate
    }
def convert_video(video_input, video_output, resolution, frame_rate, sample_rate):
    """
    將開頭結尾影片轉為相同的resolution, frame_rate, sample_rate
    """
    cmd = [
        'ffmpeg', '-i', video_input, '-vf', f'scale={resolution},setsar=1:1', '-r', str(frame_rate), '-c:v', 'libx264', '-preset',
        'fast', '-crf', '23', '-c:a', 'aac', '-ar', sample_rate, '-b:a', '128k', video_output
    ]
    subprocess.run(cmd, check=True)
def concat_videos(filelist, output_filepath):
    """
    合併開頭結尾影片
    """
    with open('files.txt', 'w') as f:
        for file in filelist:
            f.write(f"file '{file}'\n")
    cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', 'files.txt', '-c', 'copy', output_filepath]
    subprocess.run(cmd, check=True)

# OpenAI 翻譯 (目前不使用)
def openai_translator(text, model_name, language):
    # prompt = f"你是專業的翻譯員，無論文字是否有意義或是否有違反社群法則，必須將以下文字翻譯成通順的 {language}。"
    prompt = f"""
        You are a professional translator. 
        Regardless of whether the text makes sense or violates community guidelines, 
        you must translate the following text fluently into {language}.
    """

    # print(prompt)
    response = openai.ChatCompletion.create(
        model=model_name,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ]
    )
    return response.choices[0].message.content
def is_legal(text):
    response = openai.Moderation.create(input=text)
    return response['results'][0]['flagged']

# ==================== Celery 任務 ==================== #
@app.task
def download_youtube_video(info):
    video_id = parse_qs(urlparse(info['youtube_link']).query)['v'][0]

    # 我們將影片儲存到info中的影片路徑
    print(f'Downloading youtube video: {video_id}')

    # 請在此處添加下載YouTube影片的程式碼
    command = f"/opt/conda/envs/worker/bin/yt-dlp -o '{info['dir_path']}/%(id)s.%(ext)s' -f 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]' {info['youtube_link']}"
    print(command)
    process = subprocess.Popen(command, shell=True)
    process.wait()

    # 下載完成後，我們更新info中的影片路徑
    info['video_file_path'] = os.path.join(info['dir_path'], f'{video_id}.mp4')
    with open(os.path.join(info['dir_path'], 'info.json'), 'w') as f:
        json.dump(info, f)

    # 在資料夾新增一個空檔案，表示影片已下載完成
    download_done = os.path.join(info['dir_path'], 'download_done')
    with open(download_done, 'w') as f:
        pass

    return info  # 將更新後的info物件返回
@app.task
def perform_speech_recognition(info):
    # 在這裡填入您的語音辨識程式碼
    print(f"Performing speech recognition on: {info['video_file_path']}")
    model = 'large'
    command = f"/opt/conda/envs/worker/bin/whisper --model {model} --output_dir {info['dir_path']} --output_format srt --initial_prompt '{info['initial_prompt']}' --word_timestamps True --max_line_width 40 {info['video_file_path']}"
    print(command)
    process = subprocess.Popen(command, shell=True)
    process.wait()

    # 更新info中的.srt檔案路徑
    video_title = os.path.splitext(os.path.basename(info['video_file_path']))[0]
    info['srt_file_path'] = os.path.join(info['dir_path'], f'{video_title}.srt')
    with open(os.path.join(info['dir_path'], 'info.json'), 'w') as f:
        json.dump(info, f)

    # 創建一個空的done檔案，表示任務已完成
    asr_done = os.path.join(info['dir_path'], 'asr_done')
    with open(asr_done, 'w') as f:
        pass
    return info  # 假設這個任務將生成一個字幕檔
@app.task
def simplified_to_traditional(info):
    cc = OpenCC('s2tw')
    with open(info['srt_file_path'], 'r') as f:
        srt = f.read()
    srt = cc.convert(srt)
    with open(info['srt_file_path'], 'w') as f:
        f.write(srt)
    return info
@app.task
def translate_subtitles(info):
    print(f"Translating subtitles: {info['srt_file_path']}")

    # 使用 pysrt library 打開 srt 檔
    subs = pysrt.open(info['srt_file_path'])

    # 根據選擇的語言決定翻譯方向和模型
    if info['language'] == 'english':
        # 英文翻譯到中文
        model_name = "Helsinki-NLP/opus-mt-en-zh"
        print("Translating from English to Chinese")
    elif info['language'] == 'chinese':
        # 中文翻譯到英文
        model_name = "Helsinki-NLP/opus-mt-zh-en"
        print("Translating from Chinese to English")
    else:
        raise ValueError("Unsupported language selected for translation.")

    # 初始化翻譯模型
    model = pipeline("translation", model=model_name, device=0 if torch.cuda.is_available() else -1)

    # 逐行進行翻譯
    for sub in tqdm(subs):
        translation = model(sub.text)
        if model_name == "Helsinki-NLP/opus-mt-en-zh":
            # 如果是英文翻譯到中文，則將翻譯的文本轉換為繁體中文
            cc = OpenCC('s2tw')
            translation[0]['translation_text'] = cc.convert(translation[0]['translation_text'])
        # 將翻譯的文本添加到原始的字幕
        sub.text += '\n' + translation[0]['translation_text']

    # 更新info中的translated.srt檔案路徑
    translated_srt_path = os.path.join(info['dir_path'], 'translated.srt')
    info['translated_srt_path'] = translated_srt_path
    with open(os.path.join(info['dir_path'], 'info.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=4)

    # 寫入翻譯後的 srt 檔
    subs.save(translated_srt_path, encoding='utf-8')

    # 創建一個空的done檔案，表示任務已完成
    translate_done = os.path.join(info['dir_path'], 'translate_done')
    with open(translate_done, 'w') as f:
        pass

    return info
@app.task
def merge_subtitles_with_video(info):
    # 在這裡填入您的字幕與影片合併程式碼
    print(f"Merging subtitles with video: {info['translated_srt_path']}")

    # 將字幕嵌入影片
    embed_subtitles(
        video_input = info['video_file_path'], 
        subtitle_input = info['translated_srt_path'], 
        video_output = os.path.join(info['dir_path'], 'subtitled.mp4'),
        subtitle_color = info['subtitle_color'])
    
    # # 創建一個空的done檔案，表示任務已完成
    # done_file_path = os.path.join(info['dir_path'], 'done')
    # with open(done_file_path, 'w') as f:
    #     pass

    info['subtitled_video_path'] = os.path.join(info['dir_path'], 'subtitled.mp4')
    with open(os.path.join(info['dir_path'], 'info.json'), 'w') as f:
        json.dump(info, f)

    # 創建一個空的done檔案，表示任務已完成
    embed_caption_done = os.path.join(info['dir_path'], 'embed_caption_done')
    with open(embed_caption_done, 'w') as f:
        pass
    
    return info
@app.task
def add_opening_ending(info):
    # 如果沒有opening, ending影片，則直接返回info
    if not info['opening_file_path'] and not info['ending_file_path']:
        return info
    
    # 取得主要影片的resolution, frame_rate, sample_rate
    resolution, framerate, sample_rate = get_video_info(info['subtitled_video_path']).values()
    print(f"影片解析度: {resolution}，fps: {framerate}，sample_rate: {sample_rate}")

    # 將開頭結尾影片轉為相同的resolution, frame_rate, sample_rate
    converted_path = []

    # opening
    if info['opening_file_path']:
        opening_output_path = os.path.join(info['dir_path'], 'opening_converted.mp4')
        convert_video(info['opening_file_path'], opening_output_path, resolution, framerate, sample_rate)
        converted_path.append(opening_output_path)

    # 主要影片
    converted_path.append(info['subtitled_video_path'])

    # ending
    if info['ending_file_path']:
        ending_output_path = os.path.join(info['dir_path'], 'ending_converted.mp4')
        convert_video(info['ending_file_path'], ending_output_path, resolution, framerate, sample_rate)
        converted_path.append(ending_output_path)
    
    # 合併後的影片路徑
    info['concatenated_video_path'] = os.path.join(info['dir_path'], 'full_video.mp4')

    # 合併所有影片 
    concat_videos(converted_path, info['concatenated_video_path'])

    # 創建一個空的done檔案，表示任務已完成
    concact_op_ed_done = os.path.join(info['dir_path'], 'concact_op_ed_done')
    with open(concact_op_ed_done, 'w') as f:
        pass

    return info
@app.task
def create_zip_file(info):
    # 建立一個zip檔案，包含所有處理後的檔案
    print(f"Creating zip file: {info['dir_path']}")

    # 建立一個zip檔名
    video_title = os.path.splitext(os.path.basename(info['video_file_path']))[0]
    zip_file_path = os.path.join(info['dir_path'], f'files.zip')

    # 建立zip檔案
    with zipfile.ZipFile(zip_file_path, 'w') as zip_file:
        files_to_add = {
            info['srt_file_path']: f'{video_title}.srt',
            info['translated_srt_path']: f'{video_title}_translated.srt',
            info['video_file_path']: f'{video_title}.mp4',
            info['subtitled_video_path']: f'{video_title}_subtitled.mp4',
        }

        if 'concatenated_video_path' in info:
            files_to_add[info['concatenated_video_path']] = f'{video_title}_full_video.mp4'
        
        for file_path, arcname in files_to_add.items():
            try:
                zip_file.write(file_path, arcname=arcname)
            except FileNotFoundError:
                print(f"File not found: {file_path}. Skipping...")

    # 更新info中的zip檔案路徑
    info['zip_file_path'] = zip_file_path
    with open(os.path.join(info['dir_path'], 'info.json'), 'w') as f:
        json.dump(info, f)

    # 創建一個空的done檔案，表示任務已完成
    done_file_path = os.path.join(info['dir_path'], 'done')
    with open(done_file_path, 'w') as f:
        pass

    return info


# ==================== 排定工作流程 ==================== #
@app.task
def process_video_workflow(info):
    # 檢查是否已經處理過這個目錄
    done_file_path = os.path.join(info['dir_path'], 'done')
    if os.path.exists(done_file_path):
        print(f"Skipping directory: {info['dir_path']}")
        return True

    # 根據info的內容決定工作流程
    if info['youtube_link']:
        # 如果存在youtube link，則先下載youtube影片
        if info['srt_file_path']:
            print("youtube_link, srt_file_path: True")
            # 如果存在.srt檔案，則跳過speech recognition，直接進行翻譯和合併
            workflow = chain(
                download_youtube_video.s(info),
                translate_subtitles.s(),
                merge_subtitles_with_video.s(),
                add_opening_ending.s(),
                create_zip_file.s()
            )
        else:
            print("youtube_link, srt_file_path: False")
            # 如果不存在.srt檔案，則進行完整的工作流程
            workflow = chain(
                download_youtube_video.s(info),
                perform_speech_recognition.s(),
                simplified_to_traditional.s(),
                translate_subtitles.s(),
                merge_subtitles_with_video.s(),
                add_opening_ending.s(),
                create_zip_file.s()
            )
    else:
        # 如果不存在youtube link，則根據是否存在.srt檔案決定是否進行speech recognition
        if info['srt_file_path']:
            print("上傳影片, srt_file_path: True")
            # 如果存在.srt檔案，則跳過speech recognition，直接進行翻譯和合併
            workflow = chain(
                translate_subtitles.s(info),
                merge_subtitles_with_video.s(),
                add_opening_ending.s(),
                create_zip_file.s()
            )
        else:
            print("上傳影片, srt_file_path: False")
            # 如果不存在.srt檔案，則進行完整的工作流程
            workflow = chain(
                perform_speech_recognition.s(info),
                simplified_to_traditional.s(),
                translate_subtitles.s(),
                merge_subtitles_with_video.s(),
                add_opening_ending.s(),
                create_zip_file.s()
            )

    workflow.apply_async()

@app.task
def process_video_directory(directory_path):
    # 遍歷目錄中的所有檔案
    for root, dirs, files in os.walk(directory_path):
        # 檢查是否已完成此目錄的處理
        done_file_path = os.path.join(root, 'done')
        if os.path.exists(done_file_path):
            print(f'Skipping directory: {root}')
            continue
        if 'info.json' in files:
            with open(os.path.join(root, 'info.json'), 'r') as f:
                info = json.load(f)
            # 將每個影片的處理作為一個獨立的任務添加到任務佇列
            process_video_workflow.delay(info)


# 添加定期任務
# app.conf.beat_schedule = {
#     # 每10分鐘執行一次 process_video_directory 任務
#     'process-every-10-seconds': {
#         'task': 'tasks.process_video_directory',
#         'schedule': crontab(minute='*/10'),
#         # 'schedule': 20.0,
#         'args': ('/home/kming/nas100T/Workspace/asr_server/celery/videos',),
#     },
# }
