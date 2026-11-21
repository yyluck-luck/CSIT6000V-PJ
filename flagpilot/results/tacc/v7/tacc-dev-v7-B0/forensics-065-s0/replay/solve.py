import gzip

input_file = './challenge/dds1-alpine.flag.img.gz'

with gzip.open(input_file, 'rb') as f:
    while True:
        chunk = f.read(1024*1024)
        if not chunk:
            break
        idx = chunk.find(b'picoCTF{')
        if idx != -1:
            end = chunk.find(b'}', idx)
            if end != -1:
                flag = chunk[idx:end+1].decode('utf-8', errors='replace')
                print(flag)
                break