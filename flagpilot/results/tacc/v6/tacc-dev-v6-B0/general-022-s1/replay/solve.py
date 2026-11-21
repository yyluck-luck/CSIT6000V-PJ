import base64

# The base64-encoded string from the challenge description
encoded = 'bDNhcm5fdGgzX3IwcDM1'

# Decode the base64 string
decoded = base64.b64decode(encoded).decode('utf-8')

# Format as flag
flag = f'picoCTF{{{decoded}}}'
print(flag)