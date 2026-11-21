import base64
s = 'bDNhcm5fdGgzX3IwcDM1'
d = base64.b64decode(s).decode()
print(f'picoCTF{{{d}}}')