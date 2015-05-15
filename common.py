import requests

def fetch_to_file(url, file):
    with open(file, 'wb') as f:
        r = requests.get(url, stream=True)
        for chunk in r.iter_content(1024):
            f.write(chunk)
