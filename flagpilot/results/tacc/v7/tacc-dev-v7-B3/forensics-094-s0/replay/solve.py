with open('./challenge/garden.jpg','rb') as f:
    data = f.read()
import re
m = re.search(rb'picoCTF\{[^}]+\}', data)
print(m.group().decode())