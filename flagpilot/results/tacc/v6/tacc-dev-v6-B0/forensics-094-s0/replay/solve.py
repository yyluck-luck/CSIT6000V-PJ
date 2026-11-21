import sys

data = open('./challenge/garden.jpg', 'rb').read()
# Find JPEG EOI marker
idx = data.rfind(b'\xff\xd9')
appended = data[idx+2:]
print(appended.decode('utf-8', errors='replace'))
