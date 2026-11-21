import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
hidden = z.read('ppt/slideMasters/hidden').replace(b' ', b'')
padding = 4 - len(hidden) % 4
if padding != 4:
    hidden += b'=' * padding
flag = base64.b64decode(hidden).decode()
print(flag)