import zipfile
import os

zip_path = './challenge/files.zip'
extract_dir = './extracted'
os.makedirs(extract_dir, exist_ok=True)

with zipfile.ZipFile(zip_path, 'r') as zip_ref:
    zip_ref.extractall(extract_dir)

# Find the uber-secret.txt file
for root, dirs, files in os.walk(extract_dir):
    for f in files:
        if f == 'uber-secret.txt':
            filepath = os.path.join(root, f)
            with open(filepath, 'r') as fh:
                flag = fh.read().strip()
                print(flag)
                break