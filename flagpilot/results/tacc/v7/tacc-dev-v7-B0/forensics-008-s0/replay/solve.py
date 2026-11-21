import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section (starts with ffe1 marker)
# Look for base64-encoded string in the XMP data
# The XMP section contains a cc:license rdf:resource with base64
# Find the base64 string between rdf:resource=' and '/>
match = re.search(rb"rdf:resource='([A-Za-z0-9+/=]+)'/>", data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64 string that decodes to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/]{20,}={0,2}", data):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except Exception:
            pass