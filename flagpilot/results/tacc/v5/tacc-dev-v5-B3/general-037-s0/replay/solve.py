import zipfile, os
z = zipfile.ZipFile('./challenge/files.zip')
z.extractall('extracted')
for root, dirs, files in os.walk('extracted'):
    for f in files:
        if f == 'uber-secret.txt':
            path = os.path.join(root, f)
            print(open(path).read().strip())