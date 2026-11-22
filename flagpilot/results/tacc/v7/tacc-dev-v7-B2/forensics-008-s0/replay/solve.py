import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section
xmp_start = data.find(b'<?xpacket')
xmp_end = data.find(b'</x:xmpmeta>') + len(b'</x:xmpmeta>')
if xmp_start != -1 and xmp_end != -1:
    xmp = data[xmp_start:xmp_end].decode('utf-8', errors='replace')
    # Find the base64-encoded license resource
    match = re.search(r"rdf:resource='([A-Za-z0-9+/=]+)'", xmp)
    if match:
        b64 = match.group(1)
        flag = base64.b64decode(b64).decode()
        print(flag)
    else:
        print("No base64 found in XMP")
else:
    print("XMP not found")