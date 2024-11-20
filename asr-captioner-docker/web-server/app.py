from flask import Flask, render_template, request, redirect, send_from_directory, session, url_for, jsonify
from werkzeug.utils import secure_filename
from celery import Celery

# from tasks import process_video_workflow
# from celery.result import AsyncResult
# from celery_app import app as celery
import os, uuid, json, validators, shutil
from language import LANGUAGES_ZH

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads/'
app.secret_key = "super secret key"
app.config['CELERY_BROKER_URL'] = 'redis://asr-captioner-redis:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://asr-captioner-redis:6379/0'

celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'], backend=app.config['CELERY_RESULT_BACKEND'])
celery.conf.update(app.config)

@app.route('/')
def index():
    return render_template('index.html', LANGUAGES=LANGUAGES_ZH)

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files['file']
        srt = request.files['srt']
        opening = request.files['opening']
        ending = request.files['ending']
        youtube_link = request.form.get('youtube')
        initial_prompt = request.form.get('prompt')
        subtitle_color = request.form.get('subtitle_color')
        language = request.form.get('language')
        # language_id = request.form.get('translation_language')

        uid = uuid.uuid4() 
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], str(uid))

        # 驗證部分改為前端使用 Javascript 驗證
        # Ensure either file or youtube link is provided
        if not file and not youtube_link:
            message = "請上傳影片或輸入YouTube連結"
            return render_template('alert.html', message=message)
        
        # Validate youtube link
        if youtube_link and not validators.url(youtube_link):
            message = "YouTube連結格式不正確，請輸入正確的YouTube連結"
            return render_template('alert.html', message=message)

        # Validate file format
        if file and not file.filename.endswith('.mp4'):
            message = "影片格式不正確，請上傳mp4格式的影片"
            return render_template('alert.html', message=message)

        # Validate srt format
        if srt and not srt.filename.endswith('.srt'):
            message = "字幕格式不正確，請上傳srt格式的字幕"
            return render_template('alert.html', message=message)
        
        # Validate intro video format
        if opening and not opening.filename.endswith('.mp4'):
            message = "影片格式不正確，請上傳mp4格式的影片"
            return render_template('alert.html', message=message)
        
        # Validate outro video format
        if ending and not ending.filename.endswith('.mp4'):
            message = "影片格式不正確，請上傳mp4格式的影片"
            return render_template('alert.html', message=message)
        
        # ================== 驗證完畢 ================== # 
        os.makedirs(upload_path, exist_ok=True)

        # Save file
        file_path = None
        if file:
            # os.makedirs(upload_path, exist_ok=True)
            filename = secure_filename(file.filename)
            file_path = os.path.join(upload_path, filename)
            file.save(file_path)

        # Save srt if exists
        srt_path = None
        if srt:
            srt_filename = secure_filename(srt.filename)
            srt_path = os.path.join(upload_path, srt_filename)
            srt.save(srt_path)

        # Save intro video if exists
        opening_path = None
        if opening:
            opening_filename = secure_filename(opening.filename)
            opening_path = os.path.join(upload_path, opening_filename)
            opening.save(opening_path)

        # Save outro video if exists
        ending_path = None
        if ending:
            ending_filename = secure_filename(ending.filename)
            ending_path = os.path.join(upload_path, ending_filename)
            ending.save(ending_path)
        
        # Save YouTube link and initial prompt in JSON format
        info = {
            'dir_path': upload_path,
            'youtube_link': youtube_link,
            'initial_prompt': initial_prompt,
            'video_file_path': file_path,
            'srt_file_path': srt_path,
            'subtitle_color': subtitle_color,  
            'opening_file_path': opening_path,
            'ending_file_path': ending_path,
            # 'language_id': 'en'
            'language': language
        }
        with open(os.path.join(upload_path, 'info.json'), 'w') as f:
            json.dump(info, f)

        session['info'] = info
        session['uuid'] = str(uid)
        # return redirect('/process/' + str(uid))

        task = celery.send_task('tasks.process_video_workflow', args=[info])

        return redirect(url_for('process_video', uuid=session['uuid']))
    return redirect('/')

@app.route('/process/<uuid>', methods=['GET'])
def process_video(uuid):
    info = session.get('info', {})
    print(info)
    # process_video_workflow.delay(info)
    # 如果不存在 uuid 對應的資料夾，則回到首頁
    if not os.path.exists(info.get('dir_path', '')):
        return redirect('/')
    return render_template('process.html', info=info, uuid=uuid)


@app.route('/check-status/<uuid>', methods=['GET'])
def check_status(uuid):
    # Retrieve path from session
    info = session.get('info', {})
    dir_path = info.get('dir_path', '')
    done_file_path = os.path.join(dir_path, 'done')
    processing_status = {"status": "processing"}
    if os.path.exists(done_file_path):
        return {"status": "done"}
    if os.path.exists(os.path.join(dir_path, 'download_done')):
        # processing_status 加入 {'download_done': True}
        processing_status['download_done'] = True
    if os.path.exists(os.path.join(dir_path, 'asr_done')):
        processing_status['asr_done'] = True
    if os.path.exists(os.path.join(dir_path, 'translate_done')):
        processing_status['translate_done'] = True
    if os.path.exists(os.path.join(dir_path, 'embed_caption_done')):
        processing_status['embed_caption_done'] = True
    if os.path.exists(os.path.join(dir_path, 'concact_op_ed_done')):
        processing_status['concact_op_ed_done'] = True

    return processing_status


@app.route('/download/<uuid>', methods=['GET'])
def download_file(uuid):
    dir_path = os.path.join(app.config['UPLOAD_FOLDER'], str(uuid))
    # return send_from_directory(directory=dir_path, path='files.zip', as_attachment=True)
    try:
        return send_from_directory(directory=dir_path, path='files.zip', as_attachment=True)
    finally:
        # 下載完刪除資料夾
        if os.path.exists(dir_path):
            shutil.rmtree(dir_path)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=7000)
