import gzip

src = './challenge/dds1-alpine.flag.img.gz'

with gzip.open(src, 'rb') as f:
    buf = b''
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 1024*1024:
            buf = buf[-1024*1024:]
        idx = buf.find(b'picoCTF')
        if idx != -1:
            # Extract the flag up to the closing brace
            end = buf.find(b'}', idx)
            flag = buf[idx:end+1].decode()
            print(flag)
            break
    else:
        idx = buf.find(b'picoCTF')
        if idx != -1:
            end = buf.find(b'}', idx)
            flag = buf[idx:end+1].decode()
            print(flag)
        else:
            print("Flag not found")