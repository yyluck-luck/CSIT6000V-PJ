import gzip

with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    buffer = b''
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        buffer += chunk
        idx = buffer.find(b'picoCTF')
        if idx != -1:
            start = max(0, idx - 50)
            end = min(len(buffer), idx + 200)
            print(buffer[start:end])
            break
        buffer = buffer[-100:]
