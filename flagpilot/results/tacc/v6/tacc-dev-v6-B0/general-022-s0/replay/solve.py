import base64
# The challenge provides the base64-encoded string in the problem description
encoded = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(encoded).decode()
print(f'picoCTF{{{decoded}}}')