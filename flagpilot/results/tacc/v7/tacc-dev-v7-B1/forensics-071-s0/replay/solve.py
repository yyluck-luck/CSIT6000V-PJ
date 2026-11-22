import base64, zipfile
z = zipfile.ZipFile('./challenge/Forensics is fun.pptm')
data = z.read('ppt/slideMasters/hidden').decode().replace(' ', '')
s = data + '=' * (4 - len(data) % 4)
flag = base64.b64decode(s).decode()
# The flag starts with 'flagos' prefix, actual flag is picoCTF{...}
import re
m = re.search(r'picoCTF\{.*\}', flag)
print(m.group(0))