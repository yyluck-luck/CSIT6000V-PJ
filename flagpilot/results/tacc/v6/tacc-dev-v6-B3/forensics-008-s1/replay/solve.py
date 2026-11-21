import re, base64
with open('./challenge/cat.jpg','rb') as f:
    data = f.read()
# Find the base64-encoded flag in XMP metadata
m = re.search(rb'cc:license rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
flag = base64.b64decode(m.group(1)).decode()
print(flag)