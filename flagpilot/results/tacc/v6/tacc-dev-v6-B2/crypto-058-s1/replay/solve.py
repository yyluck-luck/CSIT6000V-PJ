import base64

csr = open('challenge/readmycert.csr').read()
lines = csr.strip().split('\n')
b64_content = ''.join(lines[1:-1])
der = base64.b64decode(b64_content)
text = der.decode('latin-1')
idx = text.find('picoCTF')
end = text.find('}', idx)
flag = text[idx:end+1]
print(flag)