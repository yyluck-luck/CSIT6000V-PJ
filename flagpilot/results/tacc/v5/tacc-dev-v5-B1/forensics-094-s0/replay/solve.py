import sys
data = open('./challenge/garden.jpg', 'rb').read()
# Find the flag in the trailing bytes
idx = data.rfind(b'picoCTF{')
end = data.rfind(b'}')
flag = data[idx:end+1].decode()
print(flag)