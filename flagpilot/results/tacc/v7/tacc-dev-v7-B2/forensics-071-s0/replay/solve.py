import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
data = z.read('ppt/slideMasters/hidden').replace(b' ', b'')
data += b'=' * (4 - len(data) % 4)
print(base64.b64decode(data).decode())