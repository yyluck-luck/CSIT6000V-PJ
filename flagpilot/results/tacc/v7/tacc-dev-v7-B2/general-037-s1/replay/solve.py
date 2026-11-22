import zipfile
with zipfile.ZipFile('./challenge/files.zip', 'r') as z:
    z.extractall('./extracted')
with open('./extracted/files/adequate_books/more_books/.secret/deeper_secrets/deepest_secrets/uber-secret.txt') as f:
    print(f.read().strip())