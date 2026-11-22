import zipfile
z = zipfile.ZipFile('./challenge/files.zip')
content = z.read('files/adequate_books/more_books/.secret/deeper_secrets/deepest_secrets/uber-secret.txt')
print(content.decode())
