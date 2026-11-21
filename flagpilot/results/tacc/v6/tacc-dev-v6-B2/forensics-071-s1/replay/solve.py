import zipfile, base64
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
hidden = z.read('ppt/slideMasters/hidden').decode().replace(' ', '')
padding = 4 - len(hidden) % 4
if padding != 4:
    hidden += '=' * padding
print(base64.b64decode(hidden).decode().split('flag: ')[1])