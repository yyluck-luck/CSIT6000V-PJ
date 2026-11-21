import base64
# The base64 string from the challenge description
b64_str = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(b64_str).decode()
print(f'picoCTF{{{decoded}}}')