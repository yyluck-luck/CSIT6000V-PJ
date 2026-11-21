import sys

data = open('./challenge/garden.jpg', 'rb').read()

# Find the JPEG end marker FFD9
end_idx = data.rfind(b'\xff\xd9')

# Get data after the JPEG end marker
appended = data[end_idx+2:]

# Extract the flag
flag_start = appended.find(b'picoCTF{')
flag_end = appended.find(b'}', flag_start) + 1
flag = appended[flag_start:flag_end].decode()

print(flag)