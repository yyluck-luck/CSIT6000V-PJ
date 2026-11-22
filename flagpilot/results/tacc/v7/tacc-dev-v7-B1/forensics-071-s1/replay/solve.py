import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
hidden = z.read('ppt/slideMasters/hidden').replace(b' ', b'')
padded = hidden + b'=' * (4 - len(hidden) % 4)
flag = base64.b64decode(padded).decode()
print(flag)