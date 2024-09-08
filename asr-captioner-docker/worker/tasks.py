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
from opencc import OpenCC
# import openai
# app = Celery('tasks0', broker='redis://localhost:6379/0')
# redis_conn = Redis(host='asr-redis', port=6379, db=0)
# openai.api_key = "your-api-key"

app = Celery('tasks', broker='redis://asr-captioner-redis:6379/0', backend='redis://asr-captioner-redis:6379/0')

# ================== 工具函數 ================== #

def embed_subtitles(video_input, subtitle_input, video_output, subtitle_color):
    # command = f"ffmpeg -i {video_input} -vf \"subtitles={subtitle_input}:force_style='Fontname=Noto Sans CJK TC'\" {video_output}"
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

def get_video_info(filepath):
    cmd = ['ffmpeg', '-i', filepath]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True)
    except subprocess.CalledProcessError as e:
        output = e.output  # Get the output from the exception object

    # 使用正則表達式來尋找解析度和幀率
    resolution_match = re.search(r'(\d{2,5}x\d{2,5})', output)
    framerate_match = re.search(r'(\d{1,3}(\.\d{1,2})? fps)', output)

    if resolution_match:
        resolution = resolution_match.group(1)
    else:
        raise Exception(f"Cannot determine resolution for {filepath}")

    if framerate_match:
        framerate = framerate_match.group(1).split(" ")[0]  # 只獲取數字部分，不包括 "fps"
    else:
        raise Exception(f"Cannot determine framerate for {filepath}")

    return resolution, framerate

def convert_video(input_filepath, output_filepath, resolution, framerate):
    cmd = [
        'ffmpeg', '-y', '-i', input_filepath,
        '-c:v', 'libx264', '-c:a', 'aac', '-strict', 'experimental',
        '-b:a', '128k', '-vf', f"scale={resolution},setsar=1", '-r', framerate,
        output_filepath
    ]
    print(cmd)
    subprocess.run(cmd, check=True)

def concat_videos(filelist, output_filepath):
    # base_dir = os.path.dirname(filelist[0])
    # files_path = os.path.join(base_dir, 'files.txt')
    # with open(files_path, 'w') as f:
    with open('files.txt', 'w') as f:
        for file in filelist:
            f.write(f"file '{file}'\n")

    cmd = ['ffmpeg', '-y', '-f', 'concat', '-i', 'files.txt', '-c', 'copy', output_filepath]
    subprocess.run(cmd, check=True)

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
    model = 'medium'
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
    cc = OpenCC('s2t')
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
    model = pipeline("translation", model="Helsinki-NLP/opus-mt-zh-en", device=0)
    # language = LANGUAGES_EN[info['language_id']]

    # 逐行進行翻譯
    for sub in tqdm(subs):
        translation = model(sub.text)
        # 將翻譯的文本添加到原始的字幕
        sub.text += '\n' + translation[0]['translation_text']
        # translation = openai_translator(sub.text, "gpt-3.5-turbo", language)
        # sub.text += '\n' + translation
    
    # 更新info中的translated.srt檔案路徑
    translated_srt_path = os.path.join(info['dir_path'], 'translated.srt')
    info['translated_srt_path'] = translated_srt_path
    with open(os.path.join(info['dir_path'], 'info.json'), 'w') as f:
        json.dump(info, f)
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
    merge_done = os.path.join(info['dir_path'], 'merge_done')
    with open(merge_done, 'w') as f:
        pass
    
    return info

@app.task
def add_opening_ending(info):
    # 如果沒有opening, ending影片，則直接返回info
    if not info['opening_file_path'] and not info['ending_file_path']:
        return info
    
    # 比較 opening, subtitled_video, ending 的解析度與fps
    r, f = get_video_info(info['subtitled_video_path'])
    opening_r = opening_f = ending_r = ending_f = 0
    if info['opening_file_path']:
        opening_r, opening_f = get_video_info(info['opening_file_path'])
    if info['ending_file_path']:
        ending_r, ending_f = get_video_info(info['ending_file_path'])
    
    resolution, framerate = max(r, opening_r, ending_r), max(f, opening_f, ending_f)
    print(f"合併開頭與結尾後的影片解析度: {resolution}，fps: {framerate}")

    # 將所有要合併的影片轉為相同的解析度與fps
    converted_path = []
    if info['opening_file_path']:
        path = os.path.join(info['dir_path'], 'opening_converted.mp4')
        convert_video(info['opening_file_path'], path, resolution, framerate)
        converted_path.append(path)
    path = os.path.join(info['dir_path'], 'subtitled_video_converted.mp4')
    convert_video(info['subtitled_video_path'], path, resolution, framerate)
    converted_path.append(path)
    if info['ending_file_path']:
        path = os.path.join(info['dir_path'], 'ending_converted.mp4')
        convert_video(info['ending_file_path'], path, resolution, framerate)
        converted_path.append(path)

    concat_videos(converted_path, os.path.join(info['dir_path'], 'full_video.mp4'))

    # 將訊息寫入info.json
    info['concatenated_video_path'] = os.path.join(info['dir_path'], 'full_video.mp4')

    return info



# @app.task
# def concat_videos(info):
#     # 如果沒有opening, ending影片，則直接返回info
#     if not info['opening_file_path'] and not info['ending_file_path']:
#         return info

#     # 建立一個filelist.txt，包含要合併的所有影片(opening, subtitled, ending)
#     print(f"Concatenating videos: {info['dir_path']}")
#     filelist_path = os.path.join(info['dir_path'], 'filelist.txt')
#     with open(filelist_path, 'w') as f:
#         if info['opening_file_path']:
#             f.write(f"file '{os.path.basename(info['opening_file_path'])}'\n")
#         f.write(f"file '{os.path.basename(info['subtitled_video_path'])}'\n")
#         if info['ending_file_path']:
#             f.write(f"file '{os.path.basename(info['ending_file_path'])}'\n")
#     info['concatenated_video_path'] = os.path.join(info['dir_path'], 'concatenated.mp4')
#     command = f"ffmpeg -f concat -safe 0 -i {filelist_path} -c copy -y {info['concatenated_video_path']}"
#     print(command)
#     process = subprocess.Popen(command, shell=True)
#     process.wait()
#     return info

@app.task
def create_zip_file(info):
    # 建立一個zip檔案，包含所有處理後的檔案
    print(f"Creating zip file: {info['dir_path']}")

    # 建立一個zip檔名
    video_title = os.path.splitext(os.path.basename(info['video_file_path']))[0]
    zip_file_path = os.path.join(info['dir_path'], f'files.zip')

    # 建立zip檔案
    with zipfile.ZipFile(zip_file_path, 'w') as zip_file:
        # zip_file.write(info['srt_file_path'], arcname=f'{video_title}.srt')
        # zip_file.write(info['translated_srt_path'], arcname=f'{video_title}_translated.srt')
        # zip_file.write(info['subtitled_video_path'], arcname=f'{video_title}_subtitled.mp4')
        # zip_file.write(info['concatenated_video_path'], arcname=f'{video_title}_concatenated.mp4')
        files_to_add = {
            info['srt_file_path']: f'{video_title}.srt',
            info['translated_srt_path']: f'{video_title}_translated.srt',
            info['subtitled_video_path']: f'{video_title}_subtitled.mp4',
            # info['concatenated_video_path']: f'{video_title}_full_video.mp4'
            # info.get('concatenated_video_path', None): f'{video_title}_full_video.mp4'

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
                # concat_videos.s(),
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
                # concat_videos.s(),
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
                # concat_videos.s(),
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
                # concat_videos.s(),
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
