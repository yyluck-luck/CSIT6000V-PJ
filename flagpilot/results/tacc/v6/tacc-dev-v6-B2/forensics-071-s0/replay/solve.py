import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
data = z.read('ppt/slideMasters/hidden').replace(b' ', b'')
flag = base64.b64decode(data + b'==').decode()
print(flag.split('flag: ')[1])