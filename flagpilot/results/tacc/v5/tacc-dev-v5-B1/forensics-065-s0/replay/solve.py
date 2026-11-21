import gzip, re
with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    data = f.read()
matches = re.findall(rb'picoCTF\{[^}]+\}', data)
print(matches[0].decode())