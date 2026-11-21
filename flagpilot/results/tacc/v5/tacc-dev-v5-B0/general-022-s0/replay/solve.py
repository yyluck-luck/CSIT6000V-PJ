import base64
# The challenge provides the base64 string in the prompt
b64_str = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(b64_str).decode()
print(f'picoCTF{{{decoded}}}')