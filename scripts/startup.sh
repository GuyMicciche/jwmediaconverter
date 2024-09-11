# Install MKVToolNix
apt-get update
apt-get install -y mkvtoolnix
apt-get install -y ffmpeg

gunicorn --chdir app main:app