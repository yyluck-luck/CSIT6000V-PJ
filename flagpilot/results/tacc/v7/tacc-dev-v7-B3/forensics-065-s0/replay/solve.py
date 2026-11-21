import gzip

with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    chunk_size = 1024*1024
    prev_tail = b''
    while True:
        chunk = f.read(chunk_size)
        if not chunk:
            break
        data = prev_tail + chunk
        idx = data.find(b'picoCTF')
        if idx != -1:
            # Extract the flag - find the closing brace
            end = data.find(b'}', idx)
            flag = data[idx:end+1].decode('utf-8')
            print(flag)
            break
        prev_tail = chunk[-100:]
