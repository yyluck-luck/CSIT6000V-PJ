import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP section and look for base64-encoded string in cc:license rdf:resource
# The pattern is: rdf:resource='BASE64STRING'/>
match = re.search(rb"rdf:resource='([A-Za-z0-9+/=]+)'/>", data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64-looking string that decodes to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/]{20,}={0,2}", data):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except Exception:
            pass