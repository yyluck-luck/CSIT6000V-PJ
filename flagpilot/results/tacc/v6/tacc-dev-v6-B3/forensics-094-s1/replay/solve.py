import re
with open('./challenge/garden.jpg','rb') as f:
    data=f.read()
m=re.search(rb'picoCTF\{[^}]+\}', data)
print(m.group(0).decode())