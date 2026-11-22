import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP block in the EXIF segment
# Look for the license rdf:resource attribute which contains base64
match = re.search(rb'cc:license rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    encoded = match.group(1).decode('utf-8')
    flag = base64.b64decode(encoded).decode('utf-8')
    print(flag)
else:
    # Fallback: search for base64-like strings in XMP
    xmp_match = re.search(rb'<x:xmpmeta.*?</x:xmpmeta>', data, re.DOTALL)
    if xmp_match:
        xmp = xmp_match.group(0)
        b64_match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', xmp)
        if b64_match:
            encoded = b64_match.group(1).decode('utf-8')
            flag = base64.b64decode(encoded).decode('utf-8')
            print(flag)