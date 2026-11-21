from math import gcd
from Crypto.Util.number import long_to_bytes

with open('./challenge/public-key.txt') as f:
    lines = f.read().strip().split('\n')

vals = {}
for line in lines:
    k, v = line.split(': ')
    vals[k] = int(v)

n1, n2, n3, e, c = vals['n1'], vals['n2'], vals['n3'], vals['e'], vals['c']

p = gcd(n1, n2)
q = gcd(n1, n3)
r = n2 // p

def decrypt(c, n, p, q):
    phi = (p-1)*(q-1)
    d = pow(e, -1, phi)
    return pow(c, d, n)

# Encryption: c = pow(pow(pow(m, e, n1), e, n2), e, n3)
# Decryption: reverse order: n3, n2, n1
c = decrypt(c, n3, q, r)
c = decrypt(c, n2, p, r)
c = decrypt(c, n1, p, q)

flag = long_to_bytes(c).decode()
print(flag)