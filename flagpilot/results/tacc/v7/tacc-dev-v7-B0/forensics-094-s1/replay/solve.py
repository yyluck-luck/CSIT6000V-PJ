import re
data=open('./challenge/garden.jpg','rb').read()
# find flag after FFD9
i=data.rfind(b'\xff\xd9')
tail=data[i+2:]
m=re.search(rb'picoCTF\{[^}]+\}', tail)
print(m.group(0).decode())