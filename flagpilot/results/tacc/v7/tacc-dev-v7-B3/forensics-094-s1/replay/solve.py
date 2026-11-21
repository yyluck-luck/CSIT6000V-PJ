with open('./challenge/garden.jpg', 'rb') as f:
    data = f.read()

end_marker = b'\xff\xd9'
idx = data.rfind(end_marker)
if idx != -1:
    hidden = data[idx+2:]
    text = hidden.decode('utf-8').strip()
    import re
    m = re.search(r'picoCTF\{[^}]+\}', text)
    if m:
        print(m.group(0))
