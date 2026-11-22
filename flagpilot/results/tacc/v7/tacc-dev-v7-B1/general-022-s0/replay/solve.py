import base64
s = 'bDNhcm5fdGgzX3IwcDM1'
print('picoCTF{' + base64.b64decode(s).decode() + '}')