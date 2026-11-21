import sys

data = open('./challenge/garden.jpg', 'rb').read()
# Find the JPEG end marker
end_idx = data.rfind(b'\xff\xd9')
# Get everything after the JPEG end
appended = data[end_idx+2:]
print(appended.decode('utf-8', errors='replace'))
