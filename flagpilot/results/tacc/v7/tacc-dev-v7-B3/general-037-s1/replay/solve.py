import zipfile, os, io
z = zipfile.ZipFile('./challenge/files.zip')
for name in z.namelist():
    if name.endswith('uber-secret.txt'):
        print(z.read(name).decode().strip())
        break