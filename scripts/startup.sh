# Install MKVToolNix
sudo apt-get update
sudo apt-get install -y mkvtoolnix

gunicorn --chdir app main:app