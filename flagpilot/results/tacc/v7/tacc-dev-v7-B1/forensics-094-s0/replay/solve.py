with open('./challenge/garden.jpg','rb') as f:
    data=f.read()
idx=data.rfind(b'\xff\xd9')
print(data[idx:].decode(errors='ignore'))