with open('./challenge/garden.jpg', 'rb') as f:
    data = f.read()

eoi_marker = b'\xff\xd9'
last_eoi = data.rfind(eoi_marker)

if last_eoi != -1:
    appended_data = data[last_eoi+2:]
    flag = appended_data.decode('utf-8').strip().split('"')[1]
    print(flag)