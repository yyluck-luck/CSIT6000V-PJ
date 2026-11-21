import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
data = z.read('ppt/slideMasters/hidden')
clean = data.replace(b' ', b'')
padded = clean + b'=' * (4 - len(clean) % 4) if len(clean) % 4 else clean
decoded = base64.b64decode(padded)
flag = decoded.decode().split('flag: ')[1]
print(flag)