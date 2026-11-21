import base64
# The challenge provides the base64 string in its description
b64_string = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(b64_string).decode()
print(f'picoCTF{{{decoded}}}')