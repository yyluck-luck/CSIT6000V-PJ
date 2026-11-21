import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
hidden = z.read('ppt/slideMasters/hidden')
hidden_clean = hidden.replace(b' ', b'')
padded = hidden_clean + b'=' * (4 - len(hidden_clean) % 4)
decoded = base64.b64decode(padded)
flag = decoded.decode().split('flag: ')[1]
print(flag)