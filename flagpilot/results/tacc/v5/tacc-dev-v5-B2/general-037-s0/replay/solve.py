import zipfile, os
with zipfile.ZipFile('./challenge/files.zip', 'r') as z:
    z.extractall('./challenge/extracted')
for root, dirs, files in os.walk('./challenge/extracted'):
    for f in files:
        if f == 'uber-secret.txt':
            print(open(os.path.join(root, f)).read().strip())