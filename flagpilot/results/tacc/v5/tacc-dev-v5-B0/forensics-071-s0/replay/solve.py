import base64, zipfile
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
data = z.read('ppt/slideMasters/hidden').decode().strip().replace(' ', '')
data += '=' * (-len(data) % 4)
print(base64.b64decode(data).decode())