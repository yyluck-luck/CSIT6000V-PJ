import base64
import re

with open('./challenge/readmycert.csr', 'r') as f:
    content = f.read()

b64 = re.search(r'-----BEGIN CERTIFICATE REQUEST-----(.*?)-----END CERTIFICATE REQUEST-----', content, re.DOTALL).group(1).strip()
data = base64.b64decode(b64)

# Find the CN (Common Name) in the ASN.1 structure
# CN OID is 55 04 03 (2.5.4.3)
# Search for the pattern: 0x04 <length> <string> after the CN OID
# The subject contains: 0x31 0x26 0x30 0x24 0x06 0x03 0x55 0x04 0x03 0x0C <len> <CN>
# Let's find the CN OID and extract the following string

# Find all occurrences of the CN OID (55 04 03)
oid = bytes([0x55, 0x04, 0x03])
idx = data.find(oid)
if idx != -1:
    # After the OID, we expect: 0x0C <length> <string>
    pos = idx + 3
    if data[pos] == 0x0C:
        length = data[pos + 1]
        cn = data[pos + 2:pos + 2 + length].decode('utf-8')
        print(cn)