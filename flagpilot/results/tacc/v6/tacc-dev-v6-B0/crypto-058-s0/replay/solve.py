import base64, re

csr = open('./challenge/readmycert.csr').read()
raw = base64.b64decode(re.search(r'-----BEGIN CERTIFICATE REQUEST-----(.+?)-----END CERTIFICATE REQUEST-----', csr, re.DOTALL).group(1))
flag = re.search(r'picoCTF\{[^}]+\}', raw.decode('latin-1')).group(0)
print(flag)