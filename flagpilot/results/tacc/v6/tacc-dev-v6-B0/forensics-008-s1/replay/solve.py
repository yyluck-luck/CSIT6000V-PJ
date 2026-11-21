import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section and look for base64-encoded strings
# The XMP data is in the APP1 segment (ff e1)
# Look for base64 patterns in the XMP section
xmp_start = data.find(b'<?xpacket')
xmp_end = data.find(b'<?xpacket end')
if xmp_end == -1:
    xmp_end = data.find(b'</x:xmpmeta>')

xmp_data = data[xmp_start:xmp_end].decode('utf-8', errors='ignore')

# Find base64-encoded strings (at least 20 chars of base64)
matches = re.findall(r'[A-Za-z0-9+/]{20,}={0,2}', xmp_data)

for m in matches:
    try:
        decoded = base64.b64decode(m).decode('utf-8')
        if 'picoCTF' in decoded:
            print(decoded)
            break
    except Exception:
        pass