import gzip

with gzip.open('challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        idx = chunk.find(b'picoCTF')
        if idx != -1:
            flag = chunk[idx:idx+50].split(b'\n')[0].decode()
            print(flag)
            break