import base64, re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP section and extract the base64-encoded license URL
# The pattern is: <cc:license rdf:resource='BASE64STRING'/>
match = re.search(rb"<cc:license rdf:resource='([^']+)'/>", data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for base64 strings that decode to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/=]{20,}", data):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except Exception:
            pass