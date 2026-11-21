import re
import base64

# Read the cat.jpg file
with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Search for base64-encoded strings in the XMP metadata
# Look for the pattern in cc:license rdf:resource
match = re.search(rb"rdf:resource='([A-Za-z0-9+/=]+)'", data)
if match:
    b64_str = match.group(1).decode()
    flag = base64.b64decode(b64_str).decode()
    print(flag)
else:
    # Fallback: search for any base64-like string that decodes to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/]{20,}={0,2}", data):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except:
            pass