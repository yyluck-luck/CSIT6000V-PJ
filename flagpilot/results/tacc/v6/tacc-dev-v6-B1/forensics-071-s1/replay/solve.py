import zipfile
import base64
import os

zp = './challenge/Forensics is fun.pptm'

with zipfile.ZipFile(zp, 'r') as z:
    for name in z.namelist():
        if 'hidden' in name.lower():
            data = z.read(name)
            s = data.decode().strip()
            s = s.replace(' ', '')
            # Add padding if needed
            while len(s) % 4 != 0:
                s += '='
            decoded = base64.b64decode(s)
            flag = decoded.decode().split('flag: ')[1].strip()
            print(flag)
            break