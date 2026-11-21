import re, base64
with open('./challenge/cat.jpg','rb') as f:
    data=f.read()
i=data.find(b'\xff\xe1')
seglen=int.from_bytes(data[i+2:i+4],'big')
seg=data[i+4:i+4+seglen]
m=re.search(rb"cc:license rdf:resource='([^']+)'", seg)
b64=m.group(1).decode()
flag=base64.b64decode(b64).decode()
print(flag)