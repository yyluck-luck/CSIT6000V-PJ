import base64, zipfile
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
s = z.read('ppt/slideMasters/hidden').decode().replace(' ','')
s += '=' * (-len(s) % 4)
print(base64.b64decode(s).decode().split('flag: ')[1].strip())