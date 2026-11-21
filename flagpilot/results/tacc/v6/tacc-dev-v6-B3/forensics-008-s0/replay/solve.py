import base64, re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the base64-encoded flag in the EXIF/XMP metadata
# The flag is in the cc:license rdf:resource field
match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    print("Flag not found")