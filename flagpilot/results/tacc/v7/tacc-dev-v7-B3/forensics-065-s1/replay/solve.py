import gzip

with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    chunk_size = 1024 * 1024
    prev_chunk = b''
    while True:
        chunk = f.read(chunk_size)
        if not chunk:
            break
        combined = prev_chunk + chunk
        idx = combined.find(b'picoCTF')
        if idx != -1:
            # Extract the flag from the context
            start = idx
            end = combined.find(b'}', start) + 1
            flag = combined[start:end].decode('utf-8')
            print(flag)
            break
        prev_chunk = chunk[-100:]