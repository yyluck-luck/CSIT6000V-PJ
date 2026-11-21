import zipfile
import base64

# Open the PPTM file (which is a ZIP archive)
with zipfile.ZipFile('./challenge/Forensics is fun.pptm', 'r') as z:
    # Read the hidden file
    with z.open('ppt/slideMasters/hidden') as f:
        data = f.read().decode('utf-8').strip()

# Remove spaces from the base64 string
data_nospace = data.replace(" ", "")

# Add padding if needed
padding = 4 - len(data_nospace) % 4
if padding != 4:
    data_nospace += "=" * padding

# Decode base64
decoded = base64.b64decode(data_nospace).decode('utf-8')

# Extract the flag (remove 'flag: ' prefix)
flag = decoded.replace('flag: ', '')
print(flag)