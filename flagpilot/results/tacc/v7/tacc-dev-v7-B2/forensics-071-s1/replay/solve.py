import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
data = z.read('ppt/slideMasters/hidden').decode().strip()
b64 = data.replace(' ', '')
b64 += '=' * (4 - len(b64) % 4)
print(base64.b64decode(b64).decode())