import os
import zipfile
import requests
import gzip
import io
import json
import subprocess
import shutil
import tempfile
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for, flash, send_file
from flask_bootstrap import Bootstrap5
from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired
from azure.storage.blob import BlobServiceClient
from io import BytesIO
from pathlib import Path

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Necessary for CSRF protection in Flask-WTF
Bootstrap5(app)

ROOT = Path(__file__).parent
os.environ["PATH"] += os.pathsep + str(ROOT / "bin")

# Azure Blob Storage setup (replace with your credentials)
BLOB_CONNECTION_STRING = os.getenv('BLOB_CONNECTION_STRING')
BLOB_CONTAINER_NAME = "convertedfiles"
blob_service_client = BlobServiceClient.from_connection_string(BLOB_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(BLOB_CONTAINER_NAME)

class NameForm(FlaskForm):
    name = StringField('Title', validators=[DataRequired()])

@app.route('/', methods=['GET', 'POST'])
def index():
    form = NameForm()
    if form.validate_on_submit():
        name = form.name.data
        return redirect(url_for('hello', name=name))
    return render_template('index.html', form=form)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/hello')
def hello():
    name = request.args.get('name')
    if name:
        return render_template('hello.html', name=name)
    else:
        return redirect(url_for('index'))
    
@app.route('/info')
def info():
    # Get the selected video's data from the query parameters
    video_title = request.args.get('title')
    video_data = request.args.get('data')

    # Convert the string back to JSON for rendering
    if video_data:
        video_data = json.loads(video_data)

    return render_template('info.html', title=video_title, data=video_data)


@app.route('/search')
def search_titles():
    gz_url = 'https://app.jw-cdn.org/catalogs/media/E.json.gz'

    try:
        json_data = fetch_and_decompress_gz(gz_url)
        videos = [{"title": o['title'], "data": o} for o in json_data if 'title' in o]  # Extract full object but show title
        return jsonify(videos)
    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"error": str(e)}), 500

def fetch_and_decompress_gz(gz_url):
    response = requests.get(gz_url, stream=True)
    if response.status_code == 200:
        gz_data = io.BytesIO(response.content)
        with gzip.GzipFile(fileobj=gz_data, mode='rb') as f:
            extracted_titles = []
            for line in f:
                try:
                    json_obj = json.loads(line.decode('utf-8'))
                    o = json_obj.get('o', {})
                    if json_obj['type'] == 'media-item' and o.get('keyParts', {}).get('formatCode') == 'VIDEO':
                        extracted_titles.append(o)
                except json.JSONDecodeError:
                    continue
            return extracted_titles
    else:
        raise Exception(f"Failed to download gz file from {gz_url}")
    
# Function to fetch video content as an in-memory stream
def download_video(video_url):
    response = requests.get(video_url, stream=True)
    video_stream = BytesIO()
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            video_stream.write(chunk)
    video_stream.seek(0)  # Reset the stream position to the beginning
    return video_stream  # Return video stream in memory
    
# Function to convert video to audio using FFmpeg
def convert_to_audio(video_path, audio_name):
    audio_path = f'/tmp/{audio_name}'
    command = ['ffmpeg', '-i', video_path, '-q:a', '0', '-map', 'a', audio_path]
    subprocess.run(command, check=True)
    return audio_path

