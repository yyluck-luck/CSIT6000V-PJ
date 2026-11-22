with open('./challenge/garden.jpg', 'rb') as f:
    data = f.read()
idx = data.rfind(b'\xff\xd9')
flag_data = data[idx+2:].decode('ascii', errors='replace')
import re
m = re.search(r'picoCTF\{[^}]+\}', flag_data)
print(m.group(0))