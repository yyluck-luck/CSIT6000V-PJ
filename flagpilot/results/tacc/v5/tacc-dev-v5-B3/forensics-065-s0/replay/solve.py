import gzip

src = './challenge/dds1-alpine.flag.img.gz'
flag_pattern = b'picoCTF{'

with gzip.open(src, 'rb') as f:
    chunk_size = 1024*1024
    prev_tail = b''
    while True:
        chunk = f.read(chunk_size)
        if not chunk:
            break
        data = prev_tail + chunk
        idx = data.find(flag_pattern)
        if idx != -1:
            end = data.find(b'}', idx)
            if end != -1:
                flag = data[idx:end+1].decode('utf-8', errors='replace')
                print(flag)
                break
        prev_tail = data[-100:]