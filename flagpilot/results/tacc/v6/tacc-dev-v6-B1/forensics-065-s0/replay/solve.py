import gzip

with gzip.open('./challenge/dds1-alpine.flag.img.gz', 'rb') as f:
    prev_chunk = b''
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        data = prev_chunk + chunk
        idx = data.find(b'picoCTF')
        if idx != -1:
            print(data[idx:idx+100])
        prev_chunk = chunk[-100:]
print("Done scanning")