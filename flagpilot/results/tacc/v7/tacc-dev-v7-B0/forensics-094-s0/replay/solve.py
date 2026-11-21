with open('./challenge/garden.jpg', 'rb') as f:
    data = f.read()
idx = data.find(b'picoCTF')
flag = data[idx:idx+40].decode().strip('"\n')
print(flag)