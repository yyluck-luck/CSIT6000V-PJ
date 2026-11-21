import re, base64
data = open('./challenge/cat.jpg','rb').read()
m = re.search(rb"cc:license rdf:resource='([^']+)'", data)
b64 = m.group(1).decode()
print(base64.b64decode(b64).decode())