def convert_to_audio_in_memory(video_stream):
    try:
        audio_stream = BytesIO()
        command = [
            'ffmpeg',
            '-i', 'pipe:0',   # Input from stdin (the video stream)
            '-vn',            # Disable the video part of the stream (keep audio only)
            '-ar', '44100',   # Set the audio sample rate to 44.1 kHz (common for MP3)
            '-ac', '2',       # Set the audio channel count to 2 (stereo)
            '-b:a', '192k',   # Set the audio bitrate to 192 kbps (you can adjust as needed)
            '-f', 'mp3',      # Specify MP3 as the output format
            'pipe:1'          # Output to stdout (the audio stream)
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        audio_output, _ = process.communicate(input=video_stream.getvalue())
        # Write audio output to in-memory BytesIO stream
        audio_stream.write(audio_output)
        audio_stream.seek(0)  # Reset stream position
        
        return audio_stream  # Return the audio as an in-memory stream
    except Exception as e:
        print(e)

# Function to create a ZIP file containing all the converted audio files
def create_zip(file_paths, zip_name):
    zip_path = f'/tmp/{zip_name}.zip'
    with shutil.ZipFile(zip_path, 'w') as zipf:
        for file_path in file_paths:
            zipf.write(file_path, os.path.basename(file_path))
    return zip_path

# Function to create a ZIP file containing all the converted audio files in memory
def create_zip_in_memory(files):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_zip:
        with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for i, file in enumerate(files):
                zipf.write(file, f'converted_video_{i + 1}.mkv')
        tmp_zip.seek(0)  # Reset the file position

    return tmp_zip.name  # Return the name of the temporary zip file

# Function to upload the ZIP file to Azure Blob Storage
def upload_to_blob(file_path, blob_name):
    blob_client = container_client.get_blob_client(blob_name)
    with open(file_path, 'rb') as data:
        blob_client.upload_blob(data)
    return blob_client.url

def upload_to_blob_from_file(file_path, blob_name):
    blob_client = container_client.get_blob_client(blob_name)
    # Upload the file directly to Azure Blob Storage
    with open(file_path, 'rb') as file:
        blob_client.upload_blob(file, blob_type="BlockBlob", overwrite=True)
    return blob_client.url  # Return the URL of the uploaded blob

# Helper function to fetch video download link based on the largest filesize
def fetch_largest_download_link(language_agnostic_natural_key):
    url = f"https://b.jw-cdn.org/apis/mediator/v1/media-items/E/{language_agnostic_natural_key}?clientType=www"
    response = requests.get(url)
    
    if response.status_code == 200:
        media_data = response.json()
        # Find the file with the largest size
        largest_file = max(media_data['media'][0]['files'], key=lambda f: f['filesize'])
        return largest_file['progressiveDownloadURL']
    else:
        raise Exception(f"Failed to retrieve download link for {language_agnostic_natural_key}")

def fetch_download_links(language_agnostic_natural_key):
    """
    Fetch the download links for both the English and Chinese Simplified versions of the video.
    Returns the largest video for both languages along with subtitle URLs if available.
    """
    def get_largest_file(media_data):
        return max(media_data['files'], key=lambda f: f['filesize'])
    
    base_url = "https://b.jw-cdn.org/apis/mediator/v1/media-items"

    # Fetch English media
    url_en = f"{base_url}/E/{language_agnostic_natural_key}?clientType=www"
    response_en = requests.get(url_en)
    
    # Fetch Chinese Simplified media
    url_chs = f"{base_url}/CHS/{language_agnostic_natural_key}?clientType=www"
    response_chs = requests.get(url_chs)
    
    if response_en.status_code == 200 and response_chs.status_code == 200:
        media_data_en = response_en.json().get('media', [])[0]
        media_data_chs = response_chs.json().get('media', [])[0]

        # Extract the largest video/audio file for each language
        largest_en = get_largest_file(media_data_en)
        largest_chs = get_largest_file(media_data_chs)

        # Fetch subtitles if available
        sub_e_url = largest_en.get('subtitles', {}).get('url')
        subtitles_en = sub_e_url if sub_e_url else 'None'
        sub_chs_url = largest_chs.get('subtitles', {}).get('url')
        subtitles_chs = sub_chs_url if sub_chs_url else 'None'

        return {
            "en": {
                "video_url": str(largest_en['progressiveDownloadURL']),
                "subtitles_url": str(subtitles_en),
                "title": str(media_data_en['title'])
            },
            "chs": {
                "video_url": str(largest_chs['progressiveDownloadURL']),
                "subtitles_url": str(subtitles_chs),
                "title": str(media_data_chs['title'])
            }
        }
    else:
        raise Exception("Failed to retrieve download links for one or both languages.")

def convert_and_combine_videos(video_info):
    """
    Combine English and Chinese videos and subtitles into a single file.
    """
    try:
        # Download English video
        video_en = download_video(video_info['en']['video_url'])
        #audio_en = convert_to_audio_in_memory(video_en)
        print("Downloaded English" + video_info['en']['video_url'])
        # Download Chinese video
        video_chs = download_video(video_info['chs']['video_url'])
        #audio_chs = convert_to_audio_in_memory(video_chs)
        print("Downloaded Chinese" + video_info['chs']['video_url'])

        if video_en is None or video_chs is None:
            raise ValueError("Video streams cannot be None")

        print(video_info)
        # Download subtitles (if available)
        subtitles_en = download_subtitles(video_info['en']['subtitles_url']) if video_info['en']['subtitles_url'] != 'None' else None
        subtitles_chs = download_subtitles(video_info['chs']['subtitles_url']) if video_info['chs']['subtitles_url'] != 'None' else None

        if subtitles_en is not None:
            print("Adding English subtitles")
        if subtitles_chs is not None:
            print("Adding Chinese subtitles")

        print("English video size:", video_en.getbuffer().nbytes)
        print("Chinese video size:", video_chs.getbuffer().nbytes)

        if subtitles_en:
            print("English subtitles size:", subtitles_en.getbuffer().nbytes)
        if subtitles_chs:
            print("Chinese subtitles size:", subtitles_chs.getbuffer().nbytes)

        # Combine the audio and subtitles using ffmpeg
        combined_file = combine_audio_subtitles(video_en, video_chs, subtitles_en, subtitles_chs)

        return combined_file
    except Exception as e:
        print(f"Error during conversion: {e}")
        return None
    
def combine_audio_subtitles(video_en, video_chs, subtitles_en=None, subtitles_chs=None):
    """
    Combine English and Chinese audio streams and subtitles into a single MKV file.
    """
    output_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mkv")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video_en, \
         tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video_chs:
        
        temp_files = [temp_video_en.name, temp_video_chs.name]

        # Write video streams to temporary files
        temp_video_en.write(video_en.getvalue())
        temp_video_chs.write(video_chs.getvalue())

        command = [
            'ffmpeg', '-y',  # Overwrite output file if exists
            '-i', temp_video_en.name,  # English video input (temp file)
            '-i', temp_video_chs.name  # Chinese video input (temp file)
        ]

        # Add subtitles input commands
        if subtitles_en:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".vtt") as temp_subtitles_en:
                temp_subtitles_en.write(subtitles_en.getvalue())
                temp_files.append(temp_subtitles_en.name)
                command.extend(['-i', temp_subtitles_en.name])  # English subtitle input

        if subtitles_chs:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".vtt") as temp_subtitles_chs:
                temp_subtitles_chs.write(subtitles_chs.getvalue())
                temp_files.append(temp_subtitles_chs.name)
                command.extend(['-i', temp_subtitles_chs.name])  # Chinese subtitle input

        # Map the audio and video streams
        command.extend([
            '-map_metadata', '0',
            '-c:v', 'copy', '-c:a', 'copy',  # Copy video and audio codecs, no transcoding
            '-map', '0:v:0',  # Map the video stream from the primary file
            '-map', '0:a:0',  # Map the audio stream from the primary file
            '-map', '1:a:0',  # Map Chinese audio
        ])

        # Add metadata for subtitle streams
        if subtitles_en:
            command.extend(['-map', '2:s:0', '-c:s:v:0', 'srt'])  # Metadata for English subtitles
        if subtitles_chs:
            command.extend(['-map', '3:s:0', '-c:s:v:1', 'srt'])  # Metadata for Chinese subtitles

        # Add metadata for audio streams
        command.extend([
            '-metadata:s:a:0', 'title=English', '-metadata:s:a:0', 'language=eng',  # Metadata for English audio
            '-metadata:s:a:1', 'title=Chinese', '-metadata:s:a:1', 'language=chi',  # Metadata for Chinese audio
        ])

        # Add metadata for subtitle streams
        if subtitles_en:
            command.extend(['-metadata:s:s:0', 'language=eng', '-metadata:s:s:0', 'title=English'])  # Metadata for English subtitles
        if subtitles_chs:
            command.extend(['-metadata:s:s:1', 'language=chi', '-metadata:s:s:1', 'title=Chinese'])  # Metadata for Chinese subtitles

        command.extend(['-f', 'matroska', output_file.name])  # Output as MKV format to temporary file

        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        try:
            # Collect output and handle progress
            while True:
                stderr_output = process.stderr.readline()
                if stderr_output == '' and process.poll() is not None:
                    break
                if stderr_output:
                    pass

            output, err = process.communicate()

            if process.returncode != 0:
                print(f"FFmpeg error: {err}")
                raise subprocess.CalledProcessError(process.returncode, command, output=output, stderr=err)

            print("ready")
            return output_file.name

        finally:
            # Clean up the temporary files
            for temp_file in temp_files:
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
    
