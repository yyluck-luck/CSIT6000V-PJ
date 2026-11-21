import gzip

with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    buf = b''
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        buf += chunk
        idx = buf.find(b'picoCTF{')
        if idx != -1:
            end = buf.find(b'}', idx)
            if end != -1:
                flag = buf[idx:end+1].decode('utf-8', errors='replace')
                print(flag)
                break
            else:
                buf = buf[idx:]
        else:
            buf = buf[-100:]
    else:
        idx = buf.find(b'picoCTF{')
        if idx != -1:
            end = buf.find(b'}', idx)
            if end != -1:
                print(buf[idx:end+1].decode('utf-8', errors='replace'))