import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section
xmp_start = data.find(b'<?xpacket')
xmp_end = data.find(b'<?xpacket end')
if xmp_start == -1:
    xmp_end = len(data)
else:
    xmp_end = data.find(b'?>', xmp_end) + 2

xmp_data = data[xmp_start:xmp_end].decode('utf-8', errors='replace')

# Find the base64-encoded license resource
match = re.search(r'rdf:resource=\'([A-Za-z0-9+/=]+)\'', xmp_data)
if match:
    b64 = match.group(1)
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for base64-like strings in the file
    for m in re.finditer(r'[A-Za-z0-9+/]{20,}={0,2}', data.decode('latin-1')):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except:
            pass