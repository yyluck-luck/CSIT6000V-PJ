import gzip, re
f = gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb')
prev = b''
while True:
    chunk = f.read(1024*1024)
    if not chunk: break
    data = prev + chunk
    matches = re.findall(b'picoCTF\{[^}]*\}', data)
    if matches:
        for m in matches:
            print(m.decode())
    prev = data[-100:]
f.close()