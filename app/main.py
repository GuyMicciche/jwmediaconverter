import requests
import gzip
import io
import json
from flask import Flask, jsonify, send_from_directory

app = Flask(__name__, static_folder='static')

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/search')
def search_titles():
    gz_url = 'https://app.jw-cdn.org/catalogs/media/E.json.gz'

    try:
        json_data = fetch_and_decompress_gz(gz_url)
        titles = [o['title'] for o in json_data if 'title' in o]  # Extract titles from list of objects
        return jsonify({"titles": titles})
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)