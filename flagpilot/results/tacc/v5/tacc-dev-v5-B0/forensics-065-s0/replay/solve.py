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
            start = max(0, idx - 50)
            end = min(len(data), idx + 200)
            print(f"Context: {data[start:end]}")
        prev_tail = chunk[-100:]
