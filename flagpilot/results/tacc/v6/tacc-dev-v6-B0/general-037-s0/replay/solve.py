import zipfile

with zipfile.ZipFile('./challenge/files.zip', 'r') as z:
    content = z.read('files/adequate_books/more_books/.secret/deeper_secrets/deepest_secrets/uber-secret.txt')
    print(content.decode('utf-8'))