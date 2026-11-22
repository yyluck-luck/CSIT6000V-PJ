import sys

with open('./challenge/garden.jpg', 'rb') as f:
    data = f.read()

# Find the flag in the appended data after JPEG
idx = data.find(b'picoCTF{')
if idx != -1:
    end = data.find(b'}', idx) + 1
    flag = data[idx:end].decode()
    print(flag)
else:
    print('Flag not found')
    sys.exit(1)