def download_subtitles(subtitle_url):
    if not subtitle_url:
        return None
    """
    Download subtitles and return the file path.
    """
    response = requests.get(subtitle_url, stream=True)
    subtitle_stream = BytesIO()

    if response.status_code == 200:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                subtitle_stream.write(chunk)
        subtitle_stream.seek(0)  # Reset the stream position
        return subtitle_stream
    else:
        raise Exception(f"Failed to download subtitles from {subtitle_url}")

@app.route('/convert')
def convert_video():
    try:
        # Step 1: Fetch JSON with video links
        video_data = {
                "videos": [
                    {
                        "name": "example_video_1",
                        "url": "https://download-a.akamaihd.net/files/content_assets/c6/502018809_E_cnt_1_r240P.mp4"
                    },
                    {
                        "name": "example_video_2",
                        "url": "https://download-a.akamaihd.net/files/content_assets/c6/502018809_E_cnt_1_r240P.mp4"
                    }
                ]
            }

        audio_streams = []

        for video in video_data['videos']:
            video_url = video['url']
            # Step 2: Download the video
            video_stream  = download_video(video_url)
            
            # Step 3: Convert the video to audio
            audio_stream  = convert_to_audio_in_memory(video_stream)

            audio_streams.append(audio_stream)

        # Step 4: Create a ZIP file containing all the converted audio files
        zip_name = 'converted_audio_files.zip'
        zip_stream = create_zip_in_memory(audio_streams)

        # Step 5: Upload the ZIP file to Azure Blob Storage
        zip_blob_url = upload_to_blob_from_file(zip_stream, zip_name)

        return jsonify({"status": "success", "download_url": zip_blob_url})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/download', methods=['POST'])
def download_selected_videos():
    print(request)
      # Get the selected videos from the form data
    selected_videos_json = request.form.get('selected_videos', '[]')
    selected_videos = json.loads(selected_videos_json)
    
    combined_files = []

    # Fetch, combine, and zip videos
    for video in selected_videos:
        try:
            language_agnostic_natural_key = video['data']['languageAgnosticNaturalKey']
            video_info = fetch_download_links(language_agnostic_natural_key)

            print(video_info)

            # Convert and combine video, audio, and subtitles
            output = convert_and_combine_videos(video_info)
            if output:
                combined_files.append(output)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    # Zip the combined streams
    zip_stream = create_zip_in_memory(combined_files)

    # Upload ZIP to Azure Blob Storage
    zip_blob_name = 'combined_audio_subtitles.zip'
    try:
        zip_blob_url = upload_to_blob_from_file(zip_stream, zip_blob_name)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    # Redirect to download page
    return redirect(url_for('download_page', download_url=zip_blob_url))

# Function to render a download page with the download URL
@app.route('/download_page')
def download_page():
    download_url = request.args.get('download_url')
    if download_url:
        return render_template('download.html', download_url=download_url)
    else:
        return "No download URL found.", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)