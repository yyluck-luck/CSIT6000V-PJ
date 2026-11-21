with open('./challenge/garden.jpg', 'rb') as f:
    data = f.read()
ffd9_pos = data.rfind(b'\xff\xd9')
flag_data = data[ffd9_pos+2:].decode().strip()
import re
m = re.search(r'picoCTF\{[^}]+\}', flag_data)
print(m.group(0))