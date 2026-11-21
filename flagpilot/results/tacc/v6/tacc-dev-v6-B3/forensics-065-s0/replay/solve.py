import gzip, re

src = './challenge/dds1-alpine.flag.img.gz'

with gzip.open(src, 'rb') as f:
    prev_chunk = b''
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        buf = prev_chunk + chunk
        idx = buf.find(b'picoCTF')
        if idx != -1:
            # Extract the flag using regex
            start = max(0, idx - 50)
            end = min(len(buf), idx + 200)
            context = buf[start:end]
            match = re.search(rb'picoCTF\{[^}]+\}', context)
            if match:
                print(match.group().decode())
                break
        prev_chunk = chunk[-100:]
