import zipfile
z = zipfile.ZipFile('./challenge/files.zip')
z.extractall('./challenge/extracted')
print(open('./challenge/extracted/files/adequate_books/more_books/.secret/deeper_secrets/deepest_secrets/uber-secret.txt').read().strip())