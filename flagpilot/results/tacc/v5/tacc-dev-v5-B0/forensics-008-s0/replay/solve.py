import re, base64

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section and look for base64-encoded strings
# The XMP data is in the APP1 segment (0xFFE1)
# Look for the cc:license rdf:resource attribute which contains base64
match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64-looking string in the file
    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):
        try:
            decoded = base64.b64decode(m.group(0)).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except Exception:
            pass