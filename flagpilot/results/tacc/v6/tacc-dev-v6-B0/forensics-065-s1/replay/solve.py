import gzip

flag_found = None
with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    prev_chunk = b''
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        data = prev_chunk + chunk
        idx = data.find(b'picoCTF')
        if idx != -1:
            flag_start = idx
            flag_end = data.find(b'}', idx)
            if flag_end != -1:
                flag_found = data[flag_start:flag_end+1].decode('utf-8', errors='replace')
                break
        prev_chunk = chunk[-100:]

print(flag_found)