import re
with open('./challenge/garden.jpg','rb') as f:
    data=f.read()
idx=data.rfind(b'\xff\xd9')
trailing=data[idx+2:]
m=re.search(rb'picoCTF\{[^}]*\}', trailing)
print(m.group().decode())