import re
import base64

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find base64-like strings in the image data
b64_matches = re.findall(rb'[A-Za-z0-9+/]{40,}={0,2}', data)

for m in b64_matches:
    try:
        decoded = base64.b64decode(m).decode()
        if 'picoCTF{' in decoded:
            print(decoded)
            break
    except Exception:
        continue