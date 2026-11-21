import base64
# The challenge prompt gives the base64 string 'bDNhcm5fdGgzX3IwcDM1'
encoded = 'bDNhcm5fdGgzX3IwcDM1'
decoded = base64.b64decode(encoded).decode()
print(f'picoCTF{{{decoded}}}')